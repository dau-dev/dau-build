from __future__ import annotations

from pathlib import Path

import pytest
from ccflow import BaseModel
from pydantic import ValidationError

from dau_build.build_config import (
    BackendConfig,
    BoardConfig,
    MemoryConfig,
    OperatorConfig,
    ResolvedBuildConfig,
    resolve_build_config,
)
from dau_build.build_spec import load_dau_build_spec

_EXAMPLE_SPEC = Path(__file__).resolve().parents[2] / "examples" / "identity" / "dau-build.yaml"


def test_config_models_are_pydantic() -> None:
    for model in (BoardConfig, BackendConfig, MemoryConfig, OperatorConfig):
        assert issubclass(model, BaseModel)


def test_config_model_round_trips() -> None:
    board = BoardConfig(name="b", platform="vivado-xdma", shell="xdma-ddr")
    assert BoardConfig.model_validate(board.model_dump()) == board


def test_memory_config_rejects_negative() -> None:
    with pytest.raises(ValidationError, match="host_staging_bytes cannot be negative"):
        MemoryConfig(host_staging_bytes=-1)
    with pytest.raises(ValidationError, match="device_staging_bytes cannot be negative"):
        MemoryConfig(device_staging_bytes=-1)
    assert MemoryConfig().host_staging_bytes == 0  # defaults are valid


@pytest.mark.skipif(not _EXAMPLE_SPEC.is_file(), reason="example spec not present")
def test_resolve_build_config_is_a_view_over_the_spec() -> None:
    spec = load_dau_build_spec(_EXAMPLE_SPEC)
    resolved = resolve_build_config(spec)
    assert isinstance(resolved, ResolvedBuildConfig)
    assert isinstance(resolved.board, BoardConfig) and isinstance(resolved.memory, MemoryConfig)
    # board/operators/backend derive from the spec (no bespoke override dict)
    assert resolved.board.platform == spec.platform and resolved.board.shell == spec.shell
    assert resolved.backend.name == spec.backend
    assert resolved.operators.names == spec.operators
    assert resolved.to_text().splitlines()[0] == "dau-build-resolved-config"
    # a task may select the synthesis engine
    assert resolve_build_config(spec, backend_name="vivado").backend.name == "vivado"
