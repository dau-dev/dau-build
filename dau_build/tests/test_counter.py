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


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_counter(tmp_path: Path):
    proj_path = Path(__file__).resolve().parent / "sv"
    sources = [proj_path / "counter.sv"]
    runner = get_runner("verilator")
    build_dir = tmp_path / "sim_build"
    runner.build(
        sources=sources,
        hdl_toplevel="counter",
        always=True,
        build_dir=build_dir,
    )

    runner.test(hdl_toplevel="counter", test_module="dau_build.tests.test_counter,", build_dir=build_dir)


if __name__ == "__main__":
    test_counter()
