from __future__ import annotations

import cocotb
from cocotb.clock import Clock
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

# (time_seconds, price): zero prices are ineligible; buckets are minutes
_TRADES = [
    (34200, 100),
    (34210, 0),
    (34215, 105),
    (34259, 95),  # minute 570 (one dropped)
    (34260, 102),
    (34290, 0),
    (34319, 99),  # minute 571 (one dropped)
    (34320, -3),  # minute 572 (dropped: not > 0)
]
_PREDICATE = FilterPredicate(PredicateOp.GT, 0)
_BUCKET = MapConfig(op=MapOp.MULSHR, constant=139811, shift=23)


def _golden(trades):
    eligible = filter_rows(trades, _PREDICATE)
    keyed = map_rows(eligible, _BUCKET, on_key=True)
    return grouped_aggregate_rows(keyed)


@cocotb.test()
async def pipeline_computes_ohlcv_bars(dut):
    await _start(dut)
    bars = await _run_pipeline(dut, _TRADES)
    golden = _golden(_TRADES)
    assert bars == golden
    assert [bar.key for bar in bars] == [570, 571]


@cocotb.test()
async def pipeline_survives_stall_injection(dut):
    await _start(dut)
    bars = await _run_pipeline(dut, _TRADES, ready_pattern=(1, 0, 0, 1, 0), input_gap_pattern=(0, 2, 0, 5))
    assert bars == _golden(_TRADES)


@cocotb.test()
async def pipeline_empty_selection_completes_with_zero_groups(dut):
    await _start(dut)
    trades = [(34200, 0), (34210, -5), (34260, 0)]
    bars = await _run_pipeline(dut, trades, expect_output=False)
    assert bars == []
    assert _golden(trades) == []


@cocotb.test()
async def pipeline_single_trade_single_bar(dut):
    await _start(dut)
    trades = [(34200, 100)]
    bars = await _run_pipeline(dut, trades)
    assert bars == [GroupAggregate(key=570, first=100, last=100, minimum=100, maximum=100, total=100, count=1)]


@cocotb.test()
async def pipeline_repeated_batches_relatch_config(dut):
    await _start(dut)
    assert await _run_pipeline(dut, _TRADES) == _golden(_TRADES)
    trades = [(57599, 500), (57600, 501)]  # minutes 959 and 960
    bars = await _run_pipeline(dut, trades)
    assert [bar.key for bar in bars] == [959, 960]


@cocotb.test()
async def pipeline_filter_config_error_propagates(dut):
    await _start(dut)
    _apply_config(dut, row_count=2)
    dut.filter_op0.value = 0  # reserved predicate opcode
    dut.output_ready.value = 1
    await _send_rows(dut, [(34200, 100), (34210, 105)])
    await _expect_error(dut, expected_code=1)


async def _start(dut) -> None:
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start(start_high=False))
    dut.rst.value = 1
    dut.input_valid.value = 0
    dut.input_data.value = 0
    dut.input_last.value = 0
    dut.output_ready.value = 0
    dut.status_ready.value = 0
    for name in (
        "filter_target_lane",
        "filter_op0",
        "filter_constant0",
        "filter_combine",
        "filter_op1",
        "filter_constant1",
        "row_count",
        "map_target_lane",
        "map_op",
        "map_constant",
        "map_shift",
    ):
        getattr(dut, name).value = 0
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def _apply_config(dut, *, row_count: int) -> None:
    # filter: predicate on the value lane (price)
    dut.filter_target_lane.value = 0
    dut.filter_op0.value = int(_PREDICATE.op0)
    dut.filter_constant0.value = _PREDICATE.constant0 & 0xFFFFFFFF
    dut.filter_combine.value = int(_PREDICATE.combine)
    dut.filter_op1.value = int(_PREDICATE.op1)
    dut.filter_constant1.value = _PREDICATE.constant1 & 0xFFFFFFFF
    dut.row_count.value = row_count
    # map: bucket derivation on the key lane (time -> minute)
    dut.map_target_lane.value = 1
    dut.map_op.value = int(_BUCKET.op)
    dut.map_constant.value = _BUCKET.constant & 0xFFFFFFFF
    dut.map_shift.value = _BUCKET.shift


async def _run_pipeline(dut, trades, *, ready_pattern=(1,), input_gap_pattern=(0,), expect_output=True):
    _apply_config(dut, row_count=len(trades))
    send = cocotb.start_soon(_send_rows(dut, trades, gap_pattern=input_gap_pattern))
    words = await _collect_output(dut, ready_pattern) if expect_output else []
    await send
    await _accept_success_status(dut)
    assert len(words) % 5 == 0
    return [_decode_group(words[offset : offset + 5]) for offset in range(0, len(words), 5)]


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


async def _send_rows(dut, rows, *, gap_pattern=(0,)) -> None:
    gap_index = 0
    for index, (key, value) in enumerate(rows):
        for _ in range(gap_pattern[gap_index % len(gap_pattern)]):
            dut.input_valid.value = 0
            await RisingEdge(dut.clk)
        gap_index += 1
        dut.input_data.value = ((value & 0xFFFFFFFF) << 32) | (key & 0xFFFFFFFF)
        dut.input_last.value = int(index == len(rows) - 1)
        dut.input_valid.value = 1
        while True:
            await ReadOnly()
            fired = int(dut.input_ready.value) == 1
            await RisingEdge(dut.clk)
            if fired:
                break
    dut.input_valid.value = 0
    dut.input_last.value = 0


async def _collect_output(dut, ready_pattern) -> list[int]:
    words: list[int] = []
    pattern_index = 0
    for _ in range(8192):
        ready = ready_pattern[pattern_index % len(ready_pattern)]
        pattern_index += 1
        dut.output_ready.value = ready
        await ReadOnly()
        fired = ready == 1 and int(dut.output_valid.value) == 1
        word = int(dut.output_data.value)
        last = int(dut.output_last.value)
        await RisingEdge(dut.clk)
        if fired:
            words.append(word)
            if last == 1:
                dut.output_ready.value = 0
                return words
    raise AssertionError("timed out collecting pipeline output")


async def _accept_success_status(dut) -> None:
    dut.status_ready.value = 1
    for _ in range(128):
        if int(dut.status_valid.value) == 1:
            assert int(dut.status_error.value) == 0
            await RisingEdge(dut.clk)
            dut.status_ready.value = 0
            return
        await RisingEdge(dut.clk)
    raise AssertionError("timed out waiting for pipeline success status")


async def _expect_error(dut, *, expected_code: int) -> None:
    dut.status_ready.value = 1
    for _ in range(128):
        if int(dut.status_valid.value) == 1:
            assert int(dut.status_error.value) == 1
            assert int(dut.status_error_code.value) == expected_code
            await RisingEdge(dut.clk)
            dut.status_ready.value = 0
            return
        await RisingEdge(dut.clk)
    raise AssertionError("timed out waiting for pipeline error status")


def _signed32(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


def _signed64(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value
