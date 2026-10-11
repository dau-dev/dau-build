# Architecture: how dau-build composes work from Hydra config

This page explains why dau-build is built from declarative specs, typed
`ccflow` models and a Hydra config tree, and what that structure gives you.
It is background reading. For the commands themselves, see the
[command reference](../reference/commands.md); for step-by-step goals, the
[how-to guides](../how-to/run-a-build.md).

## The shape of the tool

dau-build turns a declarative description of an FPGA build into concrete
artifacts: generated SystemVerilog, artifact bundles, backend handoff
manifests, Tcl scripts and command plans. Everything the tool does is a typed
`ccflow.CallableModel` (a `SimulateTask`, a `SynthesizeTask`, a
`BuildVivadoArtifactsTask`, and so on), and the models a user runs are
composed from configuration rather than constructed by hand in Python.

That configuration is a Hydra config tree under `dau_build/config`. Running
the tool is always the same two-phase act: compose a config from groups and
overrides, then instantiate and run the model it describes. The CLI is a thin
front end over that one idea.

The reason is that an FPGA build has many axes that vary independently (which
task, which board, which backend, which spec), and those axes recur across
dozens of operations. Encoding each axis as a Hydra config group lets any
combination compose without a separate argument parser per command, and lets
a downstream package add new options without editing dau-build.

## Config groups are directories

Each subdirectory of `dau_build/config` is a Hydra config group: `task`,
`step`, `spec`, `board`, `backend`, `platform`, `design`, `callable`.
Selecting an option is an override `<group>=<option>`, where the option is the
file's path relative to the group directory. A file at
`config/task/tasks/sim/simulate.yaml` is selected as `task=tasks/sim/simulate`.
The names are paths because the groups nest. Short aliases are not supported,
so a name always tells you where its file is.

Each option file opens with a `# @package <key>` directive that decides where
its content lands in the composed config. Tasks declare
`# @package model`, so a selected task becomes the `model` that runs. Boards,
backends, specs and platforms declare their own singular key. The base config
`config/base.yaml` lists every group as `optional … null`, so nothing is
selected until you override it, and a run picks `task=` to fill `model`. The [config group reference](../reference/config-groups.md)
has the full list.

Because groups are directories and options are files, the config tree is the
catalog. Adding a task is adding a file; the name-to-class registry is derived
by globbing the tree (`_model_types_from_config_group`), so there is no second
place to register it.

## The override syntax

A single `dau-build` command takes three kinds of override. `task=<path>`
selects the runnable and composes it into the `model` key.
`<group>=<option>` selects a config group: `spec=specs/identity`,
`board=boards/example/probe`, `backend=backends/vivado`. And
`model.<field>=value` sets a field on the selected model. The `model.` prefix
is there because the task is composed into the `model` key, so its fields are
addressed under `model.`.

`--explain` composes the overrides without running and prints the resolved
config, so you can see what they produced.

## ccflow owns ordering and caching

A composed `model` is run by ccflow, not by dau-build directly. The `callable`
group wires a `MultiEvaluator` of a `GraphEvaluator` and a
`MemoryCacheEvaluator`. Composite tasks declare their prerequisites through
`Flow.deps`, the `GraphEvaluator` walks that dependency graph so the pieces
run in order, and the `MemoryCacheEvaluator` keeps a shared dependency from
executing twice in one process. This is why `build-vivado-artifacts` can
depend on staging and validation without dau-build hand-coding the sequence:
the ordering is in the model graph and the evaluator owns it.

## The search path makes the tree extensible

dau-build registers its own config tree on the Hydra search path through a
`hydra.lernaplugins` entry point (`pkg:dau_build.config`). Any installed
package can do the same for its own `pkg:<name>.config`, and its groups then
compose alongside the packaged ones: new boards, backends, designs or tasks
without touching dau-build.

The lerna search-path bridge (`lerna` on PyPI) is what honors the entry point.
Without it the entry point does nothing, so a package that relies on
cross-package composition depends on `lerna` directly. The `dau` package is
the working example: it registers `pkg:dau.config`, which adds `task=shell`,
and with `dau` installed `dau-build task=shell` resolves that task with no
`--config-dir` overlay. dau-build never imports `dau`. Extension flows one
way, through the search path, so dau-build stays free of downstream
dependencies. [Extending dau-build](../how-to/extend-dau-build.md) shows how
to do this yourself.

