# Architecture: how dau-build composes work from Hydra config

This page explains *why* dau-build is built the way it is — declarative specs,
typed `ccflow` models, and a Hydra config tree — and what that structure buys
you. It is background reading. For the commands themselves, see the
[command reference](../reference/commands.md); for step-by-step goals, the
[how-to guides](../how-to/run-a-build.md).

## The shape of the tool

dau-build turns a declarative description of an FPGA build into concrete
artifacts: generated SystemVerilog, artifact bundles, backend handoff manifests,
Tcl scripts, and command plans. Everything the tool does is expressed as a typed
`ccflow.CallableModel` — a `SimulateTask`, a `SynthesizeTask`, a
`BuildVivadoArtifactsTask`, and so on — and every one of those models is *built
from configuration*, not hand-constructed in Python.

That configuration is a Hydra config tree under `dau_build/config`. Running the
tool is therefore always the same two-phase act: **compose** a config from
groups and overrides, then **instantiate and run** the model it describes. The
CLIs are thin front-ends over that one idea.

The reason for this design is that an FPGA build has many axes that vary
independently — which task, which board, which backend, which spec — and those
axes are reused across dozens of operations. Encoding each axis as a Hydra config
group lets any combination compose without a bespoke argument parser per command,
and lets a downstream package add new options without editing dau-build.

## Config groups are directories

Each subdirectory of `dau_build/config` is a Hydra config group: `task`, `step`,
`spec`, `board`, `backend`, `platform`, `design`, `callable`. Selecting an option
is an override `<group>=<option>`, where the option is the file's path relative to
the group directory. A file at `config/task/tasks/sim/simulate.yaml` is selected
as `task=tasks/sim/simulate`. The names are path-style because the groups nest;
short aliases are deliberately not supported, so a name always says where its
file lives.

Each option file opens with a `# @package <key>` directive that decides where its
content lands in the composed config. Tasks and steps declare `# @package model`,
so a selected task *becomes* the `model` that gets run. Boards, backends, specs,
and platforms declare their own singular key. The base config
`config/base.yaml` lists every group as `optional … null`, so nothing is selected
until you override it, and a run picks exactly one of `task=` or `step=` to fill
`model`. See the [config group reference](../reference/config-groups.md) for the
full list.

Because groups are just directories and options are just files, the config tree
*is* the catalog. Adding a task is adding a file; the name→class registry is
derived by globbing the tree (`_model_types_from_config_group`), so there is no
second place to register it.

## The override syntax

`dau-build` composes the config tree and runs the result, so a single command
carries three kinds of override. `task=<path>` (or `step=<path>`) selects the
runnable and composes it *into* the `model` key. `<group>=<option>` selects a
config group — `spec=specs/identity`, `board=boards/dau/dpv1`,
`backend=backends/vivado`. And `model.<field>=value` sets a field on the selected
model. The `model.` prefix is not decoration: because the task is composed into
the `model` key, its fields are addressed under `model.`.

Names are path-style (`tasks/sim/simulate`, `backend=backends/yosys`) because
they *are* paths into the config tree — the same reason adding a task is adding a
file. Passing `--explain` composes the overrides without running, printing the
resolved config so you can see exactly what they produced.

## ccflow owns ordering and caching

A composed `model` is run by ccflow, not by dau-build directly. The `callable`
group wires a `MultiEvaluator` of a `GraphEvaluator` and a `MemoryCacheEvaluator`.
Composite tasks declare their prerequisites through `Flow.deps`, and the
`GraphEvaluator` walks that dependency graph so the pieces run in the right order;
the `MemoryCacheEvaluator` prevents a shared dependency from executing twice in a
single process. This is why, for example, `build-vivado-artifacts` can depend on
staging and validation without dau-build hand-coding the sequence — the ordering
lives in the model graph, and the evaluator owns it.

## The search path makes the tree extensible

dau-build registers its own config tree on the Hydra search path through a
`hydra.lernaplugins` entry point (`pkg:dau_build.config`). Any installed package
can do the same for its own `pkg:<name>.config`, and its groups then compose
uniformly alongside the packaged ones — new boards, backends, designs, or tasks
without touching dau-build.

The mechanism is honored by the lerna search-path bridge (`lerna` on PyPI); the
entry point is inert without it, so a package that relies on cross-package
composition depends on `lerna` directly. The private `dau` package is the working
example: it registers `pkg:dau.config`, which adds `task=dpv1-shell`, and with
`dau` installed `dau-build task=dpv1-shell` resolves that task with no
`--config-dir` overlay. dau-build never imports `dau` — extension flows one way,
through the search path, which keeps the public tool free of private
dependencies. See [Extending dau-build](../how-to/extend-dau-build.md) for how to
do this yourself.

