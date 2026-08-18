from __future__ import annotations

from pathlib import Path

import pytest
from ccflow import NullContext

from dau_build.build_steps import BuildStepError
from dau_build.synthesize_cores import SynthesizeCoresTask

pytest.importorskip("dau_core", reason="dau-core not installed (registry unavailable)")

_TOP_K = "/dau-core/streaming-top-k"


def _task(tmp_path: Path, **kwargs) -> SynthesizeCoresTask:
    defaults = {"cores": (_TOP_K,), "output_root": tmp_path, "part": "xc7a200tfbg484-2"}
    defaults.update(kwargs)
    return SynthesizeCoresTask(**defaults)


def test_handoff_writes_ooc_tcl_and_plan(tmp_path: Path) -> None:
    result = _task(tmp_path)(NullContext())
    assert "status=handoff-written" in result.message
    tcl = (tmp_path / "dau_streaming_top_k.ooc.tcl").read_text()
    assert "read_verilog -sv" in tcl and tcl.index("read_verilog") < tcl.index("synth_design")
    assert "synth_design -top dau_streaming_top_k -part xc7a200tfbg484-2 -mode out_of_context" in tcl
    assert "-generic K=8" in tcl
    # a tile that includes a header beside it must synthesize; the flag is
    # harmless when there is no include and removes a wall that would only
    # ever appear on a remote build
    assert "-include_dirs [list {" in tcl and "dau_core/hdl}]" in tcl
    # the clock constrains SYNTHESIS: the xdc reads before synth_design
    assert "read_xdc -mode out_of_context" in tcl and tcl.index("read_xdc") < tcl.index("synth_design")
    xdc = (tmp_path / "dau_streaming_top_k.ooc.xdc").read_text()
    assert "create_clock -period 8.000 -name clk [get_ports clk]" in xdc
    assert "report_utilization" in tcl and "report_timing_summary" in tcl
    plan = (tmp_path / "synthesize-cores.sh").read_text()
    assert "vivado -mode batch -source" in plan and "dau_streaming_top_k.ooc.tcl" in plan


def test_sources_are_dependency_closed_in_order(tmp_path: Path) -> None:
    _task(tmp_path, cores=("/dau-core/stream-aggregation",))(NullContext())
    tcl = (tmp_path / "dau_stream_aggregation.ooc.tcl").read_text()
    reads = [line for line in tcl.splitlines() if line.startswith("read_verilog")]
    assert len(reads) > 1  # the package + tile deps come along
    assert reads.index(next(r for r in reads if "dau_aggregation_pkg.sv" in r)) < reads.index(
        next(r for r in reads if "dau_stream_aggregation.sv" in r)
    )


def test_parameter_override_changes_generic(tmp_path: Path) -> None:
    _task(tmp_path, parameters={"streaming-top-k": {"K": 32}})(NullContext())
    tcl = (tmp_path / "dau_streaming_top_k.ooc.tcl").read_text()
    assert "-generic K=32" in tcl


def _has_element_types() -> bool:
    """Whether the installed core registry declares element types anywhere."""
    from dau_build.synthesize_cores import core_registry

    return any(
        getattr(spec, "element_type", False) for model in core_registry().models.values() for spec in getattr(model, "parameters", {}).values()
    )


# transition guard: valid once a core registry declaring element types is
# installed; until then no parameter can carry a spelling and there is
# nothing to exercise
requires_element_types = pytest.mark.skipif(
    not _has_element_types(),
    reason="installed core registry declares no element-type parameters",
)


@requires_element_types
def test_a_type_spelling_reaches_the_generic_as_an_integer(tmp_path: Path) -> None:
    """A spelling is how a type is written; an integer is what vivado
    elaborates. `-generic FIELD_WIDTH=int32` would fail in synthesis, which
    is the far end of a 35-minute build."""
    from dau_build.synthesize_cores import core_registry

    core, parameter = next(
        (name, parameter)
        for name, model in core_registry().models.items()
        for parameter, spec in getattr(model, "parameters", {}).items()
        if getattr(spec, "element_type", False)
    )
    task = _task(tmp_path, cores=(f"/dau-core/{core}",), parameters={core: {parameter: "int32"}})
    generics = task._generics(task._resolve_core(f"/dau-core/{core}"))
    assert generics[parameter] == 32, f"a spelling reached the tool unconverted: {generics[parameter]!r}"


@requires_element_types
def test_a_spelling_on_a_plain_size_parameter_is_refused(tmp_path: Path) -> None:
    """K is a size, not a type. Reading 'int32' as a number there would
    silently synthesize a 32-deep heap."""
    with pytest.raises(BuildStepError, match="must be an int"):
        _task(tmp_path, parameters={"streaming-top-k": {"K": "int32"}})(NullContext())


