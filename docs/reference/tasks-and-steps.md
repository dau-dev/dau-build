# Task and step catalog

Tasks and steps are `ccflow.CallableModel`s selected from the `task` and `step`
config groups. This page lists each one, its `_target_` model, its required
fields (those set to `???` in the config, which must be supplied as overrides),
and its default execution mode.

The complete field set with defaults for any task or step is self-describing:

```text
dau-build-cfg-explain task=<path>
dau-build-cfg-explain step=<path>
```

Fields marked **required** have no default and must be overridden. On the Hydra
CLIs (`dau-build-cfg`, `dau-build-run`) fields carry a `model.` prefix; on the
flat CLIs (`dau-build`, `dau-build-steps`) they do not.

Tasks whose default is **plan** carry `execute: false` and produce plans,
manifests, and staged files without invoking a vendor toolchain or touching
hardware. Pass `execute=true` to run the privileged action. Tasks marked **run**
always execute their (non-privileged) operation.

## Tasks

### `tasks/spec/inspect` — `InspectTask`

Prints the resolved build-spec summary (each source, metadata file, and binary
asset with its originating manifest). Reads the spec via `spec_path` or a composed
`spec=` group. Mode: **run**.

### `tasks/spec/build` — `BuildArtifactsTask`

Writes the generated top-level SystemVerilog, DAU manifest, and
`artlink.manifest/v0` artifact bundle. Required: `output_root`. Mode: **run**.

### `tasks/spec/validate` — `ValidateTask`

Validates a generated artifact bundle when `manifest_path` is given (with optional
`root`), otherwise validates the spec. Mode: **run**.

### `tasks/sim/simulate` — `SimulateTask`

Validates or simulates a module, delegating to the simulator composed from the
`simulator` group (default `simulators/svparser`). `simulators/svparser` and
`simulators/cocotb` validate the module against the spec; `simulators/verilator`
runs a Verilator testbench (via `simulator.testbench_path`/`simulator.top_module`)
or a registered profile (`simulator.profile`). Select+configure the simulator
with, e.g., `simulator=simulators/verilator simulator.profile=<name>`. Mode:
**run**. See the [config group reference](config-groups.md) for the simulator
models.

### `tasks/build/synthesize` — `SynthesizeTask`

Writes the generated DAU top, manifest, and `artlink.manifest/v0` bundle, then
delegates to the synthesis engine composed from the `backend` group (default
`backends/vivado`). Required: `module`, `output_root`.

- `backend=backends/vivado` writes the `vivado/<artifact-stem>.manifest` handoff
  at `build_status=planned` and does not invoke Vivado.
- `backend=backends/yosys` runs a real synthesis of the generated top with yosys,
  failing the task if synthesis fails. The engine is fully configurable:
  `backend.frontend=verilog` (default, `read_verilog -sv`) or
  `backend.frontend=slang` (yosys-slang), and `backend.yosys=<exe>`.

Mode: **run**. See the [config group reference](config-groups.md) for the engine
models.

### `tasks/build/build-shell-project` — `BuildShellProjectTask`

Builds a standalone shell project from a generated Tcl script. Required:
`output_root`. Default `script: build_mm_job.tcl`. Mode: **plan**.

### `tasks/build/build-vivado-artifacts` — `BuildVivadoArtifactsTask`

Runs the generated overlay/build Vivado command, then validates the artifact
bundle. Moves the backend manifest from `planned` to `built` once the bitstream,
resource report, timing report, and Vivado log exist. Required: `work_root`.
Default `artifact_stem: dau-vivado`. Mode: **plan** (pass `execute=true` on the
Vivado host).

### `tasks/build/overlay-build` — `VivadoOverlayBuildTask`

Runs only the generated overlay/build Vivado command (no JTAG, PCIe rescan, or
smoke test). Required: `work_root`. Default `backend: vivado`. Mode: **plan**.

### `tasks/stage/stage-shell` — `ShellStageTask`

Copies a read-only Vivado shell seed into a generated work directory with
`rsync --delete --delete-excluded`, excluding Vivado run/cache/log outputs.
Required: `work_root`, `source_shell_root`. Mode: **plan**.

### `tasks/stage/stage-vivado-overlay` — `VivadoOverlayStageTask`

Writes the generated overlay Tcl, guarded build Tcl, backend manifest preview,
and Vivado command plan without invoking Vivado. Pass `dau_artifact_bundle=` to
fold a DAU artifact bundle into the overlay. Required: `work_root`,
`dau_core_root`. Mode: **plan**.

### `tasks/stage/stage-vivado-project` — `VivadoProjectStageTask`

Stages the shell seed and writes `<artifact-stem>.project` recording the shell
seed, work directory, DAU checkout roots, XDMA module path, backend artifacts,
and stage/build/validate commands, plus the same overlay artifacts as
`stage-vivado-overlay`. Required: `work_root`, `source_shell_root`,
`dau_core_root`, `dau_driver_root`. Mode: **plan**.

### `tasks/validate/validate-vivado-artifacts` — `ValidateVivadoArtifactsTask`

Checks that the manifest, overlay Tcl, build Tcl, command plan, and planned
output paths agree, without requiring Xilinx tools. Pass
`project_manifest_path=<artifact-stem>.project` to include the project manifest.
Required: `work_root`. Mode: **plan**.

### `tasks/hardware/hardware-plan` — `HardwarePlanTask`

Produces a live hardware-session command sequence for a named `plan` (e.g.
`local-build-and-program`, `validate-bitstream`, `flash`, `recovery`). Required:
`plan`, `work_root`. Mode: **plan** (pass `execute=true` on the hardware host).

### `tasks/flash/flash` — `FlashTask`

Produces a flashing plan for a bitstream. Requires `build_status=built` when it
consumes a `manifest_path`. Default `tool: openFPGAloader`, `mode: volatile`.
Mode: **run** (produces a plan).

### `tasks/flash/smoke-test` — `SmokeTestTask`

Produces a smoke-test plan/validation. Requires `build_status=built` when it
consumes a `manifest_path`. Required: `test`. Mode: **run** (produces a plan).

## Steps

Steps are lower-level operations over the spec and artifact bundle. All read the
spec via `spec_path` or a composed `spec=` group, and accept optional `board=`
and `backend=` groups.

| Step                    | Model                | Required      | Description                                                    |
| ----------------------- | -------------------- | ------------- | -------------------------------------------------------------- |
| `steps/inspect`         | `InspectStep`        | —             | Print the resolved spec summary.                               |
| `steps/validate`        | `ValidateStep`       | —             | Validate the spec / bundle.                                    |
| `steps/explain`         | `ExplainStep`        | —             | Explain the resolved inputs.                                   |
| `steps/resolved-config` | `ResolvedConfigStep` | —             | Print the resolved build config (spec + board + backend view). |
| `steps/generate`        | `GenerateStep`       | `output_root` | Generate the DAU top and artifacts.                            |
| `steps/write`           | `WriteStep`          | `output_root` | Write the artifact bundle.                                     |
| `steps/synthesis`       | `SynthesisStep`      | `output_root` | Produce the synthesis handoff.                                 |
| `steps/simulate`        | `SimulateStep`       | —             | Run a simulation (fields prefixed `simulate_*`).               |
