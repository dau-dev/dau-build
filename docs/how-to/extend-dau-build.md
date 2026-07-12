# How to extend dau-build

dau-build is designed to be extended without editing it. Its config tree is on
the Hydra search path, so an installed package can add its own tasks, steps,
boards, backends, or platforms, and they compose alongside the packaged ones.
This guide shows the three things you are most likely to add: a new task from
your own package, a new board or platform, and a new synthesis backend.

For the concepts behind the search path and the config groups, see
[the architecture explanation](../explanation/architecture.md). To test an
overlay without packaging, see **Try it without packaging** at the end of this
guide.

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

1. Run it through the `dau-build` CLI:

   ```bash
   dau-build task=tasks/<category>/<name> model.some_field=value
   ```

The private `dau` package does exactly this to add `task=dpv1-shell`: with `dau`
installed, `dau-build task=dpv1-shell design=designs/bar-noc model.output_root=...`
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

## Add a synthesis engine

The synthesis engine is the `backend` config group: each option instantiates a
polymorphic `SynthesisEngine` model, and `SynthesizeTask` delegates to
`engine.synthesize(...)` — there is no dispatch `if` to edit. `YosysEngine` is
the worked example; read
[the backend section of the architecture explanation](../explanation/architecture.md)
first, then use `yosys_backend.py` + `YosysEngine` as the template. A new engine
requires:

1. **An engine model** — a `SynthesisEngine` subclass with a `synthesize`
   method and whatever fields it needs (like `YosysEngine.frontend`). Its
   `synthesize` either generates a plan (Vivado's Tcl/manifests) or generates and
   runs a tool (yosys's script + `run_yosys_synthesis`). Put its heavy backend
   logic in a module paralleling `yosys_backend.py`.

1. **A config option** so it is selectable and configurable. Add
   `<pkg>/config/backend/backends/<name>.yaml` (this is exactly
   `backends/yosys.yaml`):

   ```yaml
   # @package backend

   _target_: mypkg.engines.NextpnrEngine
   name: nextpnr
   invocation: standard
   ```

   It composes as `backend=backends/<name>` and is fully configurable via
   `backend.<field>=...` — no change to `SynthesizeTask`.

1. **Any build/validate tasks** your flow needs, paralleling the Vivado ones,
   each targeting a new `ccflow.CallableModel`.

Because the engine is just a composed model, an engine defined in your own
package composes purely via the search path — nothing lands in dau-build unless
you extend the packaged set.

## Try it without packaging

To test a config overlay before packaging it, point the CLI at a directory with
`--config-dir`:

```bash
dau-build --config-dir ./my-configs task=tasks/<category>/<name> model.some_field=value
```

The overlay's groups merge into the tree for that run, and its `_target_` models
can come from any importable package. This is the quickest way to iterate on a new
task config before wiring up the entry point.
