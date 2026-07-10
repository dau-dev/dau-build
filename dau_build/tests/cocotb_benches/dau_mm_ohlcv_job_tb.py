from __future__ import annotations

import struct

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge
from dau_core.operators import (
    FilterPredicate,
    GroupAggregate,
    MapConfig,
    MapOp,
    PredicateOp,
    filter_rows,
    grouped_aggregate_rows,
    map_rows,
)
from dau_core.registers import RegisterOffset

from dau_build.tests.cocotb_benches.dau_mm_stream_job_tb import (
    REGISTER_WINDOW,
    STATUS_ERROR,
    STATUS_IDLE,
    _load_batch,
    _poll_done,
    _read,
    _start,
    _write,
)

ADDR_JOB_CONTROL = REGISTER_WINDOW + int(RegisterOffset.JOB_CONTROL)
ADDR_JOB_STATUS = REGISTER_WINDOW + int(RegisterOffset.JOB_STATUS)
ADDR_LAST_ERROR = REGISTER_WINDOW + int(RegisterOffset.LAST_ERROR)
ADDR_INPUT_LENGTH_LOW = REGISTER_WINDOW + int(RegisterOffset.INPUT_LENGTH_LOW)
ADDR_RESULT_LENGTH_LOW = REGISTER_WINDOW + int(RegisterOffset.RESULT_LENGTH_LOW)
ADDR_FILTER_OP = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_FILTER_OP)
ADDR_FILTER_CONSTANT0 = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_FILTER_CONSTANT0)
ADDR_FILTER_CONSTANT1 = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_FILTER_CONSTANT1)
ADDR_MAP_OP = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_MAP_OP)
ADDR_MAP_CONSTANT = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_MAP_CONSTANT)
ADDR_SELECTED_COUNT_LOW = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_SELECTED_COUNT_LOW)
ADDR_GROUP_COUNT_LOW = REGISTER_WINDOW + int(RegisterOffset.PIPELINE_GROUP_COUNT_LOW)

_GROUP_RECORD_WORDS = 5

_TRADES = [
    (34200, 100),
    (34210, 0),
    (34215, 105),
    (34259, 95),
    (34260, 102),
    (34290, 0),
    (34319, 99),
    (34320, -3),
]
_PREDICATE = FilterPredicate(PredicateOp.GT, 0)
_BUCKET = MapConfig(op=MapOp.MULSHR, constant=139811, shift=23)


def _golden(trades):
    eligible = filter_rows(trades, _PREDICATE)
    keyed = map_rows(eligible, _BUCKET, on_key=True)
    return grouped_aggregate_rows(keyed)


@cocotb.test()
async def ohlcv_job_computes_bars_via_registers(dut):
    await _start(dut)
    await _configure(dut)
    bars, selected, groups = await _run_job(dut, _TRADES)
    golden = _golden(_TRADES)
    assert bars == golden
    assert selected == 5
    assert groups == len(golden)


@cocotb.test()
async def ohlcv_job_repeated_batches(dut):
    await _start(dut)
    await _configure(dut)
    first, _, _ = await _run_job(dut, _TRADES)
    assert first == _golden(_TRADES)
    trades = [(57599, 500), (57600, 501)]
    second, _, groups = await _run_job(dut, trades)
    assert [bar.key for bar in second] == [959, 960]
    assert groups == 2


@cocotb.test()
async def ohlcv_job_empty_selection_reports_zero_result(dut):
    await _start(dut)
    await _configure(dut)
    trades = [(34200, 0), (34210, -5)]
    bars, selected, groups = await _run_job(dut, trades)
    assert bars == []
    assert selected == 0
    assert groups == 0


@cocotb.test()
async def ohlcv_job_error_then_recovery(dut):
    await _start(dut)
    await _configure(dut)
    await _write(dut, ADDR_FILTER_OP, 0)  # reserved predicate opcode
    await _load_batch(dut, _pack_rows(_TRADES))
    await _write(dut, ADDR_INPUT_LENGTH_LOW, len(_TRADES) * 8)
    await _write(dut, ADDR_JOB_CONTROL, 1)
    status = await _poll_done(dut)
    assert status & STATUS_ERROR
    assert (await _read(dut, ADDR_LAST_ERROR)) == 1  # CONFIG from the filter stage

    # the shell pulses the pipeline reset after an error status: a follow-up
    # good job must succeed without a host-side reset
    await _configure(dut)
    bars, _, _ = await _run_job(dut, _TRADES)
    assert bars == _golden(_TRADES)
    assert (await _read(dut, ADDR_JOB_STATUS)) & STATUS_IDLE


async def _configure(dut) -> None:
    filter_op = int(_PREDICATE.op0) | (int(_PREDICATE.combine) << 4) | (int(_PREDICATE.op1) << 8) | (0 << 16)
    await _write(dut, ADDR_FILTER_OP, filter_op)
    await _write(dut, ADDR_FILTER_CONSTANT0, _PREDICATE.constant0 & 0xFFFFFFFF)
    await _write(dut, ADDR_FILTER_CONSTANT1, _PREDICATE.constant1 & 0xFFFFFFFF)
    map_op = int(_BUCKET.op) | (_BUCKET.shift << 8) | (1 << 16)  # map the key lane
    await _write(dut, ADDR_MAP_OP, map_op)
    await _write(dut, ADDR_MAP_CONSTANT, _BUCKET.constant & 0xFFFFFFFF)


def _pack_rows(rows) -> bytes:
    return b"".join(struct.pack("<iI", key, value & 0xFFFFFFFF) for key, value in rows)


async def _run_job(dut, trades):
    await _load_batch(dut, _pack_rows(trades))
    await _write(dut, ADDR_INPUT_LENGTH_LOW, len(trades) * 8)
    await _write(dut, ADDR_JOB_CONTROL, 1)
    status = await _poll_done(dut)
    if status & STATUS_ERROR:
        last_error = await _read(dut, ADDR_LAST_ERROR)
        raise AssertionError(f"job errored: status=0x{status:x} last_error={last_error}")
    result_length = await _read(dut, ADDR_RESULT_LENGTH_LOW)
    assert result_length % (_GROUP_RECORD_WORDS * 8) == 0
    words = []
    for index in range(result_length // 8):
        dut.peek_addr.value = index
        await RisingEdge(dut.clk)
        await ReadOnly()
        words.append(int(dut.peek_data.value))
        await RisingEdge(dut.clk)
    selected = await _read(dut, ADDR_SELECTED_COUNT_LOW)
    groups = await _read(dut, ADDR_GROUP_COUNT_LOW)
    bars = [_decode_group(words[offset : offset + _GROUP_RECORD_WORDS]) for offset in range(0, len(words), _GROUP_RECORD_WORDS)]
    return bars, selected, groups


def _decode_group(words: list[int]) -> GroupAggregate:
    return GroupAggregate(
        key=_signed32(words[0] & 0xFFFFFFFF),
        first=_signed32(words[0] >> 32),
        last=_signed32(words[1] & 0xFFFFFFFF),
        minimum=_signed32(words[1] >> 32),
        maximum=_signed32(words[2] & 0xFFFFFFFF),
        total=_signed64(words[3]),
        count=words[4],
    )


def _signed32(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


def _signed64(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value
