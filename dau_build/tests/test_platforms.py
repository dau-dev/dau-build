from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from dau_build.platforms import (
    HostLink,
    PlaceholderPlatformError,
    PlatformDefinition,
    PlatformMemory,
    ResourceBudget,
    XdmaPersonality,
    dpv1_platform,
    fits,
    require_measured,
)


@dataclass(frozen=True)
class _Use:
    """Stand-in for dau-core's ResourceEnvelope (kept out of this public
    repo). fits() duck-types on these four attributes."""

    lut: int
    ff: int
    bram36: float
    dsp: int


def test_dpv1_round_trips_through_model_serialization() -> None:
    platform = dpv1_platform()
    assert PlatformDefinition.model_validate(platform.model_dump()) == platform


def test_round_trip_preserves_personality_and_constraints() -> None:
    platform = PlatformDefinition(
        name="probe",
        part="xc7k325tffg676-2",
        budget=ResourceBudget(lut=203800, ff=407600, bram36=445, dsp=840),
        host_link=HostLink(
            interface="pcie-xdma",
            pcie_lanes=8,
            xdma_personality=XdmaPersonality(params={"pl_link_cap_max_link_width": "X8", "axisten_freq": "125"}),
        ),
        memory=PlatformMemory(kind="ddr3", size_bytes=8 << 30, constraints_xdc="set_property IOSTANDARD LVDS [get_ports sys_clk_clk_p]\n"),
        constraints=("pins.xdc", "timing.xdc"),
        constraints_xdc="set_property CFGBVS GND [current_design]\n",
        lane_placements=((0, "GTXE2_CHANNEL_X0Y8"), (1, "GTXE2_CHANNEL_X0Y9")),
        program_method="flash",
        placeholders=("host_link.xdma_personality",),
    )
    reloaded = PlatformDefinition.model_validate(platform.model_dump())
    assert reloaded == platform
    assert reloaded.host_link.xdma_personality.params["pl_link_cap_max_link_width"] == "X8"
    assert reloaded.constraints == ("pins.xdc", "timing.xdc")
    assert reloaded.lane_placements == ((0, "GTXE2_CHANNEL_X0Y8"), (1, "GTXE2_CHANNEL_X0Y9"))
    assert reloaded.placeholders == ("host_link.xdma_personality",)


def test_personality_emits_tcl_config_and_derives_link_width() -> None:
    personality = XdmaPersonality(params={"pl_link_cap_max_link_width": "X4", "axisten_freq": "125"})
    assert personality.link_width() == 4
    assert personality.to_tcl_config() == "    CONFIG.pl_link_cap_max_link_width {X4} \\\n    CONFIG.axisten_freq {125}"


def test_validation_rejects_bad_values() -> None:
    with pytest.raises(ValidationError, match="lut must be positive"):
        ResourceBudget(lut=0, ff=1, bram36=1, dsp=1)
    with pytest.raises(ValidationError, match="pcie_lanes"):
        HostLink(interface="pcie-xdma", pcie_lanes=3)
    with pytest.raises(ValidationError, match="expected_link_width"):
        HostLink(interface="pcie-xdma", pcie_lanes=8, expected_link_width=3)
    with pytest.raises(ValidationError, match="expected_link_speed_gts"):
        HostLink(interface="pcie-xdma", pcie_lanes=8, expected_link_speed_gts=0)
    with pytest.raises(ValidationError, match="size_bytes"):
        PlatformMemory(kind="ddr3", size_bytes=0)
    with pytest.raises(ValidationError, match="program_method"):
        _dpv1_with(program_method="usb")
    with pytest.raises(ValidationError, match="part must be non-empty"):
        _dpv1_with(part="")


def test_placeholder_platform_is_refused_for_real_builds() -> None:
    placeholder = _dpv1_with(name="probe", placeholders=("host_link.xdma_personality", "budget"))
    with pytest.raises(PlaceholderPlatformError, match="xdma_personality"):
        require_measured(placeholder)
    # a measured board (no placeholders) passes
    require_measured(dpv1_platform())


def test_fits_under_and_over_budget() -> None:
    platform = dpv1_platform()
    report = fits(_Use(lut=2000, ff=1500, bram36=4.0, dsp=8), platform)
    assert report.fits
    assert report.headroom["lut"] == 108800 - 2000
    assert report.utilization["dsp"] == pytest.approx(8 / 740)

    over = fits(_Use(lut=200000, ff=1500, bram36=4.0, dsp=8), platform)
    assert not over.fits
    assert over.headroom["lut"] < 0
    # a single over-budget resource fails the whole design
    assert over.headroom["ff"] > 0


def test_fits_exactly_at_budget_is_ok() -> None:
    platform = dpv1_platform()
    budget = platform.budget
    report = fits(_Use(lut=budget.lut, ff=budget.ff, bram36=budget.bram36, dsp=budget.dsp), platform)
    assert report.fits
    assert all(value == 0 for value in report.headroom.values())
    assert all(value == pytest.approx(1.0) for value in report.utilization.values())


