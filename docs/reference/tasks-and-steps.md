# Task catalog

Tasks are `ccflow.CallableModel`s selected from the `task` config group. This
page lists each one, its `_target_` model, its required
fields (those set to `???` in the config, which you must supply as overrides),
and its default execution mode.

The complete field set with defaults for any task is self-describing:

```text
dau-build --explain task=<path>
```

Fields marked **required** have no default and must be overridden. Field
overrides take a `model.` prefix (`model.<field>=value`), because the selected
task is composed into the `model` key.

Tasks whose default mode is **plan** have `execute: false` and produce plans,
manifests and staged files without invoking a vendor toolchain or touching
hardware. Pass `execute=true` to run the privileged action. Tasks marked
**run** always execute their operation, which needs no special privileges.

## Tasks

### `tasks/spec/inspect`: `InspectTask`

Prints the resolved build-spec summary (each source, metadata file and binary
asset with the manifest it came from). Reads the spec from `spec_path` or a
composed `spec=` group. Mode: **run**.

### `tasks/spec/build`: `BuildArtifactsTask`

Writes the generated top-level SystemVerilog, the DAU manifest and the
`artlink.manifest/v0` artifact bundle. Required: `output_root`. Mode: **run**.

### `tasks/spec/validate`: `ValidateTask`

Validates a generated artifact bundle when `manifest_path` is given (with an
optional `root`), otherwise validates the spec. Mode: **run**.

### `tasks/sim/simulate`: `SimulateTask`

Validates or simulates a module through the simulator composed from the
`simulator` group (default `simulators/svparser`). `simulators/svparser` and
`simulators/cocotb` validate the module against the spec; `simulators/verilator`
runs a Verilator testbench (`simulator.testbench_path` and
`simulator.top_module`) or a registered profile (`simulator.profile`). Select
and configure the simulator together, for example
`simulator=simulators/verilator simulator.profile=<name>`. Mode: **run**. The
simulator models are in the [config group reference](config-groups.md).

### `tasks/build/synthesize`: `SynthesizeTask`

Writes the generated DAU top, manifest and `artlink.manifest/v0` bundle, then
delegates to the synthesis engine composed from the `backend` group (default
`backends/vivado`). Required: `module`, `output_root`.

- `backend=backends/vivado` writes the `vivado/<artifact-stem>.manifest`
  handoff at `build_status=planned` and does not invoke Vivado.
- `backend=backends/yosys` runs a real synthesis of the generated top with
  yosys and fails the task if synthesis fails. The engine is configurable:
  `backend.frontend=verilog` (default, `read_verilog -sv`) or
  `backend.frontend=slang` (yosys-slang), and `backend.yosys=<exe>`.

Mode: **run**. The engine models are in the
[config group reference](config-groups.md).

### `tasks/build/render-cores`: `RenderCoresTask`

Renders the HDL of generated cores (those whose registry entry names a
generator) from their configured operating points, into `output_root`.
Required: `cores` (registry paths, `/dau-core/<name>`), `output_root`.
Mode: **run**.

### `tasks/build/synthesize-cores`: `SynthesizeCoresTask`

Characterizes cores one at a time, out of context, through the registry. It
resolves `/dau-core/<name>` entries from the dau-core lernaplugin's config
tree and stages one OOC synthesis per core: dependency-closed sources,
`-generic` values from the core's declared parameters merged with validated
`model.parameters` overrides, the part from `model.part` or the composed
`platform` group, and the clock as an XDC read before `synth_design`. It
writes the Tcl plus a command-plan runner. Required: `model.cores`,
`model.output_root`.

- Default (handoff): stages files only; run the plan on the Vivado host.
- `model.execute=true`: runs Vivado per core, parses utilization and timing
  into envelope reports, and flags drift from the envelope registered for the
  core. `model.clock_ports` maps clocks not named `clk` (an empty string
  means combinational: no constraint, no timing report). Package cores are
  rejected as synthesis tops.

Mode: **run**.

### `tasks/build/build-shell-project`: `BuildShellProjectTask`

Builds a standalone shell project from a generated Tcl script. Required:
`output_root`. Default `script: build_mm_job.tcl`. Mode: **plan**.

### `tasks/build/build-vivado-artifacts`: `BuildVivadoArtifactsTask`

Runs the generated overlay/build Vivado command, then validates the artifact
bundle. Moves the backend manifest from `planned` to `built` once the
bitstream, resource report, timing report and Vivado log exist. Required:
`work_root`. Default `artifact_stem: dau-vivado`. Mode: **plan** (pass
`execute=true` on the Vivado host).

### `tasks/build/overlay-build`: `VivadoOverlayBuildTask`

Runs only the generated overlay/build Vivado command, with no JTAG, PCIe
rescan or smoke test. Required: `work_root`. Default `backend: vivado`. Mode:
**plan**.

### `tasks/stage/stage-shell`: `ShellStageTask`

Copies a read-only Vivado shell seed into a generated work directory with
`rsync --delete --delete-excluded`, excluding Vivado run, cache and log
outputs. Required: `work_root`, `source_shell_root`. Mode: **plan**.

### `tasks/stage/stage-vivado-overlay`: `VivadoOverlayStageTask`

Writes the generated overlay Tcl, guarded build Tcl, backend manifest preview
and Vivado command plan without invoking Vivado. Pass `dau_artifact_bundle=`
to fold a DAU artifact bundle into the overlay. Required: `work_root`,
`dau_core_root`. Mode: **plan**.

### `tasks/stage/stage-vivado-project`: `VivadoProjectStageTask`

Stages the shell seed and writes `<artifact-stem>.project`, which records the
shell seed, work directory, DAU checkout roots, XDMA module path, backend
artifacts and the stage/build/validate commands. Also writes the same overlay
artifacts as `stage-vivado-overlay`. Required: `work_root`,
`source_shell_root`, `dau_core_root`, `dau_driver_root`. Mode: **plan**.

### `tasks/validate/validate-vivado-artifacts`: `ValidateVivadoArtifactsTask`

Checks that the manifest, overlay Tcl, build Tcl, command plan and planned
output paths agree, without Xilinx tools. Pass
`project_manifest_path=<artifact-stem>.project` to include the project
manifest. Required: `work_root`. Mode: **plan**.

### `tasks/hardware/hardware-plan`: `HardwarePlanTask`

Produces a live hardware-session command sequence for the plan composed from
the `plan` group (`plan=plans/<name>`). The task owns the shared toolchain
fields (`work_root`, `bitstream`, `vivado`, `jtag_cable` and so on); each plan
owns its own fields (`plan.dau_core_root`, for example). Host access composes
from the `platform` group's `host_access` (`platform=platforms/<vendor>/<board>`).
dau-build has no board defaults, so a step that needs an unset fact fails to
render. Required: `plan`, `work_root`. Mode: **plan** (pass `execute=true` on
the hardware host). The plan models are in the
[config group reference](config-groups.md).
