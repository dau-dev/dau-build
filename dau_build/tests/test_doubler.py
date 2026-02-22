"""
test_doubler.py - Cocotb testbench for the doubler AXI-Stream module.

Tests the IEEE 754 double-precision multiply-by-2 logic including
normal values, edge cases (zero, inf, NaN, denormals, max-exponent).
"""

import struct
from pathlib import Path
from shutil import which

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def double_to_bits(value: float) -> int:
    """Convert a Python float to a 64-bit IEEE 754 integer."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def bits_to_double(bits: int) -> float:
    """Convert a 64-bit IEEE 754 integer to a Python float."""
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


async def reset(dut, cycles=5):
    """Assert reset for *cycles* clock cycles."""
    dut.aresetn.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.aclk)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


async def drive_and_capture(dut, input_bits: int, last: bool = False) -> int:
    """
    Push one 64-bit value through the doubler and return the result.

    Drives s_axis, waits for m_axis output.
    """
    # Drive input
    dut.s_axis_tdata.value = input_bits
    dut.s_axis_tvalid.value = 1
    dut.s_axis_tlast.value = int(last)
    dut.m_axis_tready.value = 1

    # Wait for slave handshake
    while True:
        await RisingEdge(dut.aclk)
        if dut.s_axis_tready.value and dut.s_axis_tvalid.value:
            break

    dut.s_axis_tvalid.value = 0

    # Wait for master handshake
    while True:
        await RisingEdge(dut.aclk)
        if dut.m_axis_tvalid.value and dut.m_axis_tready.value:
            result = int(dut.m_axis_tdata.value)
            dut.m_axis_tready.value = 0
            return result


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------
@cocotb.test()
async def test_normal_doubles(dut):
    """Test doubling of typical float64 values."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    test_values = [1.0, -1.0, 0.5, -0.5, 3.14159, 1e100, -1e-100, 42.0]
    for val in test_values:
        inp = double_to_bits(val)
        out = await drive_and_capture(dut, inp)
        result = bits_to_double(out)
        expected = val * 2.0
        assert result == expected, f"Mismatch for {val}: expected {expected}, got {result}"


@cocotb.test()
async def test_zero(dut):
    """Test that +0 and -0 remain zero."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    for val in [0.0, -0.0]:
        inp = double_to_bits(val)
        out = await drive_and_capture(dut, inp)
        # Bit-exact comparison (distinguishes +0 and -0)
        assert out == inp, f"Zero mismatch for {val}: input=0x{inp:016x} output=0x{out:016x}"


@cocotb.test()
async def test_infinity(dut):
    """Test that +inf and -inf stay unchanged after doubling."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    for val in [float("inf"), float("-inf")]:
        inp = double_to_bits(val)
        out = await drive_and_capture(dut, inp)
        assert out == inp, f"Infinity mismatch: input=0x{inp:016x} output=0x{out:016x}"


@cocotb.test()
async def test_nan(dut):
    """Test that NaN passes through unchanged."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    nan_bits = double_to_bits(float("nan"))
    out = await drive_and_capture(dut, nan_bits)
    assert out == nan_bits, f"NaN mismatch: input=0x{nan_bits:016x} output=0x{out:016x}"


@cocotb.test()
async def test_overflow_to_inf(dut):
    """Test that doubling the largest finite value produces infinity."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    import sys

    max_double = sys.float_info.max  # ~1.7976931348623157e+308, exp=0x7FE
    inp = double_to_bits(max_double)
    out = await drive_and_capture(dut, inp)
    result = bits_to_double(out)
    assert result == float("inf"), f"Expected inf, got {result} (0x{out:016x})"


@cocotb.test()
async def test_stream_burst(dut):
    """Test a burst of back-to-back values through the stream."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    import random

    random.seed(42)
    values = [random.uniform(-1e6, 1e6) for _ in range(64)]

    for i, val in enumerate(values):
        inp = double_to_bits(val)
        is_last = i == len(values) - 1
        out = await drive_and_capture(dut, inp, last=is_last)
        result = bits_to_double(out)
        expected = val * 2.0
        assert result == expected, f"Burst mismatch at index {i}: input={val}, expected={expected}, got={result}"


# -------------------------------------------------------------------
# Pytest runner (verilator)
# -------------------------------------------------------------------
@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_doubler():
    from cocotb_tools.runner import get_runner

    proj_path = Path(__file__).resolve().parent / ".." / "sv"
    sources = [proj_path / "doubler.sv"]
    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        hdl_toplevel="doubler",
        always=True,
    )
    runner.test(
        hdl_toplevel="doubler",
        test_module="dau_build.tests.test_doubler",
    )


if __name__ == "__main__":
    test_doubler()
