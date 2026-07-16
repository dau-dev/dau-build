# Config group reference

The packaged Hydra config tree lives in `dau_build/config`. Each subdirectory is
a Hydra **config group**; selecting an option from a group is a CLI override of
the form `<group>=<option-path>`. Options are addressed by their path relative to
the group directory, so a file at `config/task/tasks/sim/simulate.yaml` is
selected with `task=tasks/sim/simulate`.

The base config `config/base.yaml` declares the groups and their defaults:

```yaml
defaults:
  - _self_
  - optional task: null
  - optional step: null
  - optional platform: null
  - optional board: null
  - optional backend: null
  - optional driver: null
  - optional memory: null
  - optional simulator: null
  - optional spec: null
  - optional plan: null
  - optional design: null
  - callable: callable
```

Every group except `callable` is `optional … null`: nothing is selected unless
overridden. A run selects exactly one of `task=` or `step=` to populate `model`,
optionally augmented by `spec=`, `board=`, `backend=`, `driver=`, `memory=`, `simulator=`, `platform=`, `host=`, and `plan=`.

Each option file begins with a `# @package <key>` directive that places its
content under that top-level key. Tasks and steps use `# @package model`; the
other groups use their singular key (`# @package board`, etc.).

| Group       | `@package` key | Instantiated model                        | Selects                                                          |
| ----------- | -------------- | ----------------------------------------- | ---------------------------------------------------------------- |
| `task`      | `model`        | a `…Task` `ccflow.CallableModel`          | The unit of work to run.                                         |
| `step`      | `model`        | a `…Step` `ccflow.CallableModel`          | A lower-level plumbing operation.                                |
| `spec`      | `spec`         | `dau_build.build_spec.BuildSpec`          | A composed build spec (Hydra-native alternative to `spec_path`). |
| `board`     | `board`        | `dau_build.build_config.BoardConfig`      | A board's build-config view.                                     |
| `backend`   | `backend`      | a `SynthesisEngine` (e.g. `VivadoEngine`) | The synthesis engine.                                            |
| `driver`    | `driver`       | `dau_build.build_config.DriverConfig`     | The host driver (OS + transport) in the resolved config.         |
| `memory`    | `memory`       | `dau_build.build_config.MemoryConfig`     | The build-time staging buffers in the resolved config.           |
| `simulator` | `simulator`    | a `Simulator` (e.g. `VerilatorSimulator`) | The simulator (used by `SimulateTask`).                          |
| `platform`  | `platform`     | `dau_build.platforms.PlatformDefinition`  | The full physical platform definition.                           |
| `plan`      | `plan`         | a `HardwarePlan` (e.g. `RecoveryPlan`)    | The hardware-session plan (`HardwarePlanTask`).                  |
| `host`      | `host`         | `dau_build.build_config.HostConfig`       | The build host's source checkout roots (none packaged).          |
| `design`    | `design`       | (none packaged)                           | Reserved; registered by extension packages.                      |
| `callable`  | —              | ccflow registry pointer                   | Fixed evaluator wiring; not normally overridden.                 |

## `task`

Packaged options (`task=<path>`):

```text
tasks/build/build-shell-project
tasks/build/build-vivado-artifacts
tasks/build/overlay-build
tasks/build/synthesize
tasks/flash/flash
tasks/flash/smoke-test
tasks/hardware/hardware-plan
tasks/sim/simulate
tasks/spec/build
tasks/spec/inspect
tasks/spec/validate
tasks/stage/stage-shell
tasks/stage/stage-vivado-overlay
tasks/stage/stage-vivado-project
tasks/validate/validate-vivado-artifacts
```

Fields per task are in the [task and step catalog](tasks-and-steps.md).

## `step`

Packaged options (`step=<path>`):

```text
steps/explain
steps/generate
steps/inspect
steps/resolved-config
steps/simulate
steps/synthesis
steps/validate
steps/write
```

Fields per step are in the [task and step catalog](tasks-and-steps.md).

## `spec`

`spec=specs/identity` composes `dau_build.build_spec.BuildSpec` into the `spec`
key. Tasks and steps read it via `spec: ${oc.select:spec,null}`, so a composed
`spec=` is an alternative to passing `model.spec_path=<file>`. The packaged
`specs/identity` option carries `base_dir: examples/identity`, so it resolves
only when run from the dau-build repository root.

## `board`

`board=boards/dau/dpv1` composes `BoardConfig` into the `board` key. Options live
under `boards/<vendor>/<board>`. The packaged `boards/dau/dpv1` option:

```yaml
_target_: dau_build.build_config.BoardConfig
name: dpv1
platform: vivado-xdma
shell: xdma-ddr
```

`BoardConfig` fields: `name` (str), `platform` (str), `shell` (str).

## `backend`

`backend=backends/<name>` composes a synthesis engine into the `backend` key.
`SynthesizeTask` uses it as the engine (default `backends/vivado`); other tasks
use its `name`/`invocation` as the resolved-config backend label.

| Option            | Model           | Fields                                    | Description                           |
| ----------------- | --------------- | ----------------------------------------- | ------------------------------------- |
| `backends/vivado` | `VivadoEngine`  | `name`, `invocation`                      | Vivado handoff (FPGA bitstream flow). |
| `backends/yosys`  | `YosysEngine`   | `name`, `invocation`, `frontend`, `yosys` | Open-source synthesis; runs in CI.    |
| `backends/none`   | `BackendConfig` | `name`, `invocation`                      | No engine — a dry-run label.          |

