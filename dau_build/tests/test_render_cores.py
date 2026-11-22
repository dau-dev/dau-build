"""Rendering generated cores, without importing a core provider.

dau-build is public and the cores that need rendering are not, so the whole
mechanism has to work on whatever the ccflow registry hands back. These
tests use a stand-in with the same surface, which is also the honest way to
check it: if this task ever grows a dependency on a real core package, these
stop compiling.
"""

from pathlib import Path

import pytest

from dau_build.build_steps import BuildStepError
from dau_build.render_cores import RENDER_DIRNAME, render_generated_cores


class _Rendered:
    def __init__(self, name: str, text: str) -> None:
        self.source_name = name
        self.text = text

    def write(self, into: Path) -> Path:
        into.mkdir(parents=True, exist_ok=True)
        path = into / self.source_name
        path.write_text(self.text, encoding="utf-8")
        return path


class _Generator:
    def __init__(self, name: str, text: str) -> None:
        self._rendered = _Rendered(name, text)

    def render(self) -> _Rendered:
        return self._rendered


class _GeneratedCore:
    """The surface a generated core presents: a generator and a render()."""

    kind = type("Kind", (), {"value": "operator"})()

    def __init__(self, name: str, source: str, text: str = "// rendered\n") -> None:
        self.name = name
        self.source = source
        self.generator = _Generator(source, text)

    def render(self, *, into: Path | None = None) -> Path:
        assert into is not None
        return self.generator.render().write(into)


class _CheckedInCore:
    kind = type("Kind", (), {"value": "operator"})()
    generator = None

    def __init__(self, name: str) -> None:
        self.name = name

    def render(self, *, into: Path | None = None) -> Path:
        raise AssertionError(f"checked-in core {self.name} must not be rendered (into={into})")


def test_generated_cores_are_written_under_the_build_tree(tmp_path: Path) -> None:
    core = _GeneratedCore("fixed-point-exp", "dau_fixed_point_exp.sv", "// exp\n")
    written = render_generated_cores([core], root=tmp_path)

    assert len(written) == 1
    assert written[0] == tmp_path / RENDER_DIRNAME / "dau_fixed_point_exp.sv"
    assert written[0].read_text(encoding="utf-8") == "// exp\n"


def test_checked_in_cores_are_left_alone(tmp_path: Path) -> None:
    """Rendering over reviewed HDL would replace it with generated text."""
    assert render_generated_cores([_CheckedInCore("row-map-alu")], root=tmp_path) == ()
    assert not (tmp_path / RENDER_DIRNAME).exists()


def test_rendering_leaves_the_environment_untouched(tmp_path: Path, monkeypatch) -> None:
    """The render directory is passed to the provider, not published in
    process state that a later build would inherit."""
    import os

    monkeypatch.delenv("DAU_CORE_RENDER_ROOT", raising=False)
    before = dict(os.environ)
    render_generated_cores([_GeneratedCore("c", "c.sv")], root=tmp_path)
    assert dict(os.environ) == before


class _RegistryCore:
    """A registry entry as synthesize-cores walks it: dependencies and a
    source path that depends on where generated cores were rendered."""

    def __init__(self, name: str, depends: tuple[str, ...] = ()) -> None:
        self.name = name
        self.depends = depends
        self.rendered_into: Path | None = None

    def source_path(self, *, rendered_into: Path | None = None) -> Path:
        self.rendered_into = rendered_into
        return Path(f"{self.name}.sv")


def test_synthesis_reads_sources_from_this_build_tree(tmp_path: Path, monkeypatch) -> None:
    """Each dependency is asked for its source in the directory this build
    rendered into, so two builds never read each other's generated files."""
    from types import SimpleNamespace

    from dau_build.synthesize_cores import SynthesizeCoresTask

    package = _RegistryCore("pkg")
    core = _RegistryCore("core", depends=("pkg",))
    registry = SimpleNamespace(models={"pkg": package, "core": core})
    monkeypatch.setattr(SynthesizeCoresTask, "_registries", lambda self: (registry,))

    task = SynthesizeCoresTask(cores=("/dau-core/core",), output_root=tmp_path, part="xc7a200tfbg484-2", clock_period_ns=8.0)
    assert task._sources_for(core, root=tmp_path) == (Path("pkg.sv"), Path("core.sv"))
    assert package.rendered_into == core.rendered_into == tmp_path / RENDER_DIRNAME


def test_multiple_generated_cores_all_render(tmp_path: Path) -> None:
    cores = [_GeneratedCore(f"c{i}", f"c{i}.sv", f"// {i}\n") for i in range(3)]
    written = render_generated_cores(cores, root=tmp_path)
    assert len(written) == 3
    assert {p.name for p in written} == {"c0.sv", "c1.sv", "c2.sv"}


def test_the_task_renders_the_cores_it_is_given(tmp_path: Path, monkeypatch) -> None:
    """The task itself, not just the helper underneath it.

    Resolution goes through the ccflow registry, so it is stubbed here for
    the same reason the core stand-ins above exist: this package must not
    grow a dependency on a private core provider to be testable.
    """
    from ccflow import NullContext

    from dau_build import render_cores

    cores = [_GeneratedCore("fixed-point-exp", "dau_fixed_point_exp.sv"), _CheckedInCore("row-map-alu")]
    monkeypatch.setattr(render_cores, "resolve_core_definitions", lambda entries: cores)

    task = render_cores.RenderCoresTask(cores=("/dau-core/fixed-point-exp", "/dau-core/row-map-alu"), output_root=tmp_path)
    result = task(NullContext())

    assert result.step == "render-cores"
    assert "rendered=fixed-point-exp" in result.message
    assert "checked_in=row-map-alu" in result.message
    assert "files=1" in result.message
    assert (tmp_path / RENDER_DIRNAME / "dau_fixed_point_exp.sv").is_file()


def test_the_task_refuses_an_empty_selection(tmp_path: Path, monkeypatch) -> None:
    """A task that composed to no cores has nothing to do, and silently
    succeeding would look like a completed render."""
    from ccflow import NullContext

    from dau_build import render_cores

    monkeypatch.setattr(render_cores, "resolve_core_definitions", lambda entries: [])
    task = render_cores.RenderCoresTask(cores=(), output_root=tmp_path)
    with pytest.raises(BuildStepError, match="no cores selected"):
        task(NullContext())
