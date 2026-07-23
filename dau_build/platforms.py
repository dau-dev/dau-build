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

from typing import Literal, Protocol

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

    def axi_clock_mhz(self) -> int:
        """The XDMA ``axi_aclk`` frequency in MHz from ``axisten_freq`` —
        the clock the endpoint presents its AXI interfaces on (125 on dpv1's
        Gen2 x4 personality; the IP forces 250 at Gen2 x8/128-bit)."""
        freq = self.params.get("axisten_freq", "")
        if not freq.isdigit():
            raise ValueError(f"cannot parse axi clock from personality axisten_freq: {freq!r}")
        return int(freq)


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
    """The host interface — PCIe/XDMA for the dpv1/dpv2 boards.

    ``pcie_lanes`` is the endpoint's electrical width;
    ``expected_link_width``/``expected_link_speed_gts`` are what the link
    actually trains at on the proven host (dpv1 negotiates x2 at 5.0 GT/s
    over Thunderbolt despite the X4 personality) — bring-up checks compare
    against these, not the electrical maximum."""

    interface: str
    pcie_lanes: int
    xdma_personality: XdmaPersonality = Field(default_factory=XdmaPersonality)
    expected_link_width: int | None = None
    expected_link_speed_gts: float | None = None

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

    @field_validator("expected_link_width")
    @classmethod
    def _valid_expected_width(cls, value: int | None) -> int | None:
        if value is not None and value not in _PCIE_LANE_WIDTHS:
            raise ValueError(f"expected_link_width must be one of {_PCIE_LANE_WIDTHS}, got {value}")
        return value

    @field_validator("expected_link_speed_gts")
    @classmethod
    def _valid_expected_speed(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("expected_link_speed_gts must be positive when set")
        return value


class PlatformMemory(BaseModel):
    """The device-side memory a design stages through. ``constraints_xdc``
    carries the memory system's additions to the board pin constraints
    (IOSTANDARDs for the controller reference clock, calibration LEDs —
    pin placement stays in the MIG ``.prj``)."""

    kind: str
    size_bytes: int
    mig_prj: str | None = None
    bandwidth_bytes_per_s: int | None = None
    constraints_xdc: str = ""

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


class HostAccess(BaseModel):
    """How the bench host reaches the board: the PCI identity/topology the
    hardware plans probe and the programming cable. These are measured
    bench facts (BDFs, bridge rescan order, runtime-PM device patterns) —
    board/host configuration, not code defaults."""

    pci_id: str
    endpoint_bdf: str
    # explicit, not defaulted: an empty tuple is meaningful (global rescan
    # only / no runtime-PM holds), so each board states its own values
    rescan_bdfs: tuple[str, ...]
    runtime_pm_patterns: tuple[str, ...]
    runtime_pm_executable: str = "dau-utils-pci-runtime-pm"
    jtag_cable: str = "digilent_hs2"
    # the endpoint's direct upstream bridge, for the secondary-bus reset
    # (PERST#-equivalent) that re-inits the PCIe core after a volatile
    # reprogram; a measured bench fact, explicit rather than inferred from
    # rescan_bdfs order
    reset_bridge_bdf: str | None = None

    @field_validator("pci_id", "endpoint_bdf")
    @classmethod
    def _nonempty(cls, value: str, info) -> str:
        if not value:
            raise ValueError(f"host access {info.field_name} must be non-empty")
        return value


class PlatformDefinition(BaseModel):
    """One target board as data.

    ``constraints_xdc`` is the board pin-constraint text (the shell project
    generators prepend their banner); ``lane_placements`` is the GT lane
    swizzle applied as a pre-``opt_design`` implementation hook (empty means
    the board needs none). ``placeholders`` names hardware-derived values
    that have *not* been measured on the board yet (e.g.
    ``host_link.xdma_personality`` before the XCI delta is audited) —
    ``require_measured`` refuses such boards for real builds while config-only
    generation stays open. ``host_access`` carries the bench host's measured
    access facts (PCI identity, endpoint/bridge BDFs, runtime-PM patterns,
    JTAG cable) that ``HardwareToolchainConfig.for_platform`` composes from.

    ``job_clock_mhz`` decouples the job logic's clock from the XDMA's
    ``axi_aclk``: when set, the shell generators derive a job clock at that
    frequency from ``axi_aclk`` (MMCM) and clock-convert both the register
    aperture and the memory path in the smartconnects, so a personality
    that forces a faster ``axi_aclk`` (250 MHz at Gen2 x8) never drags the
    proven job-logic timing closure with it. ``None`` (the dpv1 default)
    keeps the job logic on ``axi_aclk`` unchanged."""

    name: str
    part: str
    budget: ResourceBudget
    host_link: HostLink
    memory: PlatformMemory
    host_access: HostAccess | None = None
    constraints: tuple[str, ...] = ()
    constraints_xdc: str = ""
    lane_placements: tuple[tuple[int, str], ...] = ()
    program_method: str = "jtag"
    # the flash device's boot bus width when the board self-configures from
    # SPI (e.g. 4 for an SPIx4 part): a raw-bit JTAG `-f` write to such a
    # flash leaves the board memory-dead — persistent programming must go
    # through the vivado cfgmem path. None = no SPI-boot constraint. Only 4
    # is supported: the cfgmem generator (flash.tcl / vivado_build_tcl)
    # writes SPIx4; another width would need its own cfgmem-generation path.
    spi_boot_buswidth: Literal[4] | None = None
    job_clock_mhz: int | None = None
    placeholders: tuple[str, ...] = ()

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

    @field_validator("job_clock_mhz")
    @classmethod
    def _valid_job_clock(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("job_clock_mhz must be positive when set")
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


class PlaceholderPlatformError(ValueError):
    """Raised when a platform whose hardware-derived values are still
    placeholders is used for a real build."""


def require_measured(platform: PlatformDefinition) -> None:
    """Refuse a placeholder board for hardware-affecting work: raises
    ``PlaceholderPlatformError`` naming the unmeasured values. Config-only
    generation does not call this — a placeholder board may generate a
    project, never build or program one."""
    if platform.placeholders:
        raise PlaceholderPlatformError(
            f"platform {platform.name!r} carries placeholder values ({', '.join(platform.placeholders)}); "
            "measure them on hardware and clear `placeholders` before building"
        )


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
    with. A convenience wrapper over ``resolve_platform("platforms/dau/dpv1")``."""
    from dau_build.config import resolve_platform

    return resolve_platform("platforms/dau/dpv1")
