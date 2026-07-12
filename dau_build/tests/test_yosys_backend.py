from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

import pytest

from dau_build.config import run_request_config
from dau_build.yosys_backend import YosysBackendRequest, _parse_cell_count, run_yosys_synthesis, yosys_script_text

_REPO_ROOT = Path(__file__).resolve().parents[2]
_IDENTITY_SPEC = _REPO_ROOT / "examples" / "identity" / "dau-build.yaml"


def _slang_available() -> bool:
    if which("yosys") is None:
        return False
    try:
        proc = subprocess.run(["yosys", "-m", "slang", "-p", "help read_slang"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return proc.returncode == 0


requires_yosys = pytest.mark.skipif(which("yosys") is None, reason="yosys not found")
requires_slang = pytest.mark.skipif(not _slang_available(), reason="yosys-slang plugin not found")


def test_yosys_script_text_selects_frontend() -> None:
    request = YosysBackendRequest(top_module="top", sources=(Path("a.sv"), Path("b.sv")), output_root=Path("out"))
    verilog = yosys_script_text(request)
    assert "read_verilog -sv a.sv b.sv" in verilog
    assert "synth -top top" in verilog
    assert verilog.strip().endswith("stat")

    slang = yosys_script_text(request.model_copy(update={"frontend": "slang"}))
    assert "read_slang --top top a.sv b.sv" in slang


def test_yosys_script_text_elaborate_only_when_synth_disabled() -> None:
    request = YosysBackendRequest(top_module="top", sources=(Path("a.sv"),), output_root=Path("out"), synth=False)
    script = yosys_script_text(request)
    assert "hierarchy -top top" in script
    assert "check -assert" in script
    assert "synth -top" not in script


def test_parse_cell_count_handles_both_stat_formats() -> None:
    assert _parse_cell_count("Number of cells:                 42\n") == 42
    assert _parse_cell_count("=== top ===\n   696 wires\n   977 cells\n") == 977
    assert _parse_cell_count("no stat here") is None


@requires_yosys
def test_run_yosys_synthesizes_a_module(tmp_path: Path) -> None:
    source = tmp_path / "counter.sv"
    source.write_text(
        "module counter(input logic clk, input logic rst, output logic [3:0] q);\n"
        "  always_ff @(posedge clk) q <= rst ? 4'd0 : q + 4'd1;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    result = run_yosys_synthesis(YosysBackendRequest(top_module="counter", sources=(source,), output_root=tmp_path / "out"))
    assert result.passed
    assert result.returncode == 0
    assert result.cell_count and result.cell_count > 0
    assert result.script_path.is_file()
    assert result.log_path.is_file()


@requires_yosys
def test_run_yosys_reports_a_synthesis_failure(tmp_path: Path) -> None:
    source = tmp_path / "bad.sv"
    source.write_text("module bad; missing_module u(); endmodule\n", encoding="utf-8")
    result = run_yosys_synthesis(YosysBackendRequest(top_module="bad", sources=(source,), output_root=tmp_path / "out"))
    assert not result.passed
    assert result.returncode != 0


@requires_yosys
def test_synthesize_task_yosys_engine_runs_real_synthesis(tmp_path: Path) -> None:
    # the engine is the composed backend group: backend=backends/yosys
    result = run_request_config(
        "task",
        "tasks/build/synthesize",
        overrides=["backend=backends/yosys"],
        model_values={"module": "identity", "spec_path": str(_IDENTITY_SPEC), "output_root": str(tmp_path)},
    )
    assert result.step == "synthesize"
    assert "engine=yosys frontend=verilog" in result.message
    assert "status=synthesized" in result.message
    assert (tmp_path / "dau_yosys.ys").is_file()


@requires_slang
def test_synthesize_task_yosys_slang_frontend_via_hydra_override(tmp_path: Path) -> None:
    # the engine is fully hydra-configurable: backend.frontend=slang
    result = run_request_config(
        "task",
        "tasks/build/synthesize",
        overrides=["backend=backends/yosys", "backend.frontend=slang"],
        model_values={"module": "identity", "spec_path": str(_IDENTITY_SPEC), "output_root": str(tmp_path)},
    )
    assert result.step == "synthesize"
    assert "engine=yosys frontend=slang" in result.message
    assert "status=synthesized" in result.message
