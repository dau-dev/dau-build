from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dau_build.build_spec import DauBuildSpec


@dataclass(frozen=True)
class BoardConfig:
    name: str
    platform: str
    shell: str


@dataclass(frozen=True)
class BackendConfig:
    name: str
    invocation: str


@dataclass(frozen=True)
class DriverConfig:
    os: str
    transport: str


@dataclass(frozen=True)
class OperatorConfig:
    set_name: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class MemoryConfig:
    host_staging_bytes: int
    device_staging_bytes: int


@dataclass(frozen=True)
class ResolvedBuildConfig:
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
        value = int(raw, 0)
    except ValueError as exc:
        raise ValueError(f"override {key} must be an integer") from exc
    if value < 0:
        raise ValueError(f"override {key} cannot be negative")
    return value