def test_dpv1_platform_is_the_single_source_for_the_shell() -> None:
    from dau_build.dpv1_shell import DPV1_PART, GT_LANE_SWIZZLE, dpv1_constraints_xdc, dpv1_ddr_constraints_xdc, dpv1_xdma_personality

    platform = dpv1_platform()
    assert len(platform.host_link.xdma_personality.params) == 47
    # part stays a shell constant (request default); the config must not drift
    assert platform.part == DPV1_PART
    # lane count is consistent with the personality's link width
    assert platform.host_link.pcie_lanes == platform.host_link.xdma_personality.link_width() == 4
    # the proven host trains x2 at 5.0 GT/s over Thunderbolt
    assert platform.host_link.expected_link_width == 2
    assert platform.host_link.expected_link_speed_gts == 5.0
    assert platform.memory.mig_prj == "dpv1_mig.prj"
    # the shell's personality accessor resolves the same config
    assert dpv1_xdma_personality() == platform.host_link.xdma_personality
    # the lane swizzle and pin constraints are platform data; the shell
    # constants/accessors must not drift from the config
    assert platform.lane_placements == GT_LANE_SWIZZLE
    assert dpv1_constraints_xdc().endswith(platform.constraints_xdc)
    assert dpv1_ddr_constraints_xdc().endswith(platform.memory.constraints_xdc)
    # dpv1 is hardware-proven: no placeholder values
    assert platform.placeholders == ()
    # coerce-sensitive values survive yaml load intact
    params = platform.host_link.xdma_personality.params
    assert params["pf1_msix_cap_table_size"] == "000"
    assert params["pf1_msix_cap_table_offset"] == "00000000"


def test_platform_group_resolves_dpv1_through_hydra() -> None:
    from dau_build.config import resolve_platform

    assert resolve_platform("platforms/dau/dpv1") == dpv1_platform()


def test_resolve_platform_rejects_unknown() -> None:
    from dau_build.config import resolve_platform

    with pytest.raises(KeyError, match="unknown platform"):
        resolve_platform("no-such-board")


def test_user_config_dir_overlay_adds_a_board(tmp_path) -> None:
    # a user overlay registers a brand-new board with zero dau-build source
    # changes — the board is built from scratch via hydra _target_, proving
    # the models are overlay-instantiable
    from dau_build.config import resolve_platform

    overlay = tmp_path / "user-configs"
    (overlay / "platform").mkdir(parents=True)
    (overlay / "platform" / "myboard.yaml").write_text(
        "\n".join(
            (
                "# @package platform",
                "_target_: dau_build.platforms.PlatformDefinition",
                "name: myboard",
                "part: xc7k70tfbg484-2",
                "budget:",
                "  _target_: dau_build.platforms.ResourceBudget",
                "  lut: 41000",
                "  ff: 82000",
                "  bram36: 135",
                "  dsp: 240",
                "host_link:",
                "  _target_: dau_build.platforms.HostLink",
                "  interface: pcie-xdma",
                "  pcie_lanes: 1",
                "memory:",
                "  _target_: dau_build.platforms.PlatformMemory",
                "  kind: ddr3",
                "  size_bytes: 1073741824",
            )
        )
    )
    board = resolve_platform("myboard", config_dir=str(overlay))
    assert board.name == "myboard"
    assert board.part == "xc7k70tfbg484-2"
    assert board.budget.lut == 41000
    assert board.host_link.pcie_lanes == 1
    # the packaged dpv1 board still resolves alongside the overlay
    assert resolve_platform("platforms/dau/dpv1", config_dir=str(overlay)) == dpv1_platform()


def _dpv1_with(**overrides: object) -> PlatformDefinition:
    base = dict(
        name="dpv1",
        part="xc7a200tfbg484-2",
        budget=ResourceBudget(lut=134600, ff=269200, bram36=365, dsp=740),
        host_link=HostLink(interface="pcie-xdma", pcie_lanes=4),
        memory=PlatformMemory(kind="ddr3", size_bytes=1 << 30),
    )
    base.update(overrides)
    return PlatformDefinition(**base)  # type: ignore[arg-type]


def test_dpv1_host_access_pins_the_proven_bench_facts() -> None:
    # the platform config is the only source (the code fallbacks are
    # retired); these literals pin the proven bench facts
    access = dpv1_platform().host_access
    assert access is not None
    assert access.pci_id == "10ee:7011"
    assert access.endpoint_bdf == "0000:04:00.0"
    assert access.rescan_bdfs == (
        "0000:03:01.0",
        "0000:02:00.0",
        "0000:00:0d.3",
        "0000:00:0d.2",
        "0000:00:0d.0",
        "0000:00:07.2",
        "0000:00:07.0",
    )
    assert access.runtime_pm_patterns == ("Thunderbolt", "JHL", "10ee:7011", "Xilinx")
    assert access.runtime_pm_executable == "dau-utils-pci-runtime-pm"
    assert access.jtag_cable == "digilent_hs2"
