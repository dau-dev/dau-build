"""Report parsing against the duck-typed core surface, with no provider installed.

dau-build is PUBLIC and resolves cores through the ccflow registry rather
than by importing a provider, so everything it asks of a core definition is
an interface a third party can satisfy. These tests ask for exactly that
interface and nothing else -- the integration suite beside them needs the
real registry and skips without it, which left the parts of the report that
only a provider exercises untested wherever the provider is absent, i.e. in
this repository's own CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dau_build.synthesize_cores import SynthesizeCoresTask

_UTIL_RPT = """
+----------------------------+------+-------+-----------+-------+
| Site Type                  | Used | Fixed | Available | Util% |
+----------------------------+------+-------+-----------+-------+
| Slice LUTs*                | 1295 |     0 |    133800 |  0.97 |
| Slice Registers            | 1196 |     0 |    267600 |  0.45 |
| Block RAM Tile             |    0 |     0 |       365 |  0.00 |
| DSPs                       |    0 |     0 |       740 |  0.00 |
+----------------------------+------+-------+-----------+-------+
"""
_TIMING_RPT = "Slack (MET) :             4.350ns  (required time - arrival time)\n"

_DIGEST = "1:" + "a" * 64


@dataclass
class _Point:
    part: str = "xc7a200tfbg484-2"
    clock_ns: float = 8.0
    params: dict = field(default_factory=lambda: {"K": 8})
    lut: int = 1295
    ff: int = 1196
    bram36: float = 0.0
    dsp: int = 0


@dataclass
class _Core:
    """The whole surface ``parse_reports`` reads off a core."""

    name: str = "stand-in"
    module: str = "dau_stand_in"
    resources: Any = None
    parameters: dict = field(default_factory=dict)
    measured_from: str | None = None
    closure_digest: str = _DIGEST

    def hdl_closure_digest(self, resolve) -> str:
        assert resolve is not None, "the resolver is what closes the dependency graph"
        return self.closure_digest


def _reports(tmp_path: Path) -> None:
    (tmp_path / "dau_stand_in.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_stand_in.timing.rpt").write_text(_TIMING_RPT)


def _resolve(name):
    return None


def test_a_run_reports_the_digest_of_what_it_read(tmp_path: Path) -> None:
    _reports(tmp_path)
    core = _Core(resources=(_Point(),), parameters={"K": object()})
    report = SynthesizeCoresTask.parse_reports(
        core, output_root=tmp_path, part="xc7a200tfbg484-2", clock_period_ns=8.0, params={"K": 8}, resolve=_resolve
    )
    assert report.measured_from == _DIGEST
    assert report.registered_matches is True
    # nothing stamped: no comparison, which is not the same as a match
    assert report.registered_measured_from_matches is None


def test_without_a_resolver_no_digest_is_claimed(tmp_path: Path) -> None:
    """A digest is a claim about which files a run read. Without the registry
    view that closes the dependency graph there is nothing to claim."""
    _reports(tmp_path)
    report = SynthesizeCoresTask.parse_reports(_Core(), output_root=tmp_path)
    assert report.measured_from is None
    assert report.registered_measured_from_matches is None


def test_a_stamp_is_compared_on_its_own_axis(tmp_path: Path) -> None:
    """The two comparisons answer different questions, so a stamp mismatch has
    to be visible even when every number reproduces exactly."""
    _reports(tmp_path)
    core = _Core(resources=(_Point(),), parameters={"K": object()}, measured_from=_DIGEST)
    agreed = SynthesizeCoresTask.parse_reports(
        core, output_root=tmp_path, part="xc7a200tfbg484-2", clock_period_ns=8.0, params={"K": 8}, resolve=_resolve
    )
    assert agreed.registered_matches is True and agreed.registered_measured_from_matches is True

    stale = _Core(resources=(_Point(),), parameters={"K": object()}, measured_from="1:" + "b" * 64)
    flagged = SynthesizeCoresTask.parse_reports(
        stale, output_root=tmp_path, part="xc7a200tfbg484-2", clock_period_ns=8.0, params={"K": 8}, resolve=_resolve
    )
    assert flagged.registered_matches is True, "the numbers still reproduce"
    assert flagged.registered_measured_from_matches is False, "and the RTL behind them is not what was stamped"


def test_a_stamp_is_reported_even_where_the_numbers_are_not_compared(tmp_path: Path) -> None:
    """An override run measures a coordinate the registry does not carry. That
    coordinate still has to be recorded against something."""
    _reports(tmp_path)
    core = _Core(resources=(_Point(),), parameters={"K": object()})
    report = SynthesizeCoresTask.parse_reports(core, output_root=tmp_path, compare=False, resolve=_resolve)
    assert report.registered_matches is None
    assert report.measured_from == _DIGEST
