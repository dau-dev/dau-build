from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge
from dau_core.operators import aggregate_values
from dau_core.stream import LogicalType, OperationCode
from dau_core.tests.cocotb_benches.dau_int32_record_batch_aggregation_tb import _encode_batch, _words_from_bytes

# BAR-level addresses: the register window sits at BAR offset 0x1000 on
# hardware and the module sees the full BAR offset on s_axi_*addr — driving
# window-relative addresses here would hide decode bugs the host will hit
REGISTER_WINDOW = 0x1000
ADDR_MAGIC = REGISTER_WINDOW + 0x0000
ADDR_LAST_ERROR = REGISTER_WINDOW + 0x002C
ADDR_JOB_CONTROL = REGISTER_WINDOW + 0x0050
ADDR_JOB_STATUS = REGISTER_WINDOW + 0x0054
ADDR_INPUT_LENGTH_LOW = REGISTER_WINDOW + 0x0060
ADDR_RESULT_LENGTH_LOW = REGISTER_WINDOW + 0x007C

STATUS_IDLE = 1 << 0
STATUS_BUSY = 1 << 1
STATUS_DONE = 1 << 2
STATUS_ERROR = 1 << 3


@cocotb.test()
async def mm_job_runs_sum_via_registers(dut):
    await _start(dut)
    values = [10, -3, 5, 8, 21, -100, 7]
    result_words = await _run_job(
        dut, _encode_batch([(LogicalType.INT32, values), (LogicalType.INT32, [0] * len(values))], OperationCode.SUM, input_column_id=0)
    )
    assert _scalar(result_words, signed=True) == int(aggregate_values(OperationCode.SUM, values, LogicalType.INT32))
    assert _result_type(result_words) == int(LogicalType.INT64)


@cocotb.test()
async def mm_job_repeated_jobs_without_reset(dut):
    await _start(dut)
    values = [7, -2, 5, 11, 5]
    for opcode in (OperationCode.MIN, OperationCode.MAX, OperationCode.COUNT):
        result_words = await _run_job(
            dut, _encode_batch([(LogicalType.INT32, values), (LogicalType.INT32, [1] * len(values))], opcode, input_column_id=0)
        )
        assert _scalar(result_words, signed=opcode != OperationCode.COUNT) == int(aggregate_values(opcode, values, LogicalType.INT32))


@cocotb.test()
async def mm_job_reports_descriptor_error(dut):
    await _start(dut)
    encoded = bytearray(_encode_batch([(LogicalType.INT32, [1, 2, 3, 4])], OperationCode.SUM, input_column_id=0))
    encoded[0] = 0xFF  # corrupt the magic word
    status = await _run_job_raw(dut, bytes(encoded))
    assert status & STATUS_ERROR
    assert await _read(dut, ADDR_LAST_ERROR) == 1  # descriptor error
    assert await _read(dut, ADDR_RESULT_LENGTH_LOW) == 0


@cocotb.test()
async def mm_job_rejects_unaligned_length(dut):
    await _start(dut)
    await _write(dut, ADDR_INPUT_LENGTH_LOW, 100)  # not 64-byte aligned
    await _write(dut, ADDR_JOB_CONTROL, 1)
    status = await _poll_done(dut)
    assert status & STATUS_ERROR
    assert await _read(dut, ADDR_LAST_ERROR) == 0xFE


@cocotb.test()
async def mm_job_identity_registers_visible(dut):
    await _start(dut)
    assert await _read(dut, ADDR_MAGIC) == 0x44415531
    assert (await _read(dut, ADDR_JOB_STATUS)) & STATUS_IDLE


async def _start(dut) -> None:
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start(start_high=False))
    dut.rstn.value = 0
    for name in (
        "s_axi_awvalid",
        "s_axi_wvalid",
        "s_axi_bready",
        "s_axi_arvalid",
        "s_axi_rready",
        "load_en",
        "load_addr",
        "load_data",
        "peek_addr",
        "s_axi_awaddr",
        "s_axi_wdata",
        "s_axi_wstrb",
        "s_axi_araddr",
    ):
        getattr(dut, name).value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rstn.value = 1
    await RisingEdge(dut.clk)


async def _load_batch(dut, encoded: bytes) -> None:
    words = _words_from_bytes(encoded)
    for index, word in enumerate(words):
        dut.load_en.value = 1
        dut.load_addr.value = index
        dut.load_data.value = word
        await RisingEdge(dut.clk)
    dut.load_en.value = 0
    await RisingEdge(dut.clk)


async def _run_job_raw(dut, encoded: bytes) -> int:
    await _load_batch(dut, encoded)
    await _write(dut, ADDR_INPUT_LENGTH_LOW, len(encoded))
    await _write(dut, ADDR_JOB_CONTROL, 1)
    return await _poll_done(dut)


async def _run_job(dut, encoded: bytes) -> list[int]:
    status = await _run_job_raw(dut, encoded)
    assert not (status & STATUS_ERROR), f"job errored: status=0x{status:x}"
    result_length = await _read(dut, ADDR_RESULT_LENGTH_LOW)
    assert result_length == 136, f"unexpected result length {result_length}"
    words = []
    for index in range((result_length + 7) // 8):
        dut.peek_addr.value = index
        await RisingEdge(dut.clk)
        await ReadOnly()
        words.append(int(dut.peek_data.value))
        await RisingEdge(dut.clk)
    return words


async def _poll_done(dut) -> int:
    for _ in range(4000):
        status = await _read(dut, ADDR_JOB_STATUS)
        if status & STATUS_DONE:
            return status
    raise AssertionError("timed out waiting for job done")


async def _write(dut, addr: int, data: int) -> None:
    # bvalid pulses for exactly one cycle (bready pre-asserted), possibly the
    # same cycle the aw/w handshake completes, so track both in one loop.
    dut.s_axi_awaddr.value = addr
    dut.s_axi_awvalid.value = 1
    dut.s_axi_wdata.value = data
    dut.s_axi_wstrb.value = 0xF
    dut.s_axi_wvalid.value = 1
    dut.s_axi_bready.value = 1
    for _ in range(64):
        await ReadOnly()
        aw_fired = int(dut.s_axi_awready.value) == 1 and int(dut.s_axi_wready.value) == 1
        b_seen = int(dut.s_axi_bvalid.value) == 1
        await RisingEdge(dut.clk)
        if aw_fired:
            dut.s_axi_awvalid.value = 0
            dut.s_axi_wvalid.value = 0
        if b_seen:
            dut.s_axi_bready.value = 0
            return
    raise AssertionError("axi write timeout")


async def _read(dut, addr: int) -> int:
    dut.s_axi_araddr.value = addr
    dut.s_axi_arvalid.value = 1
    dut.s_axi_rready.value = 1
    for _ in range(64):
        await ReadOnly()
        ar_fired = int(dut.s_axi_arready.value) == 1
        r_seen = int(dut.s_axi_rvalid.value) == 1
        data = int(dut.s_axi_rdata.value)
        await RisingEdge(dut.clk)
        if ar_fired:
            dut.s_axi_arvalid.value = 0
        if r_seen:
            dut.s_axi_rready.value = 0
            return data
    raise AssertionError("axi read timeout")


def _scalar(words: list[int], *, signed: bool) -> int:
    value = words[16]
    if signed and value >= 1 << 63:
        value -= 1 << 64
    return value


def _result_type(words: list[int]) -> int:
    return (words[8] >> 16) & 0xFFFF
