from __future__ import annotations

from pathlib import Path

import pytest
from ccflow import NullContext

from dau_build.build_steps import BuildStepError
from dau_build.synthesize_cores import SynthesizeCoresTask

pytest.importorskip("dau_core", reason="dau-core not installed (registry unavailable)")

_TOP_K = "/dau-core/int32-streaming-top-k"


def _task(tmp_path: Path, **kwargs) -> SynthesizeCoresTask:
    defaults = {"cores": (_TOP_K,), "output_root": tmp_path, "part": "xc7a200tfbg484-2"}
    defaults.update(kwargs)
    return SynthesizeCoresTask(**defaults)


def test_handoff_writes_ooc_tcl_and_plan(tmp_path: Path) -> None:
    result = _task(tmp_path)(NullContext())
    assert "status=handoff-written" in result.message
    tcl = (tmp_path / "dau_int32_streaming_top_k.ooc.tcl").read_text()
    assert "read_verilog -sv" in tcl and tcl.index("read_verilog") < tcl.index("synth_design")
    assert "synth_design -top dau_int32_streaming_top_k -part xc7a200tfbg484-2 -mode out_of_context -generic K=8" in tcl
    # the clock constrains SYNTHESIS: the xdc reads before synth_design
    assert "read_xdc -mode out_of_context" in tcl and tcl.index("read_xdc") < tcl.index("synth_design")
    xdc = (tmp_path / "dau_int32_streaming_top_k.ooc.xdc").read_text()
    assert "create_clock -period 8.000 -name clk [get_ports clk]" in xdc
    assert "report_utilization" in tcl and "report_timing_summary" in tcl
    plan = (tmp_path / "synthesize-cores.sh").read_text()
    assert "vivado -mode batch -source" in plan and "dau_int32_streaming_top_k.ooc.tcl" in plan


def test_sources_are_dependency_closed_in_order(tmp_path: Path) -> None:
    _task(tmp_path, cores=("/dau-core/int32-stream-aggregation",))(NullContext())
    tcl = (tmp_path / "dau_int32_stream_aggregation.ooc.tcl").read_text()
    reads = [line for line in tcl.splitlines() if line.startswith("read_verilog")]
    assert len(reads) > 1  # the package + tile deps come along
    assert reads.index(next(r for r in reads if "dau_aggregation_pkg.sv" in r)) < reads.index(
        next(r for r in reads if "dau_int32_stream_aggregation.sv" in r)
    )


def test_parameter_override_changes_generic(tmp_path: Path) -> None:
    _task(tmp_path, parameters={"int32-streaming-top-k": {"K": 32}})(NullContext())
    tcl = (tmp_path / "dau_int32_streaming_top_k.ooc.tcl").read_text()
    assert "-generic K=32" in tcl


def test_undeclared_parameter_override_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BuildStepError, match="declares no parameter"):
        _task(tmp_path, parameters={"int32-streaming-top-k": {"DEPTH": 4}})(NullContext())


def test_unknown_core_and_bad_path_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(BuildStepError, match="unknown core"):
        _task(tmp_path, cores=("/dau-core/no-such-core",))(NullContext())
    with pytest.raises(BuildStepError, match="registry path"):
        _task(tmp_path, cores=("/elsewhere/thing",))(NullContext())


def test_part_falls_back_to_platform_and_errors_without_either(tmp_path: Path) -> None:
    class FakePlatform:
        part = "xc7k325tffg900-2"

    task = SynthesizeCoresTask(cores=(_TOP_K,), output_root=tmp_path, platform=FakePlatform())
    task(NullContext())
    assert "-part xc7k325tffg900-2" in (tmp_path / "dau_int32_streaming_top_k.ooc.tcl").read_text()
    with pytest.raises(BuildStepError, match="no part selected"):
        SynthesizeCoresTask(cores=(_TOP_K,), output_root=tmp_path / "x")(NullContext())


_UTIL_RPT = """
| Slice LUTs*             | 1295 |     0 |          0 |    134600 |  0.96 |
| Slice Registers         | 1196 |     0 |          0 |    269200 |  0.44 |
| Block RAM Tile |    0 |     0 |          0 |       365 |  0.00 |
| DSPs      |    0 |     0 |          0 |       740 |  0.00 |
"""

_TIMING_RPT = "Slack (MET) :             4.350ns  (required time - arrival time)\n"


