"""Bench for the shell handle table, driven through the generated
scan-composition sim harness: the same one-lane pipeline the other sim bench
runs, but with the job's input address resolved from a handle instead of
taken from a register.

The point under test is REFUSAL. A resident dataset addressed by a raw
address keeps answering after it has been freed and the space handed to
someone else -- the read succeeds and returns the wrong bytes. So the cases
that matter here are the ones where the table must say no: a freed handle, a
recycled slot answering its previous generation, an id the table does not
have, and a job asking for more bytes than the grant. Each one has to close
the job out with its own code and read NOT ONE BEAT of memory.
"""

from pathlib import Path
from shutil import which

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb_tools.runner import get_runner

from dau_build.scan_composition import (
    HANDLE_FAULT_BAD_ID,
    HANDLE_FAULT_OUT_OF_BOUNDS,
    HANDLE_FAULT_STALE,
    LaneTile,
    ScanComposition,
    generate_scan_composition_sim_sv,
)

_SCAN_SIM_SV = Path(__file__).resolve().parent / "sv" / "scan_sim"
_MASK64 = (1 << 64) - 1

_CAPACITY = 4
_OFFSET = 7
_LANE_WORD = 256  # the lane writer's output region, in 64-bit backdoor words

# two disjoint regions of the backdoor RAM holding DIFFERENT rows: the point
# of the recycle case is that the old handle must not reach the new tenant's
# bytes, which is only observable if the two regions do not agree
_REGION_A_WORD = 16
_REGION_B_WORD = 64
_ROWS_A = [7, 21, 1 << 40, 3]
_ROWS_B = [1000, 1001, 1002, 1003]


def _bench_composition() -> ScanComposition:
    return ScanComposition(
        name="handle-table-bench",
        module_name="dau_handle_table_bench_job",
        burst_beats=16,
        lanes=(LaneTile(module="dau_test_offset_tile", config={"cfg_offset": f"64'd{_OFFSET}"}, count_port="row_count"),),
        handle_table_capacity=_CAPACITY,
    )


async def _reset(dut):
    dut.rst.value = 1
    dut.start.value = 0
    dut.input_address.value = 0
    dut.input_length_bytes.value = 0
    dut.lane_output_address.value = _LANE_WORD * 8
    dut.handle_install.value = 0
    dut.handle_free.value = 0
    dut.handle_index.value = 0
    dut.handle_base.value = 0
    dut.handle_length.value = 0
    dut.handle_generation.value = 0
    dut.job_handle_id.value = 0
    dut.job_handle_generation.value = 0
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
    await RisingEdge(dut.clk)
    return int(dut.bd_rdata.value)


async def _install(dut, *, index: int, base_word: int, length_bytes: int, generation: int):
    """One allocation staged into the programming registers and committed --
    the host allocator's write sequence, which on hardware is the same
    registers behind AXI-Lite."""
    dut.handle_index.value = index
    dut.handle_base.value = base_word * 8
    dut.handle_length.value = length_bytes
    dut.handle_generation.value = generation
    dut.handle_install.value = 1
    await RisingEdge(dut.clk)
    dut.handle_install.value = 0
    await RisingEdge(dut.clk)


async def _free(dut, index: int):
    dut.handle_index.value = index
    dut.handle_free.value = 1
    await RisingEdge(dut.clk)
    dut.handle_free.value = 0
    await RisingEdge(dut.clk)


async def _run_job(dut, *, handle_id: int, generation: int, length_bytes: int) -> None:
    dut.job_handle_id.value = handle_id
    dut.job_handle_generation.value = generation
    dut.input_length_bytes.value = length_bytes
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    for _ in range(5000):
        await RisingEdge(dut.clk)
        if dut.done.value:
            return
    raise AssertionError("job did not reach done")


async def _preload(dut):
    for j, row in enumerate(_ROWS_A):
        await _bd_write(dut, _REGION_A_WORD + j, row)
    for j, row in enumerate(_ROWS_B):
        await _bd_write(dut, _REGION_B_WORD + j, row)
    # a sentinel in every output word, so "the writer never ran" is provable
    # rather than inferred from an absence
    for j in range(len(_ROWS_A)):
        await _bd_write(dut, _LANE_WORD + j, _MASK64)


async def _assert_output(dut, rows) -> None:
    for j, row in enumerate(rows):
        got = await _bd_read(dut, _LANE_WORD + j)
        assert got == (row + _OFFSET) & _MASK64, f"output word {j}: {got:#x}"


async def _assert_output_untouched(dut, count: int) -> None:
    for j in range(count):
        got = await _bd_read(dut, _LANE_WORD + j)
        assert got == _MASK64, f"a refused job wrote output word {j} ({got:#x}); it must not have run at all"


async def _refused(dut, *, handle_id: int, generation: int, length_bytes: int, code: int) -> None:
    await _run_job(dut, handle_id=handle_id, generation=generation, length_bytes=length_bytes)
    assert dut.error.value == 1, "a refused handle must close the job out with an error"
    assert int(dut.error_code.value) == code, f"error code {int(dut.error_code.value):#04x}, wanted {code:#04x}"
    assert dut.busy.value == 0


async def _start(dut):
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start(start_high=False))
    await _reset(dut)
    await _preload(dut)


