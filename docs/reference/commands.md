# Command reference

`dau-build` installs one console script (entry point `dau_build.cli:main`). It
runs typed `ccflow.CallableModel`s composed from the Hydra config tree in
`dau_build/config`.

```text
dau-build [--config-dir DIR] [--explain] <overrides ...>
```

| Option / argument  | Description                                                           |
| ------------------ | --------------------------------------------------------------------- |
| `--config-dir DIR` | A user config overlay directory. Its groups are merged into the tree. |
| `--explain`        | Print the fully resolved config as YAML and exit without running.     |
| overrides          | Hydra overrides, described below.                                     |

## Overrides

Every argument after the options is a Hydra override:

| Form                  | Selects                                                                                             | Example                   |
| --------------------- | --------------------------------------------------------------------------------------------------- | ------------------------- |
| `task=<path>`         | the task to run (populates `model`)                                                                 | `task=tasks/sim/simulate` |
| `step=<path>`         | a low-level step to run (populates `model`)                                                         | `step=steps/inspect`      |
| `<group>=<option>`    | a config group option: `spec=`, `board=`, `backend=`, `simulator=`, `design=`, `plan=`, `platform=` | `backend=backends/yosys`  |
| `model.<field>=value` | a field on the selected task or step model                                                          | `model.output_root=out`   |

Task, step and group option names are paths into the config tree
(`task=tasks/sim/simulate`, `step=steps/inspect`, `backend=backends/yosys`).
Short names such as `task=simulate` are not accepted.

`dau-build` exits with an error if neither `task=` nor `step=` is given, since
nothing then populates `model`.

The task and step names and their fields are in the
[task and step catalog](tasks-and-steps.md). The groups selectable with
`spec=`, `board=`, `backend=` and `platform=` are in the
[config group reference](config-groups.md).

## Examples

Inspect, build and validate a spec or generated bundle:

```text
dau-build task=tasks/spec/inspect  model.spec_path=examples/identity/dau-build.yaml
dau-build task=tasks/spec/build     model.spec_path=examples/identity/dau-build.yaml model.output_root=outputs/identity
dau-build task=tasks/spec/validate  model.manifest_path=outputs/identity/dau-identity.manifest model.root=outputs/identity
```

Select a config group, here the yosys synthesis backend instead of the default
Vivado engine:

```text
dau-build task=tasks/build/synthesize spec=specs/identity backend=backends/yosys model.module=dau_identity_top model.output_root=out
```

Show what a set of overrides composes to, without running:

```text
dau-build --explain task=tasks/build/synthesize spec=specs/identity backend=backends/yosys
```

Run a low-level step. Steps are plumbing for development; the user-facing
workflows are tasks:

```text
dau-build step=steps/validate model.spec_path=examples/identity/dau-build.yaml
```

## Composition across packages

The Hydra search path is active, so config groups registered by other
installed packages (through their own `hydra.lernaplugins` entry point)
compose alongside the packaged ones with no `--config-dir` overlay. With the
`dau` package installed, for example, `dau-build task=shell design=designs/bar-noc`
resolves a task defined in `dau`'s config tree. A `--config-dir DIR` overlay
adds ad-hoc task configs (and new `_target_` models from any importable
package) the same way. See [Extending dau-build](../how-to/extend-dau-build.md).
