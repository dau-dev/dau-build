"""Core entries are ``/<group>/<name>`` registry paths, resolved from the
groups that were composed. These tests use stand-in definitions, so they
run without a core provider installed."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pytest
from ccflow import BaseModel, ModelRegistry
from omegaconf import OmegaConf

from dau_build.build_steps import BuildStepError
from dau_build.synthesize_cores import core_registries, core_sources, lookup_core, resolve_core_definition


class _Kind(Enum):
    OPERATOR = "operator"
    PACKAGE = "package"


class _Core(BaseModel):
    name: str
    kind: _Kind = _Kind.OPERATOR
    depends: tuple[str, ...] = ()

    def source_path(self, *, rendered_into: Path) -> Path:  # the provider's signature
        return Path(f"{self.name}.sv")


def _entry(name: str, **fields) -> dict:
    return {"_target_": f"{__name__}._Core", "name": name, **fields}


@pytest.fixture
def composed():
    """A registry holding a stock palette and two other packages' groups."""
    root = ModelRegistry.root()
    saved = dict(root.models)
    root.clear()
    root.load_config(
        OmegaConf.create(
            {
                "dau-core": {"stock-pkg": _entry("stock-pkg", kind="package"), "stock-alu": _entry("stock-alu", depends=["stock-pkg"])},
                "vendor": {"vendor-alu": _entry("vendor-alu", depends=["stock-alu", "vendor-helper"]), "vendor-helper": _entry("vendor-helper")},
                # an unrelated composed group that happens to reuse a core's name
                "boards": {"vendor-alu": _entry("vendor-alu")},
            }
        )
    )
    yield root
    root.clear()
    for name, group in saved.items():
        root.add(name, group)


def test_a_core_resolves_from_the_group_its_path_names(composed) -> None:
    assert resolve_core_definition("/dau-core/stock-alu") is composed.models["dau-core"].models["stock-alu"]
    assert resolve_core_definition("/vendor/vendor-alu") is composed.models["vendor"].models["vendor-alu"]
    assert resolve_core_definition("/boards/vendor-alu") is composed.models["boards"].models["vendor-alu"]


def test_a_path_that_names_nothing_is_refused_with_the_reason(composed) -> None:
    with pytest.raises(BuildStepError, match="unknown core '/vendor/absent'"):
        resolve_core_definition("/vendor/absent")
    with pytest.raises(BuildStepError, match=r"the group 'elsewhere', which is not composed; select it, for example \+elsewhere=cores"):
        resolve_core_definition("/elsewhere/vendor-alu")
    for malformed in ("vendor-alu", "/vendor", "/vendor/nested/vendor-alu", "vendor/vendor-alu"):
        with pytest.raises(BuildStepError, match="not a /<group>/<name> registry path"):
            resolve_core_definition(malformed)
    with pytest.raises(BuildStepError, match="is a package, not a synthesizable top"):
        resolve_core_definition("/dau-core/stock-pkg")


def test_a_bare_name_is_looked_up_in_the_stock_palette_first(composed) -> None:
    assert lookup_core("stock-alu") is composed.models["dau-core"].models["stock-alu"]
    assert lookup_core("vendor-helper") is composed.models["vendor"].models["vendor-helper"]
    assert lookup_core("absent") is None


def test_sources_close_over_dependencies_in_other_groups_stock_first(composed, tmp_path: Path) -> None:
    definition = resolve_core_definition("/vendor/vendor-alu")
    sources = core_sources(definition, core_registries(), root=tmp_path)
    # the stock dependencies first, then the package's own; the unrelated
    # group's entry of the same name is not a second source
    assert [path.name for path in sources] == ["stock-pkg.sv", "stock-alu.sv", "vendor-alu.sv", "vendor-helper.sv"]
    # only the registries passed are searched
    with pytest.raises(BuildStepError, match="'vendor-alu' is a declared dependency but is not in the registry"):
        core_sources(definition, (composed.models["dau-core"],), root=tmp_path)