The engines are polymorphic `SynthesisEngine` models, so they are fully
hydra-configurable — e.g. `backend=backends/yosys backend.frontend=slang` or
`+backend.<field>=...`. `YosysEngine.frontend` is `verilog` (`read_verilog -sv`)
or `slang` (yosys-slang); `yosys` sets the executable. See
[the architecture explanation](../explanation/architecture.md) for how the two
engines differ.

## `driver` and `memory`

`driver=drivers/<name>` and `memory=memories/<name>` compose a `DriverConfig`
(`os`, `transport`) and `MemoryConfig` (`host_staging_bytes`,
`device_staging_bytes`) into the resolved build config, winning over the
spec-derived defaults (`drivers/host` = `host`/`xdma`; `memories/default` =
zeros). They surface in the `resolved-config` step and are the axes a future
board/platform (dpv2) selects. Overridable like any group, e.g.
`memory=memories/default memory.host_staging_bytes=4096`.

## `simulator`

`simulator=simulators/<name>` composes a `Simulator` model into the `simulator`
key; `SimulateTask` uses it as the simulator (default `simulators/svparser`).

| Option                 | Model                | Fields                                                                                                            |
| ---------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `simulators/svparser`  | `SvparserSimulator`  | `name`                                                                                                            |
| `simulators/cocotb`    | `CocotbSimulator`    | `name`, `profile`, `profile_manifest`                                                                             |
| `simulators/verilator` | `VerilatorSimulator` | `name`, `profile`, `profile_manifest`, `testbench_path`, `top_module`, `expect_stdout`, `verilator`, `extra_args` |

Like the engines, simulators are polymorphic and fully hydra-configurable — e.g.
`simulator=simulators/verilator simulator.profile=<name>` or
`+simulator.<field>=...`.

## `platform`

`platform=platforms/dau/dpv1` composes `dau_build.platforms.PlatformDefinition`
into the `platform` key. Options live under `platforms/<vendor>/<board>`. The
packaged `platforms/dau/dpv1` option is the authoritative dpv1 definition: part
number, program method, `ResourceBudget`, `PlatformMemory`, and a `HostLink`
whose `XdmaPersonality.params` are the 47 proven bring-up XCI parameters,
quoted verbatim and order-preserved so the generated Vivado `CONFIG.*` block is
byte-for-byte identical to the known-good core.

`PlatformDefinition` is composed of `ResourceBudget` (`lut`, `ff`, `bram36`,
`dsp`), `PlatformMemory` (`kind`, `size_bytes`, `mig_prj`,
`bandwidth_bytes_per_s`, `constraints_xdc`), and `HostLink` (`interface`,
`pcie_lanes`, `xdma_personality`, `expected_link_width`,
`expected_link_speed_gts`), plus the board-level `constraints_xdc` (pin
constraint text the shell project generators emit behind their banner),
`lane_placements` (the GT lane swizzle, applied as a pre-`opt_design`
implementation hook; empty means the board needs none), and `placeholders`
(names of hardware-derived values not yet measured on the board —
`require_measured` refuses placeholder boards for real builds while
config-only generation stays open). `host_access` (`HostAccess`: `pci_id`,
`endpoint_bdf`, `rescan_bdfs`, `runtime_pm_patterns`,
`runtime_pm_executable`, `jtag_cable`) carries the bench host's measured
access facts; `HardwarePlanTask` composes its toolchain config from it when
`platform=` is selected (explicit task fields override). The shell project requests
(`MmJobShellRequest`, `MmDdrJobShellRequest`) take a `platform` and default
to dpv1; `part` defaults from the platform.

## `host`

`host=hosts/<name>` composes `dau_build.build_config.HostConfig` — where the
build host keeps its source checkouts (`dau_core_root`, `dau_driver_root`,
`dau_utils_root`). dau-build packages no host options (checkout layouts are
site-specific); register them from a search-path package or a `--config-dir`
overlay. The stage tasks and hardware plans interpolate their checkout roots
from the composed host, and a direct `<field>=...` override always wins.

## `plan`

`plan=plans/<name>` composes a `HardwarePlan` model into the `plan` key;
`HardwarePlanTask` delegates to it. Each plan owns its required fields (so
pydantic enforces them, rather than a runtime check).

| Option                          | Model                      | Extra fields                                                                                                                |
| ------------------------------- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `plans/build-and-program`       | `BuildAndProgramPlan`      | —                                                                                                                           |
| `plans/local-build-and-program` | `LocalBuildAndProgramPlan` | `dau_core_root` (from `host=` or `plan.dau_core_root=`), `source_shell_root`, `dau_utils_root`, `overlay_tcl`, `smoke_command`, `python`, `vivado_settings` |
| `plans/validate-bitstream`      | `ValidateBitstreamPlan`    | `smoke_command`, `dau_utils_root`, `python`                                                                                 |
| `plans/flash`                   | `FlashPlan`                | `dau_utils_root`, `python`, `vivado_settings`                                                                               |
| `plans/recovery`                | `RecoveryPlan`             | —                                                                                                                           |
| `plans/thunderbolt-hold`        | `ThunderboltHoldPlan`      | —                                                                                                                           |
| `plans/thunderbolt-release`     | `ThunderboltReleasePlan`   | —                                                                                                                           |

Configure a plan with `plan.<field>=…`; see
[Program a bitstream on dpv1](../how-to/program-hardware.md).

## `design`

Declared in the defaults (`optional design: null`) but no option is packaged in
`dau-build`. It is the reserved group for higher-level design/tile compositions
registered by extension packages on the search path.

## `callable`

`callable/callable.yaml` is a fixed ccflow registry pointer wiring the evaluator
(`GraphEvaluator` + `MemoryCacheEvaluator` via `MultiEvaluator`). It is selected
by default and is not normally overridden.
