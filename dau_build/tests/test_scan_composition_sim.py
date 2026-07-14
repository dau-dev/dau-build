"""Bench for the generated scan-composition sim harness: a two-lane
broadcast composition over the test-only offset tile, built against the
behavioral doubles in ``tests/sv/scan_sim`` (dau-build ships no dau-core
HDL), driven end-to-end through the backdoor RAM."""

from pathlib import Path
from shutil import which

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb_tools.runner import get_runner

from dau_build.scan_composition import LaneTile, ScanComposition, generate_scan_composition_sim_sv

_SCAN_SIM_SV = Path(__file__).resolve().parent / "sv" / "scan_sim"
_MASK64 = (1 << 64) - 1

_ROWS = [7, 21, 1 << 40, 3, 9, 2, 100, (1 << 64) - 5]
_INPUT_WORD = 16
_LANE_WORDS = (128, 256)
_OFFSETS = (1, 1000)


def _bench_composition() -> ScanComposition:
    return ScanComposition(
        name="offset-bench",
        module_name="dau_offset_bench_job",
        burst_beats=16,
        lanes=tuple(LaneTile(module="dau_test_offset_tile", config={"cfg_offset": f"64'd{offset}"}, count_port="row_count") for offset in _OFFSETS),
    )


async def _reset(dut):
    dut.rst.value = 1
    dut.start.value = 0
    dut.input_address.value = 0
    dut.input_length_bytes.value = 0
    dut.lane_output_address.value = 0
    dut.bd_write.value = 0
    dut.bd_index.value = 0
    dut.bd_wdata.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def _bd_write(dut, index: int, value: int):
    dut.bd_write.value = 1
    dut.bd_index.value = index
    dut.bd_wdata.value = value
    await RisingEdge(dut.clk)
    dut.bd_write.value = 0


async def _bd_read(dut, index: int) -> int:
    dut.bd_index.value = index
    await RisingEdge(dut.clk)
    return int(dut.bd_rdata.value)


async def _run_job(dut, *, length_bytes: int):
    dut.input_address.value = _INPUT_WORD * 8
    dut.input_length_bytes.value = length_bytes
    dut.lane_output_address.value = (_LANE_WORDS[1] * 8 << 32) | (_LANE_WORDS[0] * 8)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    for _ in range(5000):
        await RisingEdge(dut.clk)
        if dut.done.value:
            return
    raise AssertionError("job did not reach done")


async def _preload(dut):
    for j, row in enumerate(_ROWS):
        await _bd_write(dut, _INPUT_WORD + j, row)


async def _check_good_job(dut):
    await _run_job(dut, length_bytes=len(_ROWS) * 8)
    assert dut.error.value == 0, f"unexpected error {int(dut.error_code.value):#x}"
    assert dut.busy.value == 0
    lengths = int(dut.lane_result_length_bytes.value)
    counts = int(dut.lane_count.value)
    for lane, (base, offset) in enumerate(zip(_LANE_WORDS, _OFFSETS)):
        assert (lengths >> (32 * lane)) & 0xFFFFFFFF == len(_ROWS) * 8, f"lane {lane} result length"
        assert (counts >> (64 * lane)) & _MASK64 == len(_ROWS), f"lane {lane} row count"
        for j, row in enumerate(_ROWS):
            got = await _bd_read(dut, base + j)
            assert got == (row + offset) & _MASK64, f"lane {lane} row {j}: {got:#x}"


@cocotb.test()
async def broadcast_roundtrip(dut):
    """One scan fans to both lanes; each lane's tile offsets the rows and
    its writer lands them in the lane's own region."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start(start_high=False))
    await _reset(dut)
    await _preload(dut)
    await _check_good_job(dut)


@cocotb.test()
async def off_grid_length_rejected_then_recovers(dut):
    """A length off the 16-byte row grid fails fast with 0xFE and leaves
    the pipeline able to run the next job cleanly."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start(start_high=False))
    await _reset(dut)
    await _preload(dut)

    await _run_job(dut, length_bytes=len(_ROWS) * 8 - 4)
    assert dut.error.value == 1
    assert int(dut.error_code.value) == 0xFE
    assert dut.busy.value == 0

    await _check_good_job(dut)


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_scan_composition_sim_bench(tmp_path: Path):
    composition = _bench_composition()
    harness = generate_scan_composition_sim_sv(
        composition,
        module_name="dau_offset_bench_sim",
        mem_words=4096,
        sources=(_SCAN_SIM_SV / "dau_test_offset_tile.sv",),
    )
    top = tmp_path / "dau_offset_bench_sim.v"
    top.write_text(harness)

    runner = get_runner("verilator")
    build_dir = tmp_path / "sim_build"
    runner.build(
        sources=[top, *sorted(_SCAN_SIM_SV.glob("*.sv"))],
        hdl_toplevel="dau_offset_bench_sim",
        always=True,
        build_dir=build_dir,
    )
    runner.test(hdl_toplevel="dau_offset_bench_sim", test_module="dau_build.tests.test_scan_composition_sim", build_dir=build_dir)