## Plans first, execution on the host

Most build and hardware tasks default to `execute: false` and emit *plans*:
generated Tcl, backend manifests at `build_status=planned`, staged work
directories, and ordered command sequences — all without invoking a vendor
toolchain or touching hardware. Passing `execute=true` runs the privileged action.

This separation is deliberate. It lets the whole pipeline be composed, inspected,
validated, and tested on a developer machine with no Xilinx tools and no board
attached, and confines the privileged, irreversible steps (running Vivado,
programming JTAG, rescanning PCIe) to an explicit opt-in on the machine that
actually has the hardware. The validators (`validate-vivado-artifacts`) check that
a plan is internally consistent — manifest, Tcl, command plan, and output paths
all agree — before anyone spends an hour of synthesis on it.

## Board, platform, and backend are three separate things

These three groups are easy to conflate; they answer different questions.

- **`platform`** (`PlatformDefinition`) is the hardware board as data: the part
  number, the resource budget, the memory, and the host link — including the
  full XDMA personality. For dpv1 the personality is the 47 proven bring-up XCI
  parameters, quoted verbatim and order-preserved so the generated Vivado
  `CONFIG.*` block is byte-for-byte identical to the known-good core. A hand-picked
  subset of those parameters leaves the device memory-dead on hardware, which is
  why the definition insists on carrying all of them exactly. `fits()` checks a
  design's resource use against this budget.
- **`board`** (`BoardConfig`) is a small build-config view — `name`, `platform`,
  `shell` — where `platform` and `shell` here are backend *labels* (e.g.
  `vivado-xdma`, `xdma-ddr`) threaded into Vivado manifests, not the hardware
  `PlatformDefinition`.
- **`backend`** (`BackendConfig`) is the synthesis toolchain, as a label.

They are independent groups, composed together into a task's
`ResolvedBuildConfig` only where a task needs them. Keeping the hardware truth
(`platform`) separate from the backend labels (`board`, `backend`) means the same
physical dpv1 definition can be reused regardless of which backend produced a
bitstream.

## Backends: Vivado and yosys

Two synthesis engines are real: **Vivado** (the FPGA bitstream flow) and
**yosys** (open-source synthesis). The engine is chosen the hydra-first way —
it *is* the `backend` config group. Each option instantiates a polymorphic
`SynthesisEngine` model, and `SynthesizeTask` just delegates to
`engine.synthesize(...)`. There is no engine `Literal` and no `if engine ==`
dispatch branch; adding an engine is adding a model and a yaml.

- **`backend=backends/vivado`** (the default) composes a `VivadoEngine`, which
  writes a Vivado *handoff* — `vivado_backend.py` generates text artifacts
  (overlay Tcl, build Tcl, a key=value manifest, a command plan) but never
  spawns Vivado; that happens later in the `execute=true` build tasks and in
  `hardware_plan.py`. Vivado is not present in CI, so this path is plan-only there.
- **`backend=backends/yosys`** composes a `YosysEngine`, which *runs* synthesis.
  `yosys_backend.py` generates a yosys script and executes it, so the generated
  top is actually elaborated and synthesized — a real check, not a plan. yosys is
  open-source and installs in CI, which is the point: it turns synthesis into
  something the test suite can exercise on every run.

Because the engine is a composed model, it is fully hydra-configurable. The
`YosysEngine`'s SystemVerilog frontend is a field, set like any other override:
`backend=backends/yosys backend.frontend=slang` (or `+backend.<field>=...`).
`frontend=verilog` uses yosys's built-in `read_verilog -sv` (enough for
dau-build's own synthesizable sources); `frontend=slang` uses the yosys-slang
plugin's `read_slang`, the same slang engine as the project's `pyslang` parser,
for the full SV surface (packages, interfaces) the private cores use.

The engine model carries only `name`/`invocation` up to the resolved-config view
(`ResolvedBuildConfig` normalizes it to that label), so an engine's own fields
never leak into the build-config reporting. A third engine (a nextpnr
place-and-route flow, another vendor) is a new `SynthesisEngine` subclass and a
yaml; the config-group plumbing, the open registry, the `_target_` indirection,
and the search-path extension are all engine-agnostic, so it can even come from a
package without touching dau-build —
[Extending dau-build](../how-to/extend-dau-build.md) walks through exactly what a
new engine requires, using yosys as the worked example.
