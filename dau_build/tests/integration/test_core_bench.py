from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from ccflow import NullContext
from dau_sim.tests.verilator_support import requires_cocotb_verilator

from dau_build.build_steps import BuildStepError
from dau_build.core_bench import CoreBenchTask

pytest.importorskip("dau_core", reason="dau-core not installed (registry unavailable)")

_TIME_BUCKET_KEY = "/dau-core/time-bucket-key"


def test_a_core_without_a_bench_is_refused(tmp_path: Path) -> None:
    from dau_build.synthesize_cores import core_registry

    name = next(name for name, model in core_registry().models.items() if model.bench is None and model.kind.value != "package")
    with pytest.raises(BuildStepError, match=f"core {name!r} declares no bench"):
        CoreBenchTask(core=f"/dau-core/{name}", output_root=tmp_path)(NullContext())


def test_an_unknown_parameter_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BuildStepError, match=r"declares no parameter \['NOPE'\]"):
        CoreBenchTask(core=_TIME_BUCKET_KEY, output_root=tmp_path, parameters={"NOPE": 4})(NullContext())


@requires_cocotb_verilator
def test_one_testcase_runs_and_keeps_its_waveform(tmp_path: Path) -> None:
    """The CLI surface: task=tasks/sim/bench runs the bench the core declares."""
    from ccflow.utils.hydra import cfg_run

    from dau_build.config import compose_config

    result = compose_config(
        [
            "task=tasks/sim/bench",
            f"model.core={_TIME_BUCKET_KEY}",
            f"model.output_root={tmp_path}",
            "model.testcase=bucket_key_matches_golden",
            "model.waves=true",
        ]
    )
    outcome = cfg_run(result.cfg)

    waveform = tmp_path / "time-bucket-key" / "dump.vcd"
    assert outcome.message == (
        f"dau-build-core-bench\tcore=time-bucket-key testcase=bucket_key_matches_golden passed=1 failed=0 waveform={waveform} status=passed"
    )
    assert waveform.stat().st_size > 0
    names = [case.get("name") for case in ET.parse(tmp_path / "time-bucket-key-cocotb-results.xml").iter("testcase")]
    assert names == ["bucket_key_matches_golden"]