## Plans first, execution on the host

Most build and hardware tasks default to `execute: false` and emit plans:
generated Tcl, backend manifests at `build_status=planned`, staged work
directories and ordered command sequences, all without invoking a vendor
toolchain or touching hardware. Passing `execute=true` runs the privileged
action.

The split lets the whole pipeline be composed, inspected, validated and
tested on a developer machine with no Xilinx tools and no board attached. The
steps that are privileged and hard to undo (running Vivado, programming over
JTAG, rescanning PCIe) happen only on explicit opt-in, on the machine that has
the hardware. The validators (`validate-vivado-artifacts`) check that a plan
agrees with itself (manifest, Tcl, command plan and output paths) before
anyone spends a synthesis run on it.

## Board, platform and backend are three separate things

These three groups are easy to confuse. They answer different questions.

- **`platform`** (`PlatformDefinition`) is the hardware board as data: the
  part number, the resource budget, the memory, and the host link including
  the full XDMA personality: the complete set of user-set XCI parameters a
  board's bring-up proved, quoted verbatim and in order, so the generated
  Vivado `CONFIG.*` block matches the known-good core byte for byte. A
  hand-picked subset of those parameters leaves the device memory-dead on
  hardware, which is why the definition insists on all of them. `fits()`
  checks a design's resource use against the budget.
- **`board`** (`BoardConfig`) is a small build-config view: `name`,
  `platform`, `shell`. Here `platform` and `shell` are backend labels
  (`vivado-xdma`, `xdma-ddr`) threaded into Vivado manifests, not the
  hardware `PlatformDefinition`.
- **`backend`** (`BackendConfig`) is the synthesis toolchain, as a label.

They are independent groups, composed into a task's `ResolvedBuildConfig`
only where a task needs them. Keeping the hardware facts (`platform`) apart
from the backend labels (`board`, `backend`) means the same physical dpv1
definition serves whichever backend produced a bitstream.

## Backends: Vivado and yosys

Two synthesis engines are implemented: Vivado for the FPGA bitstream flow and
yosys for open-source synthesis. The engine is the `backend` config group.
Each option instantiates a polymorphic `SynthesisEngine` model, and
`SynthesizeTask` delegates to `engine.synthesize(...)`. There is no engine
`Literal` and no `if engine ==` branch; adding an engine is adding a model and
a yaml file.

- **`backend=backends/vivado`** (the default) composes a `VivadoEngine`,
  which writes a Vivado handoff. `vivado_backend.py` generates text artifacts
  (overlay Tcl, build Tcl, a key=value manifest, a command plan) and never
  spawns Vivado; that happens later in the `execute=true` build tasks and in
  `hardware_plan.py`. Vivado is not present in CI, so this path is plan-only
  there.
- **`backend=backends/yosys`** composes a `YosysEngine`, which runs the
  synthesis. `yosys_backend.py` generates a yosys script and executes it, so
  the generated top is elaborated and synthesized for real. yosys is open
  source and installs in CI, which turns synthesis into something the test
  suite runs on every push.

Because the engine is a composed model, its fields are Hydra overrides. The
`YosysEngine` SystemVerilog frontend is one of them:
`backend=backends/yosys backend.frontend=slang` (or `+backend.<field>=...`).
`frontend=verilog` uses yosys's built-in `read_verilog -sv`, enough for
dau-build's own synthesizable sources. `frontend=slang` uses the yosys-slang
plugin's `read_slang`, the same slang engine as the project's `pyslang`
parser, for the full SystemVerilog surface (packages, interfaces) the dau-core
tiles use.

Only `name` and `invocation` reach the resolved-config view
(`ResolvedBuildConfig` normalizes the engine to that label), so an engine's
own fields never leak into build-config reporting. A third engine (a nextpnr
place-and-route flow, another vendor) is a new `SynthesisEngine` subclass and
a yaml file. The config-group plumbing, the open registry, the `_target_`
indirection and the search-path extension are all engine-agnostic, so it can
come from another package without touching dau-build.
[Extending dau-build](../how-to/extend-dau-build.md) lists what a new engine
needs, with yosys as the worked example.
