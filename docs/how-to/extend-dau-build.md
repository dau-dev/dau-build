# How to extend dau-build

dau-build is designed to be extended without editing it. Its config tree is on
the Hydra search path, so an installed package can add its own tasks, steps,
boards, backends, or platforms, and they compose alongside the packaged ones.
This guide shows the three things you are most likely to add: a new task from
your own package, a new board or platform, and a new synthesis backend.

For the concepts behind the search path and the config groups, see
[the architecture explanation](../explanation/architecture.md). For an ad-hoc
overlay without packaging, jump to [the last section](#try-it-without-packaging).

## Register your config tree on the search path

The one-time setup: give your package a `config` directory laid out like
dau-build's (config groups as subdirectories), make it importable, and register
it on the Hydra search path.

1. Put a Hydra config tree in your package, e.g. `mypkg/config/`, with an
   `__init__.py` so `mypkg.config` is an importable package (Hydra resolves the
   `pkg:` search-path entry by importing it).

1. Add the entry point and depend on the search-path bridge in your
   `pyproject.toml`:

   ```toml
   dependencies = ["dau-build", "lerna>=2.0.4"]

   [project.entry-points."hydra.lernaplugins"]
   mypkg = "pkg:mypkg.config"
   ```

   `lerna` is what honors the `hydra.lernaplugins` entry point; without it the
   entry point is inert.

Your groups now compose through dau-build's Hydra CLIs. dau-build never imports
your package — extension flows one way, so your private code can depend on
dau-build while dau-build stays free of it.

## Add a task

A task is a config file plus the `ccflow.CallableModel` it targets.

1. Write the model as a `ccflow.CallableModel` in your package (e.g.
   `mypkg.tasks.MyTask`).

1. Add `mypkg/config/task/tasks/<category>/<name>.yaml`:

   ```yaml
   # @package model

   _target_: mypkg.tasks.MyTask
   some_field: ???
   other_field: default
   ```

   The `# @package model` directive makes the selected task *become* the `model`
   that runs. dau-build derives its name→class registry by globbing the config
   tree, so there is nothing else to register — the `_target_` may point at any
   importable module.

1. Run it through dau-build's Hydra CLI:

   ```bash
   dau-build-run task=tasks/<category>/<name> model.some_field=value
   ```

The private `dau` package does exactly this to add `task=dpv1-shell`: with `dau`
installed, `dau-build-run task=dpv1-shell model.shell=... model.output_root=...`
resolves a task defined entirely in `dau`, with no `--config-dir` overlay.

## Add a board or platform

Boards and platforms reuse dau-build's existing models, so they are pure config —
no new Python.

For a board's build-config view, add
`mypkg/config/board/boards/<vendor>/<board>.yaml`:

```yaml
# @package board

_target_: dau_build.build_config.BoardConfig
name: myboard
platform: my-xdma
shell: my-shell
```

For the hardware platform definition (part, resource budget, memory, host link),
add `mypkg/config/platform/platforms/<vendor>/<board>.yaml` targeting
`dau_build.platforms.PlatformDefinition`. Select them with
`board=boards/<vendor>/<board>` and `platform=platforms/<vendor>/<board>`.

## Add a synthesis backend {#add-a-synthesis-backend}

Adding a *real* backend (for example a yosys/nextpnr flow) is more than a config
file, because today `BackendConfig` is only a label and synthesis codegen is
hard-wired to Vivado. Read
[the backend section of the architecture explanation](../explanation/architecture.md#backends)
first — it describes exactly what is a label and what is wired.

A new backend requires four things:

1. **A backend label**, so it is selectable. Add
   `<pkg>/config/backend/backends/yosys.yaml` reusing the existing model:

   ```yaml
   # @package backend

   _target_: dau_build.build_config.BackendConfig
   name: yosys
   invocation: standard
   ```

   By itself this only changes the reported metadata in the resolved config.

1. **A backend module** that generates the flow's artifacts, paralleling
   `dau_build/vivado_backend.py`: its own request/artifact `ccflow.BaseModel`s
   and `generate_*`/`validate_*` entry points, emitting whatever scripts and
   manifests the yosys/nextpnr flow needs, with a backend identity string
   analogous to `dau_build.vivado_backend.vivado_overlay`.

1. **Real engine dispatch.** `SynthesizeTask.engine` is currently
   `Literal["vivado"]` and the task calls the Vivado handoff unconditionally.
   Widen the literal to include your engine and replace that unconditional call
   with a branch on `engine`. This is the change that must land *in dau-build*
   (or in a fork), since the dispatch lives there — the other pieces can live in
   your package.

1. **Build and validate tasks**, paralleling `build-vivado-artifacts.yaml` and
   `validate-vivado-artifacts.yaml`, each targeting a new build-step
   `ccflow.CallableModel` for your backend.

Everything except step 3 composes purely from your package via the search path.
Until a backend implements steps 2–4, selecting its label runs no real codegen.

## Try it without packaging {#try-it-without-packaging}

To test a config overlay before packaging it, point the argparse CLI at a
directory with `--config-dir`:

```bash
dau-build-cfg --config-dir ./my-configs task=tasks/<category>/<name> model.some_field=value
```

The overlay's groups merge into the tree for that run, and its `_target_` models
can come from any importable package. This is the quickest way to iterate on a new
task config before wiring up the entry point.
