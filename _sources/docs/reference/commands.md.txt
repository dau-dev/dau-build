# Command reference

`dau-build` installs a single console script (entry point
`dau_build.cli:main`). It runs typed `ccflow.CallableModel`s composed from the
Hydra config tree in `dau_build/config`.

```text
dau-build [--config-dir DIR] [--explain] <overrides ...>
```

| Option / argument | Description                                                                                                        |
| ----------------- | ----------------------------------------------------------------------------------------------------------------- |
| `--config-dir DIR` | A user config overlay directory. Its groups are merged into the tree (open registration).                        |
| `--explain`        | Print the fully resolved config as YAML and exit without running.                                                 |
| overrides          | Hydra overrides — see below.                                                                                       |

## Overrides

Every argument after the options is a Hydra override:

| Form                  | Selects                                                                                          | Example                          |
| --------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------- |
| `task=<path>`         | the task to run (populates `model`)                                                             | `task=tasks/sim/simulate`        |
| `step=<path>`         | a low-level step to run (populates `model`)                                                     | `step=steps/inspect`             |
| `<group>=<option>`    | a config group option: `spec=`, `board=`, `backend=`, `simulator=`, `design=`, `plan=`, `platform=` | `backend=backends/yosys`         |
| `model.<field>=value` | a field on the selected task/step model                                                         | `model.output_root=out`          |

Task, step, and group option names are **path-style**, mirroring the config
tree (`task=tasks/sim/simulate`, `step=steps/inspect`, `backend=backends/yosys`).
Short names (`task=simulate`) are not accepted.

`dau-build` raises an error if no `task=`/`step=` is selected (nothing populates
`model`).

The full set of task and step names and their fields is in the
[task and step catalog](tasks-and-steps.md). The config groups selectable with
`spec=`, `board=`, `backend=`, `platform=` are in the
[config group reference](config-groups.md).

## Examples

Inspect, build, and validate a spec or generated bundle:

```text
dau-build task=tasks/spec/inspect  model.spec_path=examples/identity/dau-build.yaml
dau-build task=tasks/spec/build     model.spec_path=examples/identity/dau-build.yaml model.output_root=outputs/identity
dau-build task=tasks/spec/validate  model.manifest_path=outputs/identity/dau-identity.manifest model.root=outputs/identity
```

Select a config group — here the Yosys synthesis backend instead of the default
Vivado engine:

```text
dau-build task=tasks/build/synthesize spec=specs/identity backend=backends/yosys model.output_root=out
```

Inspect what a set of overrides composes to, without running:

```text
dau-build --explain task=tasks/build/synthesize spec=specs/identity backend=backends/yosys
```

Run a low-level step (development/plumbing; user-facing workflows use tasks):

```text
dau-build step=steps/validate model.spec_path=examples/identity/dau-build.yaml
```

## Open registration and cross-package composition

The Hydra search path is active, so config groups registered by other installed
packages (through their own `hydra.lernaplugins` entry point) compose uniformly
alongside the packaged ones — no `--config-dir` overlay needed. For example,
with the private `dau` package installed, `dau-build task=dpv1-shell
design=designs/bar-noc` resolves a task defined in `dau`'s config tree. A
`--config-dir DIR` overlay adds ad-hoc task configs (and new `_target_` models
from any importable package) the same way. See
[Extending dau-build](../how-to/extend-dau-build.md).
