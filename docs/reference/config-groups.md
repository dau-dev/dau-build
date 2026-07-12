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
  - optional spec: null
  - optional design: null
  - callable: callable
```

Every group except `callable` is `optional … null`: nothing is selected unless
overridden. A run selects exactly one of `task=` or `step=` to populate `model`,
optionally augmented by `spec=`, `board=`, `backend=`, and `platform=`.

Each option file begins with a `# @package <key>` directive that places its
content under that top-level key. Tasks and steps use `# @package model`; the
other groups use their singular key (`# @package board`, etc.).

| Group      | `@package` key | Instantiated model                       | Selects                                                          |
| ---------- | -------------- | ---------------------------------------- | ---------------------------------------------------------------- |
| `task`     | `model`        | a `…Task` `ccflow.CallableModel`         | The unit of work to run.                                         |
| `step`     | `model`        | a `…Step` `ccflow.CallableModel`         | A lower-level plumbing operation.                                |
| `spec`     | `spec`         | `dau_build.build_spec.BuildSpec`         | A composed build spec (Hydra-native alternative to `spec_path`). |
| `board`    | `board`        | `dau_build.build_config.BoardConfig`     | A board's build-config view.                                     |
| `backend`  | `backend`      | `dau_build.build_config.BackendConfig`   | The synthesis backend.                                           |
| `platform` | `platform`     | `dau_build.platforms.PlatformDefinition` | The full physical platform definition.                           |
| `design`   | `design`       | (none packaged)                          | Reserved; registered by extension packages.                      |
| `callable` | —              | ccflow registry pointer                  | Fixed evaluator wiring; not normally overridden.                 |

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

`backend=backends/vivado` composes `BackendConfig` into the `backend` key.

| Option            | `name`   | `invocation` | Description                    |
| ----------------- | -------- | ------------ | ------------------------------ |
| `backends/vivado` | `vivado` | `standard`   | The Vivado/XDMA backend.       |
| `backends/none`   | `none`   | `dry-run`    | No vendor toolchain (dry-run). |

`BackendConfig` fields: `name` (str), `invocation` (str). Vivado is the only
backend with a real implementation. See
[the architecture explanation](../explanation/architecture.md) for
current-vs-future backend support.

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
`bandwidth_bytes_per_s`), and `HostLink` (`interface`, `pcie_lanes`,
`xdma_personality`).

## `design`

Declared in the defaults (`optional design: null`) but no option is packaged in
`dau-build`. It is the reserved group for higher-level design/tile compositions
registered by extension packages on the search path.

## `callable`

`callable/callable.yaml` is a fixed ccflow registry pointer wiring the evaluator
(`GraphEvaluator` + `MemoryCacheEvaluator` via `MultiEvaluator`). It is selected
by default and is not normally overridden.
