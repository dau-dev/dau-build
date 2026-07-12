# Command reference

`dau-build` installs five console scripts. All of them ultimately run typed
`ccflow.CallableModel`s composed from the Hydra config tree in
`dau_build/config`. They differ in how arguments are parsed and which surface of
the tree they expose.

| Command                 | Entry point                                | Argument style                                 | Purpose                                            |
| ----------------------- | ------------------------------------------ | ---------------------------------------------- | -------------------------------------------------- |
| `dau-build`             | `dau_build.build_spec:main`                | flat `task=<path> field=value`                 | Flat task dispatch                                 |
| `dau-build-steps`       | `dau_build.build_spec:main_callable_steps` | flat `step=<path> field=value`                 | Low-level step dispatch                            |
| `dau-build-cfg`         | `dau_build.cli:main`                       | `[--config-dir DIR] task=<path> model.<f>=v`   | Compose and run a task through the registry        |
| `dau-build-cfg-explain` | `dau_build.cli:explain`                    | same as `dau-build-cfg`                        | Print the resolved config; do not run              |
| `dau-build-run`         | `dau_build.cli:run`                        | Hydra app: `task=<path> group=opt model.<f>=v` | Hydra `hydra.main` app with the search path active |

Task and step names are **path-style**, mirroring the config tree: `task=tasks/sim/simulate`, `step=steps/inspect`. Short names (`task=simulate`) are not accepted. Field overrides carry a `model.` prefix on the Hydra CLIs (`dau-build-cfg`, `dau-build-cfg-explain`, `dau-build-run`) and no prefix on the flat CLIs (`dau-build`, `dau-build-steps`).

The full set of task and step names and their fields is in the [task and step catalog](tasks-and-steps.md). The config groups selectable with `spec=`, `board=`, `backend=`, `platform=` are in the [config group reference](config-groups.md).

## `dau-build`

```text
dau-build task=<path> [field=value ...]
```

Dispatches a task through the registry from the flat surface — equivalent to `dau-build-cfg` without the `model.` prefix on field overrides. Exits non-zero with a usage message if the first argument is not a `key=value` override. For example, the spec operations (inspect, build, and validate a spec or generated bundle):

```text
dau-build task=tasks/spec/inspect  spec_path=examples/identity/dau-build.yaml
dau-build task=tasks/spec/build     spec_path=examples/identity/dau-build.yaml output_root=outputs/identity
dau-build task=tasks/spec/validate  manifest_path=outputs/identity/dau-identity.manifest root=outputs/identity
```

## `dau-build-steps`

```text
dau-build-steps step=<path> [field=value ...]
```

Dispatches a low-level step (see [steps](tasks-and-steps.md)). Fields carry no `model.` prefix. Steps are development/plumbing operations; user-facing workflows use tasks.

## `dau-build-cfg`

```text
dau-build-cfg [--config-dir DIR] task=<path> [group=option ...] [model.<field>=value ...]
```

Composes the base config with the given overrides and runs the selected model through ccflow. Prints the model's result message.

| Option         | Description                                                                                                   |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| `--config-dir` | A user config overlay directory. Its groups are merged into the tree (open registration).                     |
| overrides      | Hydra overrides: group selections (`task=`, `spec=`, `board=`, `backend=`) and field sets (`model.<field>=`). |

Raises an error if no `task=`/`step=` is selected (nothing populates `model`).

## `dau-build-cfg-explain`

```text
dau-build-cfg-explain [--config-dir DIR] task=<path> [group=option ...] [model.<field>=value ...]
```

Takes the same arguments as `dau-build-cfg` but prints the fully resolved config as YAML and does not run anything. Use it to inspect what a set of overrides composes to.

## `dau-build-run`

```text
dau-build-run task=<path> [group=option ...] [model.<field>=value ...]
```

A Hydra `hydra.main` application over `dau_build/config` (config name `base`). The Hydra search path is active, so config groups registered by other installed packages compose uniformly alongside the packaged ones. Standard Hydra CLI conventions apply: use `+group=option` to add a group not present in the defaults, and `-m` for multirun.

This is the recommended entry point for cross-package composition. For example, with the private `dau` package installed, `dau-build-run task=dpv1-shell` resolves a task defined in `dau`'s config tree with no `--config-dir` overlay. See [Extending dau-build](../how-to/extend-dau-build.md).
