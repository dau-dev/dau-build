from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge
from dau_core.operators import aggregate_values
from dau_core.stream import LogicalType, OperationCode
from dau_core.tests.cocotb_benches.dau_int32_record_batch_aggregation_tb import _encode_batch, _words_from_bytes


@cocotb.test()
async def axis_wrapper_aggregates_and_reports_success(dut):
    await _start(dut)
    values = [10, -3, 5, 8]
    columns = [(LogicalType.INT32, values), (LogicalType.INT32, [0] * len(values))]
    await _send(dut, _words_from_bytes(_encode_batch(columns, OperationCode.SUM, input_column_id=0)))
    result_words = await _collect(dut)

    assert result_words[16] == int(aggregate_values(OperationCode.SUM, values, LogicalType.INT32))
    assert int(dut.m_axis_tkeep.value) == 0xFF
    # the auto-drained status pulse lands a cycle or two after the last beat
    for _ in range(8):
        await RisingEdge(dut.aclk)
        if int(dut.batches_completed.value) == 1:
            break
    assert int(dut.batches_completed.value) == 1
    assert int(dut.status_error_sticky.value) == 0


@cocotb.test()
async def axis_wrapper_latches_sticky_error(dut):
    await _start(dut)
    words = _words_from_bytes(_encode_batch([(LogicalType.INT32, [1, 2])], OperationCode.SUM, input_column_id=5))
    await _send(dut, words)
    for _ in range(64):
        await RisingEdge(dut.aclk)
        if int(dut.status_error_sticky.value) == 1:
            break
    assert int(dut.status_error_sticky.value) == 1
    assert int(dut.status_error_code_sticky.value) == 1  # descriptor error
    assert int(dut.batches_completed.value) == 1


async def _start(dut) -> None:
    clock = Clock(dut.aclk, 10, unit="ns")
    cocotb.start_soon(clock.start(start_high=False))
    dut.aresetn.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tkeep.value = 0xFF
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 1
    await RisingEdge(dut.aclk)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


async def _send(dut, words: list[int]) -> None:
    for index, word in enumerate(words):
        dut.s_axis_tdata.value = word
        dut.s_axis_tlast.value = int(index == len(words) - 1)
        dut.s_axis_tvalid.value = 1
        while True:
            await ReadOnly()
            fired = int(dut.s_axis_tready.value) == 1
            await RisingEdge(dut.aclk)
            if fired:
                break
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value = 0


async def _collect(dut) -> list[int]:
    words: list[int] = []
    for _ in range(256):
        await ReadOnly()
        fired = int(dut.m_axis_tvalid.value) == 1
        word = int(dut.m_axis_tdata.value)
        last = int(dut.m_axis_tlast.value)
        await RisingEdge(dut.aclk)
        if fired:
            words.append(word)
            if last:
                return words
    raise AssertionError("timed out collecting AXIS result stream")
