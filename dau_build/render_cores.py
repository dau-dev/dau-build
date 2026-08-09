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

from pathlib import Path

from ccflow import Flow, NullContext

from dau_build.build_steps import BuildCallableModel, BuildStepError, BuildStepResult
from dau_build.synthesize_cores import resolve_core_definitions

# The environment variable a core provider reads to find rendered sources.
# An environment variable rather than a call is the only channel that does
# not require the public package to import the private one.
RENDER_ROOT_ENV = "DAU_CORE_RENDER_ROOT"

# Subdirectory of a build tree that holds rendered HDL.
RENDER_DIRNAME = "generated"


def render_generated_cores(definitions, *, root: Path) -> tuple[Path, ...]:
    """Render every generated core in ``definitions`` under ``root``.

    Returns the written paths. Cores with a checked-in source are skipped —
    rendering over reviewed HDL is refused by the core provider, and asking
    it to would be a bug here.

    Points ``DAU_CORE_RENDER_ROOT`` at the directory so the provider's own
    ``source_path()`` resolves to what was just written. A build tree
    rendering its own inputs is what keeps two concurrent builds off one
    file.
    """
    import os

    target = root / RENDER_DIRNAME
    written: list[Path] = []
    for definition in definitions:
        if getattr(definition, "generator", None) is None:
            continue
        render = getattr(definition, "render", None)
        if render is None:
            raise BuildStepError(
                f"core {getattr(definition, 'name', '?')!r} declares a generator but the core provider "
                "offers no render(); the installed dau-core is too old for generated cores"
            )
        written.append(render(into=target))
    if written:
        os.environ[RENDER_ROOT_ENV] = str(target)
    return tuple(written)


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

        generated = [d.name for d in definitions if getattr(d, "generator", None) is not None]
        skipped = [d.name for d in definitions if getattr(d, "generator", None) is None]
        return BuildStepResult(
            step="render-cores",
            message=(
                f"dau-build-render-cores\trendered={','.join(generated) if generated else 'none'} "
                f"checked_in={','.join(skipped) if skipped else 'none'} "
                f"output_root={root / RENDER_DIRNAME} files={len(written)} status=rendered"
            ),
        )
