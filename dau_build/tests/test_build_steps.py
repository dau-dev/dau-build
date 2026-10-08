from __future__ import annotations

from pathlib import Path
from shutil import which

import pytest
from ccflow import CallableModel

from dau_build.build_steps import (
    TASK_MODEL_TYPES,
    BuildStepError,
    available_task_names,
)
from dau_build.cli import main

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_task_dispatch_uses_ccflow_callable_models() -> None:
    assert available_task_names() == (
        "tasks/build/build-shell-project",
        "tasks/build/build-vivado-artifacts",
        "tasks/build/overlay-build",
        "tasks/build/render-cores",
        "tasks/build/synthesize",
        "tasks/build/synthesize-cores",
        "tasks/hardware/hardware-plan",
        "tasks/sim/simulate",
        "tasks/spec/build",
        "tasks/spec/inspect",
        "tasks/spec/validate",
        "tasks/stage/stage-shell",
        "tasks/stage/stage-vivado-overlay",
        "tasks/stage/stage-vivado-project",
        "tasks/validate/validate-vivado-artifacts",
    )
    assert all(issubclass(model_type, CallableModel) for model_type in TASK_MODEL_TYPES.values())


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_simulate_task_can_run_a_verilator_testbench(tmp_path: Path) -> None:
    pytest.importorskip("dau_sim.integrations.verilator")
    spec_path = _write_counter_spec(tmp_path)
    testbench_path = _write_counter_testbench(tmp_path)
    work_dir = tmp_path / "verilator-work"

    result = _run(
        "task=tasks/sim/simulate",
        f"model.spec_path={spec_path}",
        "model.module=counter",
        f"model.output_root={work_dir}",
        "simulator=simulators/verilator",
        f"simulator.testbench_path={testbench_path}",
        "simulator.top_module=counter_tb",
        "simulator.expect_stdout=DAU_BUILD_COUNTER_TB_OK",
    )

    assert result.step == "simulate"
    assert "simulator=verilator" in result.message and "status=passed" in result.message


def test_public_build_docs_and_tests_do_not_name_internal_hardware_hosts() -> None:
    forbidden = ("nu" + "c2", "ma" + "tx")
    repo_root = Path(__file__).resolve().parents[2]
    checked_paths = [repo_root / "README.md", repo_root / "examples", repo_root / "dau_build"]
    matches: list[str] = []
    for path in checked_paths:
        files = path.rglob("*") if path.is_dir() else (path,)
        for file_path in files:
            if not file_path.is_file() or file_path.suffix not in {".md", ".py", ".yaml", ".yml", ".toml"}:
                continue
            text = file_path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    matches.append(file_path.relative_to(repo_root).as_posix())
    assert matches == []


def test_a_task_without_a_spec_is_refused() -> None:
    with pytest.raises(BuildStepError, match="a spec is required"):
        _run("task=tasks/spec/inspect")


def test_the_entrypoint_prints_the_task_result(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = main(["task=tasks/spec/validate", f"model.spec_path={spec_path}"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [f"dau-build-spec-valid\tspec={spec_path}"]


def _run(*overrides):
    """Compose and run a task the way the CLI does: Hydra overrides only."""
    from ccflow.utils.hydra import cfg_run

    from dau_build.config import compose_config

    if len(overrides) == 1 and isinstance(overrides[0], tuple):
        overrides = overrides[0]
    return cfg_run(compose_config(list(overrides)).cfg)


def _write_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: identity-pipeline",
                "top_name: dau_identity_top",
                "platform: vivado-xdma",
                "shell: xdma-ddr",
                "artifact_stem: dau-identity",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - identity",
                "sources:",
                f"  - {(_SV_DIR / 'ff.sv').as_posix()}",
                "modules:",
                "  - ff",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def _write_counter_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "counter-dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: counter-pipeline",
                "top_name: counter_top",
                "platform: sim",
                "shell: unit-test",
                "artifact_stem: dau-counter",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - counter",
                "sources:",
                f"  - {(Path(__file__).parent / 'sv' / 'counter.sv').as_posix()}",
                "modules:",
                "  - counter",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def _write_counter_testbench(tmp_path: Path) -> Path:
    testbench_path = tmp_path / "counter_tb.sv"
    testbench_path.write_text(
        '`timescale 1ns/1ps\nmodule counter_tb;\n  logic clk = 1\'b0;\n  logic [31:0] out;\n  counter dut(.clk(clk), .out(out));\n  always #5 clk = ~clk;\n  initial begin\n    repeat (3) @(posedge clk);\n    #1;\n    if (out != 32\'d3) $fatal(1, "counter mismatch: %0d", out);\n    $display("DAU_BUILD_COUNTER_TB_OK");\n    $finish;\n  end\nendmodule\n',
        encoding="utf-8",
    )
    return testbench_path


def test_task_dispatch_import_stays_light() -> None:
    """Hardware hosts run flash/shell tasks with none of the SV-parser
    stack installed: importing the task surface must not pull it."""
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dau_build.build_steps; heavy = [m for m in ('amaranth', 'pyslang', 'dau_sim') if m in sys.modules]; raise SystemExit(1 if heavy else 0)",
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()


def test_vivado_engine_threads_the_overlay_definition_into_the_handoff(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from dau_build import build_steps
    from dau_build.vivado_backend import VivadoOverlayDefinition

    captured = {}

    def capture_request(request):
        captured["request"] = request
        raise ValueError("captured")

    monkeypatch.setattr(build_steps, "generate_vivado_backend_artifacts", capture_request)
    definition = VivadoOverlayDefinition(bd_overlay_tcl='puts "engine overlay"')
    spec = SimpleNamespace(
        sources=(str(tmp_path / "hdl" / "top.sv"),),
        artifact_stem="dau-identity",
        register_map_version="0.1",
        stream_protocol_version="0.1",
    )

    with pytest.raises(BuildStepError, match="captured"):
        build_steps._write_vivado_backend_handoff(
            spec,
            selected_module="dau_identity_top",
            output_root=tmp_path / "out",
            dau_artifact_bundle_path=tmp_path / "bundle.yaml",
            platform="vivado-xdma",
            shell="xdma-shell",
            operator_set=("identity",),
            overlay_definition=definition,
        )

    assert captured["request"].overlay_definition == definition