def test_undeclared_parameter_override_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BuildStepError, match="declares no parameter"):
        _task(tmp_path, parameters={"streaming-top-k": {"DEPTH": 4}})(NullContext())


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
    assert "-part xc7k325tffg900-2" in (tmp_path / "dau_streaming_top_k.ooc.tcl").read_text()
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
    from dau_core.registry import core

    definition = core("streaming-top-k")
    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)
    # this core carries a measurement SURFACE: several points share the part
    # and clock, so the comparison is only meaningful with the params too --
    # ALL of them, since the comparison is an exact coordinate match and the
    # tile declares its element type beside K (int32 encodes as 32)
    at_k8 = {"part": "xc7a200tfbg484-2", "clock_period_ns": 8.0, "params": {"K": 8, "FIELD_WIDTH": 32}}
    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, **at_k8)
    assert (report.lut, report.ff, report.bram36, report.dsp) == (1295, 1196, 0.0, 0)
    assert report.wns_ns == 4.350 and report.met
    assert report.registered_matches is True  # the registered point came from this shape

    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT.replace("1295", "999"))
    drifted = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, **at_k8)
    assert drifted.registered_matches is False


def test_parse_reports_needs_every_axis_to_compare_a_measurement_surface(tmp_path: Path) -> None:
    """A point is keyed by (part, clock, params). Drop any axis and there is
    no way to tell which point of a surface the build corresponds to, so the
    comparison is skipped rather than made against whichever point happens
    to be first — including the clock, which matters the moment a tile
    carries the same part and params at two clocks."""
    from dau_core.registry import core

    definition = core("streaming-top-k")
    assert isinstance(definition.resources, (list, tuple)) and len(definition.resources) > 1
    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)

    full = {"part": "xc7a200tfbg484-2", "clock_period_ns": 8.0, "params": {"K": 8, "FIELD_WIDTH": 32}}
    assert SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, **full).registered_matches is True
    for dropped in ("clock_period_ns", "params"):
        coordinate = {**full, dropped: None}
        report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, **coordinate)
        assert report.registered_matches is None, f"dropping {dropped} still selected a point"


def test_parse_reports_negative_slack_is_violated(tmp_path: Path) -> None:
    from dau_core.registry import core

    definition = core("streaming-top-k")
    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_streaming_top_k.timing.rpt").write_text("Slack (VIOLATED) :        -0.898ns  (required time - arrival time)\n")
    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path)
    assert not report.met and report.wns_ns == -0.898


def test_resolving_cores_leaves_the_root_registry_alone() -> None:
    """The fallback composition must not register the core group on the root
    registry: a later full compose by the core package would collide on a
    name this task put there, so whether an application's own composition
    works would depend on whether it ran a build task first."""
    from ccflow import ModelRegistry

    before = set(ModelRegistry.root().models)
    task = SynthesizeCoresTask(cores=("/dau-core/streaming-top-k",), output_root=Path("/unused"), part="xc7a200tfbg484-2")
    assert len(task._core_registry().models) > 0
    assert set(ModelRegistry.root().models) == before


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
    assert (tmp_path / "dau_streaming_top_k.ooc.tcl").is_file()


def test_parameter_override_constraints_are_enforced(tmp_path: Path) -> None:
    # the registry's declared ParameterSpec bounds reject bad overrides here,
    # never as an HDL elaboration failure
    with pytest.raises(BuildStepError, match="positive int"):
        _task(tmp_path, parameters={"streaming-top-k": {"K": 0}})(NullContext())
    with pytest.raises(BuildStepError, match="<= 128"):
        _task(tmp_path, parameters={"streaming-top-k": {"K": 200}})(NullContext())


def test_relative_output_root_stages_absolute_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # a relative output_root (the documented ./ooc) resolves once; every
    # staged path is absolute so a vivado cwd cannot double the prefix
    monkeypatch.chdir(tmp_path)
    result = _task(Path("ooc"))(NullContext())
    assert "status=handoff-written" in result.message
    tcl = (tmp_path / "ooc" / "dau_streaming_top_k.ooc.tcl").read_text()
    for line in tcl.splitlines():
        if line.startswith("read_xdc") or "-file" in line:
            assert str(tmp_path) in line, line
    plan = (tmp_path / "ooc" / "synthesize-cores.sh").read_text()
    assert str(tmp_path / "ooc" / "dau_streaming_top_k.ooc.tcl") in plan


def test_clock_port_mapping_and_unclocked_core(tmp_path: Path) -> None:
    # identity-axil clocks on s_axi_aclk; identity-registers is combinational
    _task(tmp_path, cores=("/dau-core/identity-axil",), clock_ports={"identity-axil": "s_axi_aclk"})(NullContext())
    xdc = (tmp_path / "dau_identity_axil.ooc.xdc").read_text()
    assert "[get_ports s_axi_aclk]" in xdc
    _task(tmp_path, cores=("/dau-core/identity-registers",), clock_ports={"identity-registers": ""})(NullContext())
    tcl = (tmp_path / "dau_identity_registers.ooc.tcl").read_text()
    assert "read_xdc" not in tcl and "report_timing_summary" not in tcl