def test_parse_reports_builds_envelope_and_flags_drift(tmp_path: Path) -> None:
    from dau_core.cores import core

    definition = core("int32-streaming-top-k")
    (tmp_path / "dau_int32_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_int32_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)
    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path)
    assert (report.lut, report.ff, report.bram36, report.dsp) == (1295, 1196, 0.0, 0)
    assert report.wns_ns == 4.350 and report.met
    assert report.registered_matches is True  # the registered envelope came from this shape

    (tmp_path / "dau_int32_streaming_top_k.util.rpt").write_text(_UTIL_RPT.replace("1295", "999"))
    drifted = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path)
    assert drifted.registered_matches is False


def test_parse_reports_negative_slack_is_violated(tmp_path: Path) -> None:
    from dau_core.cores import core

    definition = core("int32-streaming-top-k")
    (tmp_path / "dau_int32_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_int32_streaming_top_k.timing.rpt").write_text("Slack (VIOLATED) :        -0.898ns  (required time - arrival time)\n")
    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path)
    assert not report.met and report.wns_ns == -0.898


def test_task_composes_from_the_config_group(tmp_path: Path) -> None:
    """The CLI surface: task=tasks/build/synthesize-cores resolves this task
    with cores/output_root overrides — the registry is the coupling, not an
    import."""
    from ccflow.utils.hydra import cfg_run

    from dau_build.config import compose_config

    result = compose_config(
        [
            "task=tasks/build/synthesize-cores",
            f"model.cores=[{_TOP_K}]",
            f"model.output_root={tmp_path}",
            "model.part=xc7a200tfbg484-2",
        ]
    )
    outcome = cfg_run(result.cfg)
    assert "status=handoff-written" in outcome.message
    assert (tmp_path / "dau_int32_streaming_top_k.ooc.tcl").is_file()


def test_parameter_override_constraints_are_enforced(tmp_path: Path) -> None:
    # the registry's declared ParameterSpec bounds reject bad overrides here,
    # never as an HDL elaboration failure
    with pytest.raises(BuildStepError, match="positive int"):
        _task(tmp_path, parameters={"int32-streaming-top-k": {"K": 0}})(NullContext())
    with pytest.raises(BuildStepError, match="<= 128"):
        _task(tmp_path, parameters={"int32-streaming-top-k": {"K": 200}})(NullContext())


def test_relative_output_root_stages_absolute_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # a relative output_root (the documented ./ooc) resolves once; every
    # staged path is absolute so a vivado cwd cannot double the prefix
    monkeypatch.chdir(tmp_path)
    result = _task(Path("ooc"))(NullContext())
    assert "status=handoff-written" in result.message
    tcl = (tmp_path / "ooc" / "dau_int32_streaming_top_k.ooc.tcl").read_text()
    for line in tcl.splitlines():
        if line.startswith("read_xdc") or "-file" in line:
            assert str(tmp_path) in line, line
    plan = (tmp_path / "ooc" / "synthesize-cores.sh").read_text()
    assert str(tmp_path / "ooc" / "dau_int32_streaming_top_k.ooc.tcl") in plan


def test_clock_port_mapping_and_unclocked_core(tmp_path: Path) -> None:
    # identity-axil clocks on s_axi_aclk; identity-registers is combinational
    _task(tmp_path, cores=("/dau-core/identity-axil",), clock_ports={"identity-axil": "s_axi_aclk"})(NullContext())
    xdc = (tmp_path / "dau_identity_axil.ooc.xdc").read_text()
    assert "[get_ports s_axi_aclk]" in xdc
    _task(tmp_path, cores=("/dau-core/identity-registers",), clock_ports={"identity-registers": ""})(NullContext())
    tcl = (tmp_path / "dau_identity_registers.ooc.tcl").read_text()
    assert "read_xdc" not in tcl and "report_timing_summary" not in tcl


def test_overridden_parameters_skip_envelope_comparison(tmp_path: Path) -> None:
    from dau_core.cores import core

    definition = core("int32-streaming-top-k")
    (tmp_path / "dau_int32_streaming_top_k.util.rpt").write_text(_UTIL_RPT.replace("1295", "5000"))
    (tmp_path / "dau_int32_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)
    # a K=32 build is a different shape than the registered K=8 envelope, not drift
    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, compare=False)
    assert report.registered_matches is None
