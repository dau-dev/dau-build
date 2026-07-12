from __future__ import annotations

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

    @classmethod
    def from_spec(
        cls,
        spec: DauBuildSpec,
        *,
        board: BoardConfig | None = None,
        backend: BackendConfig | None = None,
        backend_name: str | None = None,
    ) -> ResolvedBuildConfig:
        """The build config as a view over the spec. Board, driver,
        operators, and memory derive from the spec directly. A composed
        ``board=``/``backend=`` Hydra group wins over the spec-derived
        default when provided; ``backend_name`` (e.g. a task's synthesis
        engine) selects the backend when no ``backend=`` group is given."""
        return cls(
            spec=spec,
            board=board or BoardConfig(name=spec.platform, platform=spec.platform, shell=spec.shell),
            backend=backend or BackendConfig(name=backend_name or spec.backend, invocation="dry-run"),
            driver=DriverConfig(os="host", transport="xdma"),
            operators=OperatorConfig(set_name="spec", names=spec.operators),
            memory=MemoryConfig(),
        )

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
