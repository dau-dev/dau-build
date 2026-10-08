"""Build specs for tests, the way users supply them: as the ``spec=`` config
group. ``write_spec_option`` puts a spec mapping into a per-test
``--config-dir`` overlay as ``spec=specs/<stem>``; ``spec_from_text`` builds
the ``BuildSpec`` model directly for unit tests of the spec itself."""

from __future__ import annotations

from pathlib import Path

import yaml

SPEC_TARGET = "dau_build.build_spec.BuildSpec"


def write_spec_option(tmp_path: Path, text: str, *, stem: str = "spec", base_dir: Path | None = None) -> tuple[str, str]:
    """Write ``text`` (a BuildSpec mapping in yaml, paths relative to
    ``base_dir``, default ``tmp_path``) as a Hydra spec option in
    ``<tmp_path>/config``. Returns ``(config_dir, "spec=specs/<stem>")``."""
    option = tmp_path / "config" / "spec" / "specs" / f"{stem}.yaml"
    option.parent.mkdir(parents=True, exist_ok=True)
    option.write_text(f"# @package spec\n\n_target_: {SPEC_TARGET}\nbase_dir: {(base_dir or tmp_path).as_posix()}\n{text}", encoding="utf-8")
    return str(tmp_path / "config"), f"spec=specs/{stem}"


def spec_from_text(text: str, base_dir: Path):
    """The ``BuildSpec`` a spec option with this mapping composes to."""
    from dau_build.build_spec import BuildSpec

    return BuildSpec.model_validate({**yaml.safe_load(text), "base_dir": base_dir})
