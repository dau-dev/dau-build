"""Platform definitions: boards as data.

A ``PlatformDefinition`` captures what a build needs to know about a target
board that is not the design itself — the FPGA part, the host link, the
memory system, constraints, the programming method — plus a resource
budget in the same units as dau-core's ``ResourceEnvelope`` (LUT/FF/BRAM36/
DSP). ``fits()`` checks a composition's estimated resources against that
budget before the design is built.

The resource input to ``fits()`` is duck-typed (any object exposing
``lut``/``ff``/``bram36``/``dsp`` — dau-core's ``ResourceEnvelope`` does)
so this public module never imports the private core, respecting the
public/private wall.

This is the schema (roadmap P0.1). Reconciling dpv1's authoritative values
(the full XDMA personality map, constraint files) and resolving them
through a hydra config group is P0.2 — ``dpv1_platform()`` here is a
representative instance for the schema, not yet the build's source of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping, Protocol

_PCIE_LANE_WIDTHS = (1, 2, 4, 8, 16)


class PlatformError(ValueError):
    pass


@dataclass(frozen=True)
class ResourceBudget:
    """Placeable capacity of a platform, in ``ResourceEnvelope`` units."""

    lut: int
    ff: int
    bram36: float
    dsp: int

    def __post_init__(self) -> None:
        for name in ("lut", "ff", "bram36", "dsp"):
            if getattr(self, name) <= 0:
                raise PlatformError(f"resource budget {name} must be positive")


@dataclass(frozen=True)
class HostLink:
    """The host interface — PCIe/XDMA for the dpv1/dpv2 boards. The
    personality map is the set of XCI user parameters the shell's endpoint
    is customized with (empty until P0.2 populates it)."""

    interface: str
    pcie_lanes: int
    xdma_personality: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.interface:
            raise PlatformError("host link interface must be non-empty")
        if self.pcie_lanes not in _PCIE_LANE_WIDTHS:
            raise PlatformError(f"pcie_lanes must be one of {_PCIE_LANE_WIDTHS}, got {self.pcie_lanes}")


@dataclass(frozen=True)
class PlatformMemory:
    """The device-side memory a design stages through."""

    kind: str
    size_bytes: int
    mig_prj: str | None = None
    bandwidth_bytes_per_s: int | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise PlatformError("memory kind must be non-empty")
        if self.size_bytes <= 0:
            raise PlatformError("memory size_bytes must be positive")
        if self.bandwidth_bytes_per_s is not None and self.bandwidth_bytes_per_s <= 0:
            raise PlatformError("memory bandwidth_bytes_per_s must be positive when set")


@dataclass(frozen=True)
class PlatformDefinition:
    """One target board as data."""

    name: str
    part: str
    budget: ResourceBudget
    host_link: HostLink
    memory: PlatformMemory
    constraints: tuple[str, ...] = ()
    program_method: str = "jtag"

    def __post_init__(self) -> None:
        if not self.name:
            raise PlatformError("platform name must be non-empty")
        if not self.part:
            raise PlatformError("platform part must be non-empty")
        if self.program_method not in ("jtag", "flash"):
            raise PlatformError(f"program_method must be 'jtag' or 'flash', got {self.program_method!r}")

    def to_dict(self) -> dict:
        """Plain nested dict/list form (yaml/hydra friendly)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> PlatformDefinition:
        """Reconstruct from a plain mapping (the inverse of ``to_dict``),
        validating every field through the dataclass constructors."""
        try:
            host = data["host_link"]
            return cls(
                name=data["name"],
                part=data["part"],
                budget=ResourceBudget(**data["budget"]),
                host_link=HostLink(
                    interface=host["interface"],
                    pcie_lanes=host["pcie_lanes"],
                    xdma_personality=dict(host.get("xdma_personality") or {}),
                ),
                memory=PlatformMemory(**data["memory"]),
                constraints=tuple(data.get("constraints") or ()),
                program_method=data.get("program_method", "jtag"),
            )
        except (KeyError, TypeError) as exc:
            raise PlatformError(f"malformed platform definition: {exc}") from exc


class ResourceUse(Protocol):
    """Anything carrying a placed-resource count — dau-core's
    ``ResourceEnvelope`` (and ``estimated_resources(spec)``) satisfies it."""

    lut: int
    ff: int
    bram36: float
    dsp: int


@dataclass(frozen=True)
class FitReport:
    """Whether a design's resources fit a platform, with per-resource
    detail. ``headroom`` is ``budget - used`` (negative where over);
    ``utilization`` is ``used / budget``."""

    fits: bool
    headroom: Mapping[str, float]
    utilization: Mapping[str, float]


def fits(used: ResourceUse, platform: PlatformDefinition) -> FitReport:
    """Check placed/estimated resources against a platform's budget."""
    budget = platform.budget
    budget_by = {"lut": budget.lut, "ff": budget.ff, "bram36": budget.bram36, "dsp": budget.dsp}
    used_by = {"lut": used.lut, "ff": used.ff, "bram36": used.bram36, "dsp": used.dsp}
    headroom = {key: budget_by[key] - used_by[key] for key in budget_by}
    utilization = {key: used_by[key] / budget_by[key] for key in budget_by}
    return FitReport(fits=all(value >= 0 for value in headroom.values()), headroom=headroom, utilization=utilization)


def dpv1_platform() -> PlatformDefinition:
    """Representative dpv1 (NiteFury XC7A200T) platform — the P0.1 schema
    example. The XDMA personality map and constraint files are populated in
    P0.2 from ``dau_build.dpv1_shell.DPV1_XDMA_PERSONALITY``; until then this
    is a schema demonstration, not the authoritative build source.

    Budget: XC7A200T-2FBG484 (134,600 LUT / 269,200 FF / 365 BRAM36 / 740
    DSP48E1). Memory: 1 GiB DDR3-800 (~1.6 GB/s). Link: XDMA PCIe Gen2 x4."""
    return PlatformDefinition(
        name="dpv1",
        part="xc7a200tfbg484-2",
        budget=ResourceBudget(lut=134600, ff=269200, bram36=365, dsp=740),
        host_link=HostLink(interface="pcie-xdma", pcie_lanes=4),
        memory=PlatformMemory(kind="ddr3", size_bytes=1 << 30, mig_prj="dpv1_mig.prj", bandwidth_bytes_per_s=1_600_000_000),
        program_method="jtag",
    )
