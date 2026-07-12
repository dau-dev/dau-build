from __future__ import annotations

from collections.abc import Mapping

from ccflow import BaseModel
from pydantic import field_validator

from dau_build.build_spec import DauBuildSpec


class BoardConfig(BaseModel):
    name: str
    platform: str
    shell: str


class BackendConfig(BaseModel):
    name: str
    invocation: str


class DriverConfig(BaseModel):
    os: str
    transport: str


class OperatorConfig(BaseModel):
    set_name: str
    names: tuple[str, ...]


class MemoryConfig(BaseModel):
    host_staging_bytes: int = 0
    device_staging_bytes: int = 0

    @field_validator("host_staging_bytes", "device_staging_bytes")
    @classmethod
    def _non_negative(cls, value: int, info) -> int:
        if value < 0:
            raise ValueError(f"{info.field_name} cannot be negative")
        return value


class ResolvedBuildConfig(BaseModel):
    spec: DauBuildSpec
    board: BoardConfig
    backend: BackendConfig
    driver: DriverConfig
    operators: OperatorConfig
    memory: MemoryConfig

    def to_text(self) -> str:
        return "\n".join(
            (
                "dau-build-resolved-config",
                f"board\tname={self.board.name} platform={self.board.platform} shell={self.board.shell}",
                f"backend\tname={self.backend.name} invocation={self.backend.invocation}",
                f"driver\tos={self.driver.os} transport={self.driver.transport}",
                f"operators\tset={self.operators.set_name} names={','.join(self.operators.names)}",
                f"memory\thost_staging_bytes={self.memory.host_staging_bytes} device_staging_bytes={self.memory.device_staging_bytes}",
            )
        )


def resolve_build_config(spec: DauBuildSpec, overrides: Mapping[str, str]) -> ResolvedBuildConfig:
    return ResolvedBuildConfig(
        spec=spec,
        board=BoardConfig(
            name=overrides.get("board.name", spec.platform),
            platform=overrides.get("board.platform", spec.platform),
            shell=overrides.get("board.shell", spec.shell),
        ),
        backend=BackendConfig(
            name=overrides.get("backend.name", spec.backend),
            invocation=overrides.get("backend.invocation", "dry-run"),
        ),
        driver=DriverConfig(
            os=overrides.get("driver.os", "host"),
            transport=overrides.get("driver.transport", "xdma"),
        ),
        operators=OperatorConfig(
            set_name=overrides.get("operator.set", "spec"),
            names=spec.operators,
        ),
        memory=MemoryConfig(
            host_staging_bytes=_int_override(overrides, "memory.host_staging_bytes", 0),
            device_staging_bytes=_int_override(overrides, "memory.device_staging_bytes", 0),
        ),
    )


def _int_override(overrides: Mapping[str, str], key: str, default: int) -> int:
    raw = overrides.get(key)
    if raw is None:
        return default
    try:
        return int(raw, 0)
    except ValueError as exc:
        raise ValueError(f"override {key} must be an integer") from exc
