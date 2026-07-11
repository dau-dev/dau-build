"""Platform definitions: boards as data.

A ``PlatformDefinition`` captures what a build needs to know about a target
board that is not the design itself — the FPGA part, the host link (with its
XDMA personality), the memory system, constraints, the programming method —
plus a resource budget in the same units as dau-core's ``ResourceEnvelope``
(LUT/FF/BRAM36/DSP). ``fits()`` checks a composition's estimated resources
against that budget before the design is built.

These are ``ccflow.BaseModel`` (pydantic) models, selected and composed
through the ``platform`` hydra config group like the task tree — validation,
serialization, and CLI field overrides come from pydantic, not hand-rolled.

The resource input to ``fits()`` is duck-typed (any object exposing
``lut``/``ff``/``bram36``/``dsp`` — dau-core's ``ResourceEnvelope`` does)
so this public module never imports the private core, respecting the
public/private wall.
"""

from __future__ import annotations

from typing import Protocol

from ccflow import BaseModel
from pydantic import Field, field_validator

_PCIE_LANE_WIDTHS = (1, 2, 4, 8, 16)


class XdmaPersonality(BaseModel):
    """The XDMA endpoint's complete set of ``value_src=user`` XCI
    parameters, applied verbatim — the dpv1 bring-up proved a hand-picked
    subset leaves the core memory-dead (BARs enumerate, reads all ones), so
    the personality is the *complete* customization. ``to_tcl_config``
    emits the Vivado ``CONFIG.*`` block the shell project Tcl is built with;
    keeping it here (not inline in the shell generator) makes it reusable
    across platforms."""

    params: dict[str, str] = Field(default_factory=dict)

    def to_tcl_config(self, *, indent: str = "    ") -> str:
        return " \\\n".join(f"{indent}CONFIG.{key} {{{value}}}" for key, value in self.params.items())

    def link_width(self) -> int:
        """PCIe lane count from ``pl_link_cap_max_link_width`` (``"X4"`` →
        ``4``) — one source for the endpoint's lane configuration."""
        width = self.params.get("pl_link_cap_max_link_width", "")
        if not width.upper().startswith("X") or not width[1:].isdigit():
            raise ValueError(f"cannot parse pcie lane width from personality: {width!r}")
        return int(width[1:])


class ResourceBudget(BaseModel):
    """Placeable capacity of a platform, in ``ResourceEnvelope`` units."""

    lut: int
    ff: int
    bram36: float
    dsp: int

    @field_validator("lut", "ff", "bram36", "dsp")
    @classmethod
    def _positive(cls, value: float, info) -> float:
        if value <= 0:
            raise ValueError(f"resource budget {info.field_name} must be positive")
        return value


class HostLink(BaseModel):
    """The host interface — PCIe/XDMA for the dpv1/dpv2 boards."""

    interface: str
    pcie_lanes: int
    xdma_personality: XdmaPersonality = Field(default_factory=XdmaPersonality)

    @field_validator("interface")
    @classmethod
    def _interface_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("host link interface must be non-empty")
        return value

    @field_validator("pcie_lanes")
    @classmethod
    def _valid_lane_width(cls, value: int) -> int:
        if value not in _PCIE_LANE_WIDTHS:
            raise ValueError(f"pcie_lanes must be one of {_PCIE_LANE_WIDTHS}, got {value}")
        return value


class PlatformMemory(BaseModel):
    """The device-side memory a design stages through."""

    kind: str
    size_bytes: int
    mig_prj: str | None = None
    bandwidth_bytes_per_s: int | None = None

    @field_validator("kind")
    @classmethod
    def _kind_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("memory kind must be non-empty")
        return value

    @field_validator("size_bytes")
    @classmethod
    def _size_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("memory size_bytes must be positive")
        return value

    @field_validator("bandwidth_bytes_per_s")
    @classmethod
    def _bandwidth_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("memory bandwidth_bytes_per_s must be positive when set")
        return value


class PlatformDefinition(BaseModel):
    """One target board as data."""

    name: str
    part: str
    budget: ResourceBudget
    host_link: HostLink
    memory: PlatformMemory
    constraints: tuple[str, ...] = ()
    program_method: str = "jtag"

    @field_validator("name", "part")
    @classmethod
    def _nonempty(cls, value: str, info) -> str:
        if not value:
            raise ValueError(f"platform {info.field_name} must be non-empty")
        return value

    @field_validator("program_method")
    @classmethod
    def _valid_program_method(cls, value: str) -> str:
        if value not in ("jtag", "flash"):
            raise ValueError(f"program_method must be 'jtag' or 'flash', got {value!r}")
        return value


class ResourceUse(Protocol):
    """Anything carrying a placed-resource count — dau-core's
    ``ResourceEnvelope`` (and ``estimated_resources(spec)``) satisfies it."""

    lut: int
    ff: int
    bram36: float
    dsp: int


class FitReport(BaseModel):
    """Whether a design's resources fit a platform, with per-resource
    detail. ``headroom`` is ``budget - used`` (negative where over);
    ``utilization`` is ``used / budget``."""

    fits: bool
    headroom: dict[str, float]
    utilization: dict[str, float]


def fits(used: ResourceUse, platform: PlatformDefinition) -> FitReport:
    """Check placed/estimated resources against a platform's budget."""
    budget = platform.budget
    budget_by = {"lut": budget.lut, "ff": budget.ff, "bram36": budget.bram36, "dsp": budget.dsp}
    used_by = {"lut": used.lut, "ff": used.ff, "bram36": used.bram36, "dsp": used.dsp}
    headroom = {key: budget_by[key] - used_by[key] for key in budget_by}
    utilization = {key: used_by[key] / budget_by[key] for key in budget_by}
    return FitReport(fits=all(value >= 0 for value in headroom.values()), headroom=headroom, utilization=utilization)


def dpv1_platform() -> PlatformDefinition:
    """The dpv1 (NiteFury XC7A200T) platform, resolved from its config
    (``config/platform/dpv1.yaml``) — the single source for the part,
    budget, memory, and the 47-parameter XDMA personality the shell builds
    with. A convenience wrapper over ``resolve_platform("dpv1")``."""
    from dau_build.config import resolve_platform

    return resolve_platform("dpv1")
