from __future__ import annotations

from pathlib import Path

import pytest

from dau_build.sv_contract import StreamContractError, module_ports, validate_stream_tile

_CONFORMING = """
module good_tile (
    input  wire logic        clk,
    input  wire logic        rst,
    input  wire logic [31:0] cfg_thing,
    input  wire logic        input_valid,
    output logic             input_ready,
    input  wire logic [63:0] input_data,
    input  wire logic        input_last,
    output logic             output_valid,
    input  wire logic        output_ready,
    output logic [63:0]      output_data,
    output logic             output_last,
    output logic             status_valid,
    input  wire logic        status_ready,
    output logic             status_error,
    output logic [7:0]       status_error_code,
    output logic [63:0]      row_count
);
endmodule
"""

_VECTORED = """
module fanout_tile #(parameter int N = 4) (
    input  wire logic          clk,
    input  wire logic          rst,
    input  wire logic          input_valid,
    output logic               input_ready,
    input  wire logic [63:0]   input_data,
    input  wire logic          input_last,
    output logic [N-1:0]       output_valid,
    input  wire logic [N-1:0]  output_ready,
    output logic [N*64-1:0]    output_data,
    output logic [N-1:0]       output_last,
    output logic [N-1:0]       status_valid,
    input  wire logic [N-1:0]  status_ready,
    output logic [N-1:0]       status_error,
    output logic [N*8-1:0]     status_error_code
);
endmodule
"""

_BROKEN = """
module bad_tile (
    input  wire logic        clk,
    input  wire logic        rst,
    input  wire logic        input_valid,
    input  wire logic        input_ready,
    input  wire logic [63:0] input_data,
    input  wire logic        input_last,
    output logic             output_valid,
    input  wire logic        output_ready,
    output logic [63:0]      output_data,
    output logic             output_last,
    output logic             status_valid,
    input  wire logic        status_ready,
    output logic             status_error
);
endmodule
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tile.sv"
    path.write_text(text, encoding="utf-8")
    return path


def test_conforming_tile_passes(tmp_path: Path) -> None:
    source = _write(tmp_path, _CONFORMING)
    assert validate_stream_tile([source], "good_tile", count_port="row_count") == []


def test_vectored_fanout_tile_passes(tmp_path: Path) -> None:
    # per-lane vectored ports conform: names and directions are the contract
    source = _write(tmp_path, _VECTORED)
    assert validate_stream_tile([source], "fanout_tile") == []


def test_violations_are_reported_individually(tmp_path: Path) -> None:
    source = _write(tmp_path, _BROKEN)
    violations = validate_stream_tile([source], "bad_tile")
    assert "port 'input_ready' must be an output, is 'input'" in violations
    assert "missing output port 'status_error_code'" in violations


def test_missing_count_port_is_a_violation(tmp_path: Path) -> None:
    source = _write(tmp_path, _CONFORMING)
    violations = validate_stream_tile([source], "good_tile", count_port="bar_count")
    assert violations == ["missing declared count port 'bar_count'"]


def test_unknown_module_raises(tmp_path: Path) -> None:
    source = _write(tmp_path, _CONFORMING)
    with pytest.raises(StreamContractError, match="not found"):
        module_ports([source], "nope")


def test_inherited_ansi_directions_resolve(tmp_path: Path) -> None:
    source = _write(tmp_path, "module pair (input logic a, b, output logic c, d);\nendmodule\n")
    assert module_ports([source], "pair") == {"a": "input", "b": "input", "c": "output", "d": "output"}


def test_non_ansi_port_list_is_reported_explicitly(tmp_path: Path) -> None:
    source = _write(tmp_path, "module old (a, b);\ninput a;\noutput b;\nendmodule\n")
    with pytest.raises(StreamContractError, match="non-ANSI"):
        module_ports([source], "old")
