"""The bench task's own behavior, with the registry and the simulator stood
in: what it passes to the cocotb launcher and how it reads the results. The
run against a real core is in ``integration/test_core_bench.py``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from ccflow import NullContext

from dau_build import core_bench
from dau_build.build_steps import BuildStepError
from dau_build.core_bench import CoreBenchTask


@pytest.fixture
def launched(monkeypatch, tmp_path: Path) -> dict:
    """A core with a bench and one parameter, and a launcher that records its
    arguments and writes the results the test asks for."""
    definition = SimpleNamespace(name="vendor-alu", module="vendor_alu", bench="vendor.benches.vendor_alu_tb")
    source = tmp_path / "vendor_alu.sv"
    calls: dict = {"suite": '<testsuite tests="2" failures="0" skipped="0"/>'}

    def launch(**kwargs) -> Path:
        calls.update(kwargs)
        Path(kwargs["results_xml"]).write_text(f"<testsuites>{calls['suite']}</testsuites>")
        return Path(kwargs["results_xml"])

    monkeypatch.setattr(core_bench, "resolve_core_definition", lambda entry: definition)
    monkeypatch.setattr(core_bench, "core_generics", lambda definition, overrides: {"WIDTH": 32, **overrides})
    monkeypatch.setattr(core_bench, "core_registries", lambda: ())
    monkeypatch.setattr(core_bench, "core_sources", lambda definition, registries, root: (source,))
    monkeypatch.setattr("dau_build.render_cores.render_generated_cores", lambda definitions, root: None)
    monkeypatch.setattr("dau_sim.integrations.cocotb.run_cocotb_testbench", launch)
    return calls


def test_the_bench_runs_the_declared_module_and_reports_the_counts(launched: dict, tmp_path: Path) -> None:
    outcome = CoreBenchTask(core="/dau-core/vendor-alu", output_root=tmp_path, testcase="adds", waves=True, parameters={"WIDTH": 16})(NullContext())

    build_dir = tmp_path / "vendor-alu"
    assert outcome.message == f"dau-build-core-bench\tcore=vendor-alu testcase=adds passed=2 failed=0 waveform={build_dir / 'dump.vcd'} status=passed"
    assert (launched["hdl_toplevel"], launched["test_module"], launched["build_dir"]) == ("vendor_alu", "vendor.benches.vendor_alu_tb", build_dir)
    # only the overrides are passed; the module keeps its own defaults otherwise
    assert (launched["parameters"], launched["waves"], launched["testcase"]) == ({"WIDTH": 16}, True, "adds")


def test_a_run_without_a_waveform_names_none(launched: dict, tmp_path: Path) -> None:
    outcome = CoreBenchTask(core="/dau-core/vendor-alu", output_root=tmp_path)(NullContext())
    assert outcome.message == "dau-build-core-bench\tcore=vendor-alu testcase=all passed=2 failed=0 status=passed"
    assert launched["parameters"] == {}


def test_a_failing_test_fails_the_task(launched: dict, tmp_path: Path) -> None:
    """The cocotb launcher raises on a failure only under pytest, so the task
    reads the results itself."""
    launched["suite"] = '<testsuite tests="3" failures="1" skipped="0"/>'
    with pytest.raises(BuildStepError, match="1 of 3 tests failed"):
        CoreBenchTask(core="/dau-core/vendor-alu", output_root=tmp_path)(NullContext())


def test_a_testcase_that_matches_nothing_fails_the_task(launched: dict, tmp_path: Path) -> None:
    launched["suite"] = '<testsuite tests="0" failures="0" skipped="0"/>'
    with pytest.raises(BuildStepError, match="ran no tests .*testcase 'nope'"):
        CoreBenchTask(core="/dau-core/vendor-alu", output_root=tmp_path, testcase="nope")(NullContext())