@cocotb.test()
async def a_live_handle_resolves_to_its_region(dut):
    """The baseline the refusals are measured against: an installed handle
    names its region and the job reads exactly those rows."""
    await _start(dut)
    await _install(dut, index=2, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=1)
    await _run_job(dut, handle_id=2, generation=1, length_bytes=len(_ROWS_A) * 8)
    assert dut.error.value == 0, f"unexpected error {int(dut.error_code.value):#04x}"
    assert int(dut.lane_count.value) == len(_ROWS_A)
    await _assert_output(dut, _ROWS_A)


@cocotb.test()
async def a_freed_handle_is_refused(dut):
    """Free bumps the slot out of liveness; the handle the caller still holds
    stops resolving even though the memory is untouched and still readable."""
    await _start(dut)
    await _install(dut, index=2, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=1)
    await _free(dut, 2)
    await _refused(dut, handle_id=2, generation=1, length_bytes=len(_ROWS_A) * 8, code=HANDLE_FAULT_STALE)
    await _assert_output_untouched(dut, len(_ROWS_A))


@cocotb.test()
async def a_recycled_slot_refuses_the_previous_generation(dut):
    """THE CASE THE TABLE EXISTS FOR. Slot 2 is freed and handed to a
    different allocation over a different region. The first caller's handle
    is byte-for-byte what it always was, and the raw address it used to mean
    still reads fine -- so the only thing that can refuse it is the
    generation. It is refused, and the reissued handle reads the NEW tenant's
    rows, proving the slot really was reused underneath."""
    await _start(dut)
    await _install(dut, index=2, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=1)
    await _free(dut, 2)
    await _install(dut, index=2, base_word=_REGION_B_WORD, length_bytes=len(_ROWS_B) * 8, generation=2)

    await _refused(dut, handle_id=2, generation=1, length_bytes=len(_ROWS_A) * 8, code=HANDLE_FAULT_STALE)
    await _assert_output_untouched(dut, len(_ROWS_A))

    await _run_job(dut, handle_id=2, generation=2, length_bytes=len(_ROWS_B) * 8)
    assert dut.error.value == 0, f"unexpected error {int(dut.error_code.value):#04x}"
    await _assert_output(dut, _ROWS_B)


@cocotb.test()
async def an_unarmed_job_handle_is_refused(dut):
    """Generation zero is one the allocator never issues, so a host that
    starts a job without arming a handle is refused rather than quietly
    reading slot zero."""
    await _start(dut)
    await _install(dut, index=0, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=1)
    await _refused(dut, handle_id=0, generation=0, length_bytes=len(_ROWS_A) * 8, code=HANDLE_FAULT_STALE)
    await _assert_output_untouched(dut, len(_ROWS_A))


@cocotb.test()
async def an_id_the_table_does_not_have_is_refused(dut):
    """Capacity is planned and refused, never discovered: an id past the
    declared table is its own fault code, not a wrapped index into a slot
    that happens to be live."""
    await _start(dut)
    await _install(dut, index=0, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=1)
    await _refused(dut, handle_id=_CAPACITY, generation=1, length_bytes=len(_ROWS_A) * 8, code=HANDLE_FAULT_BAD_ID)
    await _assert_output_untouched(dut, len(_ROWS_A))


@cocotb.test()
async def a_job_longer_than_the_grant_is_refused(dut):
    """The grant bounds the read. Asking for more than was allocated would
    walk off the end of the region into whatever follows it, which is the
    same wrong answer by a different route."""
    await _start(dut)
    await _install(dut, index=1, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=1)
    await _refused(dut, handle_id=1, generation=1, length_bytes=(len(_ROWS_A) + 2) * 8, code=HANDLE_FAULT_OUT_OF_BOUNDS)
    await _assert_output_untouched(dut, len(_ROWS_A))


@cocotb.test()
async def a_refusal_leaves_the_pipeline_able_to_run(dut):
    """A refused job must not wedge the next one: the units never started, so
    nothing is mid-stream, and the following good handle runs clean."""
    await _start(dut)
    await _install(dut, index=3, base_word=_REGION_A_WORD, length_bytes=len(_ROWS_A) * 8, generation=5)
    await _refused(dut, handle_id=3, generation=4, length_bytes=len(_ROWS_A) * 8, code=HANDLE_FAULT_STALE)
    await _run_job(dut, handle_id=3, generation=5, length_bytes=len(_ROWS_A) * 8)
    assert dut.error.value == 0, f"unexpected error {int(dut.error_code.value):#04x}"
    await _assert_output(dut, _ROWS_A)


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_handle_table_sim_bench(tmp_path: Path):
    harness = generate_scan_composition_sim_sv(
        _bench_composition(),
        module_name="dau_handle_table_bench_sim",
        mem_words=4096,
        sources=(_SCAN_SIM_SV / "dau_test_offset_tile.sv",),
    )
    top = tmp_path / "dau_handle_table_bench_sim.v"
    top.write_text(harness)

    runner = get_runner("verilator")
    build_dir = tmp_path / "sim_build"
    runner.build(
        sources=[top, *sorted(_SCAN_SIM_SV.glob("*.sv"))],
        hdl_toplevel="dau_handle_table_bench_sim",
        always=True,
        build_dir=build_dir,
    )
    runner.test(hdl_toplevel="dau_handle_table_bench_sim", test_module="dau_build.tests.test_handle_table_sim", build_dir=build_dir)
