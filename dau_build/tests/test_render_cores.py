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
from dau_build.render_cores import RENDER_DIRNAME, RENDER_ROOT_ENV, render_generated_cores


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

    def render(self, *, into: Path) -> Path:
        return self.generator.render().write(into)


class _CheckedInCore:
    kind = type("Kind", (), {"value": "operator"})()
    generator = None

    def __init__(self, name: str) -> None:
        self.name = name


class _GeneratorWithoutRender(_CheckedInCore):
    """A provider too old to render: declares a generator, offers no render."""

    generator = object()


def test_generated_cores_are_written_under_the_build_tree(tmp_path: Path) -> None:
    core = _GeneratedCore("int32-fixed-point-exp", "dau_int32_fixed_point_exp.sv", "// exp\n")
    written = render_generated_cores([core], root=tmp_path)

    assert len(written) == 1
    assert written[0] == tmp_path / RENDER_DIRNAME / "dau_int32_fixed_point_exp.sv"
    assert written[0].read_text(encoding="utf-8") == "// exp\n"


def test_checked_in_cores_are_left_alone(tmp_path: Path) -> None:
    """Rendering over reviewed HDL would replace it with generated text."""
    assert render_generated_cores([_CheckedInCore("int32-row-map-alu")], root=tmp_path) == ()
    assert not (tmp_path / RENDER_DIRNAME).exists()


def test_the_render_root_is_published_for_the_core_provider(tmp_path: Path, monkeypatch) -> None:
    """The provider resolves source paths through an environment variable --
    the only channel that does not require the public package to import the
    private one."""
    monkeypatch.delenv(RENDER_ROOT_ENV, raising=False)
    render_generated_cores([_GeneratedCore("c", "c.sv")], root=tmp_path)
    import os

    assert os.environ[RENDER_ROOT_ENV] == str(tmp_path / RENDER_DIRNAME)


def test_nothing_generated_leaves_the_environment_untouched(tmp_path: Path, monkeypatch) -> None:
    """A build of only checked-in cores must not repoint the provider's
    search at an empty directory."""
    monkeypatch.delenv(RENDER_ROOT_ENV, raising=False)
    render_generated_cores([_CheckedInCore("plain")], root=tmp_path)
    import os

    assert RENDER_ROOT_ENV not in os.environ


def test_a_provider_that_cannot_render_is_refused(tmp_path: Path) -> None:
    """Declaring a generator the installed provider cannot render must fail
    loudly, not silently synthesize whatever file happens to be there."""
    with pytest.raises(BuildStepError, match="too old for generated cores"):
        render_generated_cores([_GeneratorWithoutRender("c")], root=tmp_path)


def test_multiple_generated_cores_all_render(tmp_path: Path) -> None:
    cores = [_GeneratedCore(f"c{i}", f"c{i}.sv", f"// {i}\n") for i in range(3)]
    written = render_generated_cores(cores, root=tmp_path)
    assert len(written) == 3
    assert {p.name for p in written} == {"c0.sv", "c1.sv", "c2.sv"}