def test_overridden_parameters_skip_envelope_comparison(tmp_path: Path) -> None:
    from dau_core.registry import core

    definition = core("streaming-top-k")
    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT.replace("1295", "5000"))
    (tmp_path / "dau_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)
    # a K=32 build is a different shape than the registered K=8 envelope, not drift
    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, compare=False)
    assert report.registered_matches is None


def test_parse_reports_carries_the_digest_of_the_hdl_it_read(tmp_path: Path) -> None:
    """The number a transcriber must record beside the LUT count. It comes
    from here because this is the only process that knows what synthesis
    actually read; a digest written anywhere else is a claim about a
    measurement nobody took."""
    from dau_core.registry import core, loaded_cores

    definition = core("streaming-top-k")
    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)

    without = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path)
    assert without.measured_from is None and without.registered_measured_from_matches is None

    report = SynthesizeCoresTask.parse_reports(definition, output_root=tmp_path, resolve=loaded_cores().get)
    assert report.measured_from == definition.hdl_closure_digest(loaded_cores().get)
    # nothing stamped yet: no comparison, which is not the same as a match
    assert report.registered_measured_from_matches is None


def test_parse_reports_flags_a_stamp_that_disagrees_with_the_hdl(tmp_path: Path) -> None:
    """The two comparisons are independent. Numbers that reproduce exactly
    say nothing about whether the registry knows which RTL produced them, so
    a stamp is reported on its own axis."""
    from dau_core.registry import core, loaded_cores

    definition = core("streaming-top-k")
    (tmp_path / "dau_streaming_top_k.util.rpt").write_text(_UTIL_RPT)
    (tmp_path / "dau_streaming_top_k.timing.rpt").write_text(_TIMING_RPT)
    at_k8 = {"part": "xc7a200tfbg484-2", "clock_period_ns": 8.0, "params": {"K": 8, "FIELD_WIDTH": 32}}
    resolve = loaded_cores().get

    stamped = definition.model_copy(update={"measured_from": definition.hdl_closure_digest(resolve)})
    agreed = SynthesizeCoresTask.parse_reports(stamped, output_root=tmp_path, resolve=resolve, **at_k8)
    assert agreed.registered_matches is True and agreed.registered_measured_from_matches is True

    stale = definition.model_copy(update={"measured_from": f"1:{'0' * 64}"})
    flagged = SynthesizeCoresTask.parse_reports(stale, output_root=tmp_path, resolve=resolve, **at_k8)
    assert flagged.registered_matches is True, "the numbers still reproduce"
    assert flagged.registered_measured_from_matches is False, "and the RTL behind them is not what was stamped"


def test_package_entries_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(BuildStepError, match="not a synthesizable top"):
        _task(tmp_path, cores=("/dau-core/aggregation-pkg",))(NullContext())


def test_dau_build_never_imports_a_private_core_package() -> None:
    """dau-build is PUBLIC and ships to PyPI; a private package is not a
    declared dependency, so importing one is a runtime landmine for any
    public install. Cores resolve through the ccflow ModelRegistry that a
    provider composes onto the shared hydra searchpath."""
    import ast
    from pathlib import Path

    package_root = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root in {"dau_core", "dau_driver", "dau_polars", "dau"}:
                    offenders.append(f"{path.relative_to(package_root)}:{node.lineno} imports {name}")
    assert not offenders, "public package imports a private one:\n" + "\n".join(offenders)


def test_a_missing_searchpath_plugin_names_itself(monkeypatch) -> None:
    """`+group=option` does NOT fail when the group is absent -- hydra
    assigns the literal string "cores" to a new key. The resulting empty
    registry then failed with a KeyError on the group name, which reads as a
    composition bug: it sent one investigation into ccflow's release notes
    when the actual cause was that the 'lerna' searchpath plugin was not
    installed on that host, so no provider's config tree was ever visible.
    """
    from types import SimpleNamespace

    from ccflow import ModelRegistry

    import dau_build.synthesize_cores as module

    monkeypatch.setattr(ModelRegistry, "root", staticmethod(lambda: ModelRegistry(name="empty-root")))
    monkeypatch.setattr(
        module,
        "compose_config",
        lambda *a, **k: SimpleNamespace(cfg={"dau-core": "cores"}),
        raising=False,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "dau_build.config",
        SimpleNamespace(compose_config=lambda *a, **k: SimpleNamespace(cfg={"dau-core": "cores"})),
    )

    with pytest.raises(BuildStepError) as caught:
        module.core_registry()
    message = str(caught.value)
    assert "not on the hydra searchpath" in message
    assert "hydra.lernaplugins" in message
    assert "hydra_plugins.lerna" in message, "the message must name the plugin whose absence causes this"
    assert "str" in message, "it should say what it got instead of a group"
