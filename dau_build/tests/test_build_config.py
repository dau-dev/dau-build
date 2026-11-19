from __future__ import annotations

import pytest
from ccflow import BaseModel
from pydantic import ValidationError

from dau_build.build_config import (
    BackendConfig,
    MemoryConfig,
    OperatorConfig,
    ResolvedBuildConfig,
)


def test_config_models_are_pydantic() -> None:
    for model in (BackendConfig, MemoryConfig, OperatorConfig):
        assert issubclass(model, BaseModel)


def test_config_model_round_trips() -> None:
    backend = BackendConfig(name="vivado", invocation="standard")
    assert BackendConfig.model_validate(backend.model_dump()) == backend


def test_memory_config_rejects_negative() -> None:
    with pytest.raises(ValidationError, match="host_staging_bytes cannot be negative"):
        MemoryConfig(host_staging_bytes=-1)
    with pytest.raises(ValidationError, match="device_staging_bytes cannot be negative"):
        MemoryConfig(device_staging_bytes=-1)
    assert MemoryConfig().host_staging_bytes == 0  # defaults are valid


def test_resolve_build_config_is_a_view_over_the_spec() -> None:
    spec = _identity_spec().resolve()
    resolved = ResolvedBuildConfig.from_spec(spec)
    assert isinstance(resolved, ResolvedBuildConfig)
    assert isinstance(resolved.memory, MemoryConfig)
    # labels and operators come from the spec; the backend is only ever the
    # composed group or a task's engine, never a spec field
    assert f"spec\tname={spec.name} platform={spec.platform} shell={spec.shell}" in resolved.to_text().splitlines()
    assert resolved.backend.name == "none"
    assert resolved.operators.names == spec.operators
    assert resolved.to_text().splitlines()[0] == "dau-build-resolved-config"
    # a task may select the synthesis engine
    assert ResolvedBuildConfig.from_spec(spec, backend_name="vivado").backend.name == "vivado"


def _identity_spec():
    """The packaged ``spec=specs/identity`` option, composed as the CLI would."""
    from hydra.utils import instantiate

    from dau_build.config import compose_config

    return instantiate(compose_config(["spec=specs/identity"]).cfg.spec)


def test_the_driver_is_reported_only_when_composed() -> None:
    spec = _identity_spec().resolve()
    resolved = ResolvedBuildConfig.from_spec(spec)
    assert resolved.driver is None
    assert "driver\tnone" in resolved.to_text().splitlines()


def test_composed_build_config_models_are_frozen() -> None:
    backend = BackendConfig(name="vivado", invocation="standard")
    with pytest.raises(ValidationError):
        backend.name = "other"  # ty: ignore[invalid-assignment]
    memory = MemoryConfig()
    with pytest.raises(ValidationError):
        memory.host_staging_bytes = 1  # ty: ignore[invalid-assignment]
