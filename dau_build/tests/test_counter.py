from pathlib import Path
from shutil import which

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb_tools.runner import get_runner


@cocotb.test()
async def simple_test(dut):
    """Test that d propagates to q"""

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start(start_high=False))

    await RisingEdge(dut.clk)

    expected_val = 1
    for i in range(10):
        await RisingEdge(dut.clk)
        assert dut.out.value == expected_val, f"output out was incorrect on the {i}th cycle"
        expected_val += 1


@pytest.mark.skipif(which("verilator") is None, reason="vivado not found")
def test_counter():
    proj_path = Path(__file__).resolve().parent / "sv"
    sources = [proj_path / "counter.sv"]
    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        hdl_toplevel="counter",
        always=True,
    )

    runner.test(hdl_toplevel="counter", test_module="dau_build.tests.test_counter,")


if __name__ == "__main__":
    test_counter()
