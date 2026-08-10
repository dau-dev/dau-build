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

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Protocol

from ccflow import BaseModel
from pydantic import ConfigDict, Field, field_serializer, field_validator, model_validator

_PCIE_LANE_WIDTHS = (1, 2, 4, 8, 16)


class XdmaPersonality(BaseModel):
    """The XDMA endpoint's complete set of ``value_src=user`` XCI
    parameters, applied verbatim — the dpv1 bring-up proved a hand-picked
    subset leaves the core memory-dead (BARs enumerate, reads all ones), so
    the personality is the *complete* customization. ``to_tcl_config``
    emits the Vivado ``CONFIG.*`` block the shell project Tcl is built with;
    keeping it here (not inline in the shell generator) makes it reusable
    across platforms."""

    model_config = ConfigDict(frozen=True)

    params: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))

    @field_validator("params")
    @classmethod
    def _freeze_params(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("params")
    def _serialize_params(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

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


StorageTechnology = Literal["bram", "ddr", "spi-flash", "nvme", "sata"]
StorageAccess = Literal["owned", "shared"]


class StorageTier(BaseModel):
    """One declared storage tier of a platform's inventory. ``owned`` tiers
    back private single-cycle arenas; ``shared`` tiers sit behind handle-based
    transactions. Far technologies are schema-legal and HDL-absent until their
    tripwire fires (STORAGE_AND_NOC.md). ``constraints_xdc`` carries the
    memory system's additions to the board pin constraints (IOSTANDARDs for
    the controller reference clock, calibration LEDs — pin placement stays in
    the MIG ``.prj``)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    technology: StorageTechnology
    capacity_bytes: int = Field(gt=0)
    access: StorageAccess
    persistent: bool = False
    bandwidth_bytes_per_s: int | None = Field(default=None, gt=0)
    mig_prj: str | None = None
    constraints_xdc: str = ""


class DdrStaging(BaseModel):
    """Where a host stages job data in a board's DDR: the input regions, the
    result region, and the alignment those addresses obey.

    These are BOARD facts, and they were previously host-side Python
    constants with an inline `memory_bytes > 1 << 32` conditional standing
    in for "this is the bigger board". A conditional is not configuration:
    it hides one board's layout inside another's code path, and the next
    board needs a second branch. Declaring them per platform is the same
    rule host_access already follows.

    Addresses are offsets into the tier this staging describes, so a board
    whose DDR is not at zero states its own base rather than relying on one.
    """

    model_config = ConfigDict(frozen=True)

    tier: str = "ddr"
    input_addresses: tuple[int, ...]
    input_bytes: int
    output_address: int
    output_bytes: int
    address_align: int

    @field_validator("input_addresses")
    @classmethod
    def _at_least_one_input(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("ddr staging needs at least one input region")
        return value

    def aligned(self) -> bool:
        addresses = (*self.input_addresses, self.output_address)
        return all(address % self.address_align == 0 for address in addresses)


class HostAccess(BaseModel):
    """How the bench host reaches the board: the PCI identity/topology the
    hardware plans probe and the programming cable. These are measured
    bench facts (BDFs, bridge rescan order, runtime-PM device patterns) —
    board/host configuration, not code defaults."""

    model_config = ConfigDict(frozen=True)

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
    # command prefix for the host-side PRIVILEGED PCIe steps (sysfs writes,
    # setpci, the runtime-PM hold), e.g. ("sudo",). The deadman self-escalates
    # (it invokes `sudo systemctl` itself) and the JTAG programmer runs
    # unprivileged, so this wraps ONLY the steps that write sysfs/config space.
    # Empty (the default) = already privileged, or an unprivileged host.
    privilege_prefix: tuple[str, ...] = ()

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

    model_config = ConfigDict(frozen=True)

    name: str
    part: str
    budget: ResourceBudget
    host_link: HostLink
    storage_tiers: tuple[StorageTier, ...] = ()
    # which shared tier a BUILD instantiates (a bitstream drives one memory
    # controller); None = the first shared tier
    active_memory_tier: str | None = None
    host_access: HostAccess | None = None
    # where the host stages job data in this board's DDR; unset means the
    # board has not declared one and a caller must not invent it
    ddr_staging: DdrStaging | None = None
    constraints: tuple[str, ...] = ()
    constraints_xdc: str = ""
    lane_placements: tuple[tuple[int, str], ...] = ()
    # the platform's on-wire identity word (1–4 ASCII bytes) advertised in the
    # PLATFORM_ID register: the shell generators encode it into the identity
    # block so first-power reads back the right platform. REQUIRED: a board
    # that forgot to state one used to become dpv1 silently, and the shell
    # would then stamp that identity into a device which is not that board.
    platform_id: str
    program_method: str = "jtag"
    # the flash device's boot bus width when the board self-configures from
    # SPI (e.g. 4 for an SPIx4 part): a raw-bit JTAG `-f` write to such a
    # flash leaves the board memory-dead — persistent programming must go
    # through the vivado cfgmem path. None = no SPI-boot constraint. Only 4
    # is supported: the cfgmem generator (flash.tcl / vivado_build_tcl)
    # writes SPIx4; another width would need its own cfgmem-generation path.
    spi_boot_buswidth: Literal[4] | None = None
    # the SPI configuration clock rate (MHz) baked into the bitstream for a
    # self-configuring board. It sets how long the board takes to come up
    # from flash, so it is a measured board fact rather than a tool default.
    spi_boot_configrate: int | None = None
    # the platform's supported memory-read width tiers (the precompiled
    # bitstream catalog dimension): each entry is a legal `width=` build
    # selection on this board. Bounded by the memory system's AXI width and
    # by what has closed timing on the part — a build request outside the
    # list is refused before any tool runs.
    width_tiers: tuple[int, ...] = (64,)
    # job_clock_mhz is a CONVERSION REQUEST, not a frequency fact: its
    # presence makes the shell insert a clock converter and run the job logic
    # at this rate; its absence means the job inherits the XDMA axi_aclk
    # unconverted. dpv1 deliberately leaves it None and still runs at 125.
    # Anything that needs the actual frequency (pricing against measured
    # resource points, rate models) must ask effective_job_clock_mhz().
    job_clock_mhz: int | None = None
    placeholders: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _storage_tiers_cohere(self) -> PlatformDefinition:
        names = [tier.name for tier in self.storage_tiers]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate storage tier names: {names}")
        if self.active_memory_tier is not None:
            tier = next((t for t in self.storage_tiers if t.name == self.active_memory_tier), None)
            if tier is None:
                raise ValueError(f"active_memory_tier {self.active_memory_tier!r} names no declared tier")
            if tier.access != "shared":
                raise ValueError(f"active_memory_tier {self.active_memory_tier!r} is owned, not shared")
        return self

    @property
    def memory(self) -> StorageTier:
        """The memory system this build instantiates: the named active shared
        tier, else the first shared tier. A property, not a field — the
        inventory is ``storage_tiers``; this is a selection over it."""
        if self.active_memory_tier is not None:
            return next(t for t in self.storage_tiers if t.name == self.active_memory_tier)
        shared = next((t for t in self.storage_tiers if t.access == "shared"), None)
        if shared is None:
            raise ValueError(f"platform {self.name!r} declares no shared storage tier")
        return shared

    def effective_job_clock_mhz(self) -> int:
        """The frequency the job logic actually runs at: ``job_clock_mhz``
        when a converter is requested, otherwise the XDMA ``axi_aclk`` the
        job inherits (125 on dpv1's Gen2 x4 personality). This is the clock
        to price and rate-model against; ``job_clock_mhz`` alone cannot
        answer that question because ``None`` there means "unconverted", not
        "unknown"."""
        return self.job_clock_mhz or self.host_link.xdma_personality.axi_clock_mhz()

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

    @field_validator("width_tiers")
    @classmethod
    def _valid_width_tiers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("width_tiers must name at least one tier")
        bad = [tier for tier in value if tier not in (64, 128, 256, 512)]
        if bad:
            raise ValueError(f"width_tiers entries must be 64/128/256/512, got {bad}")
        if len(set(value)) != len(value):
            raise ValueError(f"width_tiers has duplicates: {value}")
        return value

    @field_validator("platform_id")
    @classmethod
    def _valid_platform_id(cls, value: str) -> str:
        # 1–4 ASCII bytes: it must fit a single 32-bit PLATFORM_ID word (the
        # same rule dau-core's encoder enforces; checked here without importing
        # the private core, per this module's public/private wall)
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"platform_id must be ASCII, got {value!r}") from exc
        if not 1 <= len(encoded) <= 4:
            raise ValueError(f"platform_id must be 1 to 4 ASCII bytes, got {value!r}")
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


def min_lanes_for_full_rate(data_width: int) -> int:
    """The width-tier sizing law: a W-bit front delivers W/128 quad rows per
    beat and each lane drains one row per two cycles, so a catalog image
    sized to the front's delivered bandwidth needs ``lanes >= 2 * (W/128)``
    (64-bit two-beat framing is half a row per cycle — one lane matches it).
    Today's wide dispatcher serializes to one row per cycle, which two lanes
    already saturate; the law sizes for the front's full rate so precompiled
    images do not under-lane when parallel dispatch lands."""
    if data_width not in (64, 128, 256, 512):
        raise ValueError(f"data_width must be 64/128/256/512, got {data_width}")
    return max(1, 2 * (data_width // 128))


def max_lanes(lane_resources: ResourceUse, platform: PlatformDefinition, *, overhead: ResourceUse | None = None) -> int:
    """The envelope's lane ceiling: how many copies of one lane's resource
    footprint fit the platform budget after the composition's shared
    overhead (front/dispatcher/reader — pass the non-lane remainder as
    ``overhead``). Returns 0 when even the overhead alone does not fit —
    the caller decides whether that is a refusal or a smaller design.
    (Lane count is also a timing-closure knob — the 2026-07-19 lesson —
    so the pricing layer treats this as a CEILING, not a target.)"""
    budget = platform.budget

    def _halves(value: float, label: str) -> int:
        # BRAM counts in exact half-BRAM36 (RAMB18) units: float floor
        # division undercounts (335 // 0.1 == 3349.0) and real placements
        # are half-granular anyway
        halves = round(value * 2)
        if abs(value * 2 - halves) > 1e-9:
            raise ValueError(f"{label} bram36 must be half-BRAM granular, got {value}")
        return halves

    for label, use in (("lane_resources", lane_resources), ("overhead", overhead)):
        if use is not None and (use.lut < 0 or use.ff < 0 or use.bram36 < 0 or use.dsp < 0):
            raise ValueError(f"{label} components must be nonnegative")
    remaining = {
        "lut": budget.lut - (overhead.lut if overhead is not None else 0),
        "ff": budget.ff - (overhead.ff if overhead is not None else 0),
        "bram36": _halves(budget.bram36, "budget") - (_halves(overhead.bram36, "overhead") if overhead is not None else 0),
        "dsp": budget.dsp - (overhead.dsp if overhead is not None else 0),
    }
    if any(value < 0 for value in remaining.values()):
        return 0
    per_lane = {
        "lut": lane_resources.lut,
        "ff": lane_resources.ff,
        "bram36": _halves(lane_resources.bram36, "lane_resources"),
        "dsp": lane_resources.dsp,
    }
    ceilings = [remaining[key] // per_lane[key] for key in per_lane if per_lane[key] > 0]
    if not ceilings:
        raise ValueError("lane_resources must use at least one resource")
    return int(min(ceilings))
