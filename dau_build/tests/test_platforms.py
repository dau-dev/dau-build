from __future__ import annotations

from dataclasses import dataclass

import pytest
import yaml

from dau_build.platforms import (
    HostLink,
    PlatformDefinition,
    PlatformError,
    PlatformMemory,
    ResourceBudget,
    dpv1_platform,
    fits,
)


@dataclass(frozen=True)
class _Use:
    """Stand-in for dau-core's ResourceEnvelope (kept out of this public
    repo). fits() duck-types on these four attributes."""

    lut: int
    ff: int
    bram36: float
    dsp: int


def test_dpv1_round_trips_through_yaml() -> None:
    platform = dpv1_platform()
    reloaded = PlatformDefinition.from_dict(yaml.safe_load(yaml.safe_dump(platform.to_dict())))
    assert reloaded == platform


def test_round_trip_preserves_personality_and_constraints() -> None:
    platform = PlatformDefinition(
        name="probe",
        part="xc7k325tffg676-2",
        budget=ResourceBudget(lut=203800, ff=407600, bram36=445, dsp=840),
        host_link=HostLink(interface="pcie-xdma", pcie_lanes=8, xdma_personality={"pl_link_cap_max_link_width": "X8", "axisten_freq": "125"}),
        memory=PlatformMemory(kind="ddr3", size_bytes=8 << 30),
        constraints=("pins.xdc", "timing.xdc"),
        program_method="flash",
    )
    reloaded = PlatformDefinition.from_dict(yaml.safe_load(yaml.safe_dump(platform.to_dict())))
    assert reloaded == platform
    assert reloaded.host_link.xdma_personality["pl_link_cap_max_link_width"] == "X8"
    assert reloaded.constraints == ("pins.xdc", "timing.xdc")


def test_validation_rejects_bad_values() -> None:
    with pytest.raises(PlatformError, match="lut must be positive"):
        ResourceBudget(lut=0, ff=1, bram36=1, dsp=1)
    with pytest.raises(PlatformError, match="pcie_lanes"):
        HostLink(interface="pcie-xdma", pcie_lanes=3)
    with pytest.raises(PlatformError, match="size_bytes"):
        PlatformMemory(kind="ddr3", size_bytes=0)
    with pytest.raises(PlatformError, match="program_method"):
        _dpv1_with(program_method="usb")
    with pytest.raises(PlatformError, match="part must be non-empty"):
        _dpv1_with(part="")


def test_from_dict_reports_malformed_input() -> None:
    with pytest.raises(PlatformError, match="malformed"):
        PlatformDefinition.from_dict({"name": "x"})  # missing part/budget/link/memory


def test_fits_under_and_over_budget() -> None:
    platform = dpv1_platform()
    report = fits(_Use(lut=2000, ff=1500, bram36=4.0, dsp=8), platform)
    assert report.fits
    assert report.headroom["lut"] == 134600 - 2000
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
