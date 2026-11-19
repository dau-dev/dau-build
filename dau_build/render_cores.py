"""Materialize cores whose HDL is rendered rather than checked in.

Most tiles ship a ``.sv`` beside their registry entry. A few cannot: a
fixed-point transcendental needs a table of constants that no HDL language
can compute at elaboration, so its source is a function of its configured
operating point. Those cores declare a generator in the registry and refuse
to hand out a source path until it has been rendered.

This task does the rendering, and `synthesize-cores` calls the same code
path before it stages anything — so a generated core is never synthesized
from a stale file, and never from one whose parameters nobody recorded.

Like every other task here, the core provider is reached through the ccflow
registry and never imported: dau-build is public, the cores that need
rendering are not, and a generator is just a callable on the definition the
registry handed back.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ccflow import Flow, NullContext

from dau_build.build_steps import BuildCallableModel, BuildStepError, BuildStepResult
from dau_build.synthesize_cores import resolve_core_definitions

# Subdirectory of a build tree that holds rendered HDL.
RENDER_DIRNAME = "generated"


class RenderableCore(Protocol):
    """The part of a registry core definition this task reads: its name,
    whether it declares a generator, and the render call."""

    @property
    def name(self) -> str: ...

    @property
    def generator(self) -> object | None: ...

    def render(self, *, into: Path | None = None) -> Path: ...


def render_generated_cores(definitions: Sequence[RenderableCore], *, root: Path) -> tuple[Path, ...]:
    """Render every generated core in ``definitions`` under ``root``.

    Returns the written paths. Cores with a checked-in source are skipped —
    rendering over reviewed HDL is refused by the core provider, and asking
    it to would be a bug here.

    Callers read the rendered sources back with
    ``source_path(rendered_into=root / RENDER_DIRNAME)``. A build tree
    rendering its own inputs is what keeps two concurrent builds off one
    file.
    """
    target = root / RENDER_DIRNAME
    return tuple(definition.render(into=target) for definition in definitions if definition.generator is not None)


class RenderCoresTask(BuildCallableModel):
    """Render generated cores' HDL from their configured operating points."""

    # registry paths (`/dau-core/<core-name>`; bare core names accepted)
    cores: tuple[str, ...]
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:  # noqa: ARG002 (ccflow requires the name `context`)
        definitions = resolve_core_definitions(self.cores)
        if not definitions:
            raise BuildStepError("no cores selected; pass model.cores=[/dau-core/<name>,...]")
        root = self.output_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        written = render_generated_cores(definitions, root=root)

        generated = [d.name for d in definitions if d.generator is not None]
        skipped = [d.name for d in definitions if d.generator is None]
        return BuildStepResult(
            step="render-cores",
            message=(
                f"dau-build-render-cores\trendered={','.join(generated) if generated else 'none'} "
                f"checked_in={','.join(skipped) if skipped else 'none'} "
                f"output_root={root / RENDER_DIRNAME} files={len(written)} status=rendered"
            ),
        )
