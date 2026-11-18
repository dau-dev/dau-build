from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ccflow import BaseModel
from pydantic import ConfigDict, field_validator

from dau_build.build_spec import DauBuildSpec


class BoardConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    platform: str
    shell: str


class HostConfig(BaseModel):
    """Where the build host keeps its source checkouts — the ``host`` hydra
    config group. Tasks and plans that need a checkout root interpolate from
    the composed host (``host=hosts/<name>``, registered from a search-path
    package or ``--config-dir`` overlay) instead of demanding the paths as
    first-class required fields; a direct ``<field>=...`` override still
    wins."""

    model_config = ConfigDict(frozen=True)

    name: str = "local"
    dau_core_root: Path | None = None
    dau_driver_root: Path | None = None
    dau_utils_root: Path | None = None


class BackendConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    invocation: str


class BackendLabel(Protocol):
    """What the resolved config reports of a composed backend: a
    ``BackendConfig`` or a synthesis engine model both carry these."""

    @property
    def name(self) -> str: ...

    @property
    def invocation(self) -> str: ...


class DriverConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    os: str
    transport: str


class OperatorConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    set_name: str
    names: tuple[str, ...]


class MemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    host_staging_bytes: int = 0
    device_staging_bytes: int = 0

    @field_validator("host_staging_bytes", "device_staging_bytes")
    @classmethod
    def _non_negative(cls, value: int, info) -> int:
        if value < 0:
            raise ValueError(f"{info.field_name} cannot be negative")
        return value


class ResolvedBuildConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    spec: DauBuildSpec
    board: BoardConfig
    backend: BackendConfig
    # the host driver, when a `driver=` group was composed; the spec names none
    driver: DriverConfig | None
    operators: OperatorConfig
    memory: MemoryConfig

    @classmethod
    def from_spec(
        cls,
        spec: DauBuildSpec,
        *,
        board: BoardConfig | None = None,
        backend: BackendLabel | None = None,
        backend_name: str | None = None,
        driver: DriverConfig | None = None,
        memory: MemoryConfig | None = None,
    ) -> ResolvedBuildConfig:
        """The build config as a view over the spec. Board and operators
        derive from the spec. A composed ``board=`` group must name the
        spec's platform and shell: the generated top and its manifest follow
        the spec, so a board that disagrees is refused rather than used for
        the backend handoff. ``driver=`` is reported only when composed (the
        spec names no driver). ``backend_name`` (e.g. a task's synthesis
        engine) selects the backend when no ``backend=`` group is given.

        A composed backend may be a synthesis *engine* model (carrying extra
        fields like a yosys frontend); it is normalized here to the ``name`` +
        ``invocation`` label the resolved config reports."""
        if board is not None and (board.platform, board.shell) != (spec.platform, spec.shell):
            raise ValueError(
                f"board {board.name!r} is platform={board.platform} shell={board.shell}, "
                f"but spec {spec.name!r} builds for platform={spec.platform} shell={spec.shell}"
            )
        return cls(
            spec=spec,
            board=board or BoardConfig(name=spec.platform, platform=spec.platform, shell=spec.shell),
            backend=_backend_label(backend, backend_name),
            driver=driver,
            operators=OperatorConfig(set_name="spec", names=spec.operators),
            memory=memory or MemoryConfig(),
        )

    def to_text(self) -> str:
        return "\n".join(
            (
                "dau-build-resolved-config",
                f"board\tname={self.board.name} platform={self.board.platform} shell={self.board.shell}",
                f"backend\tname={self.backend.name} invocation={self.backend.invocation}",
                "driver\tnone" if self.driver is None else f"driver\tos={self.driver.os} transport={self.driver.transport}",
                f"operators\tset={self.operators.set_name} names={','.join(self.operators.names)}",
                f"memory\thost_staging_bytes={self.memory.host_staging_bytes} device_staging_bytes={self.memory.device_staging_bytes}",
            )
        )


def _backend_label(backend: BackendLabel | None, backend_name: str | None) -> BackendConfig:
    """Normalize a composed backend (a BackendConfig or a synthesis engine
    model with extra fields) to the name+invocation label the resolved config
    reports. With no ``backend=`` group the label is ``backend_name`` (a
    task's synthesis engine) or the dry-run ``none``; the spec names no
    backend."""
    if isinstance(backend, BackendConfig):
        return backend
    if backend is not None:
        return BackendConfig(name=backend.name, invocation=backend.invocation)
    return BackendConfig(name=backend_name or "none", invocation="dry-run")
