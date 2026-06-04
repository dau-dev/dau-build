from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class VerilatorProfile:
    name: str
    sources: tuple[Path, ...]
    top_module: str
    expect_stdout: str


_DAU_CORE_HDL = files("dau_core.hdl")
_DAU_CORE_TESTS = files("dau_core").joinpath("tests")
_SV_TESTBENCH_DIR = _DAU_CORE_TESTS.joinpath("sv")


def _resource_path(resource) -> Path:
    return Path(str(resource))


_PROFILES: dict[str, VerilatorProfile] = {
    "dau-int32-aggregation-tile": VerilatorProfile(
        name="dau-int32-aggregation-tile",
        sources=(_resource_path(_SV_TESTBENCH_DIR.joinpath("dau_int32_aggregation_tile_tb.sv")),),
        top_module="dau_int32_aggregation_tile_tb",
        expect_stdout="DAU_INT32_AGGREGATION_TILE_TB_OK",
    ),
    "dau-int32-stream-aggregation": VerilatorProfile(
        name="dau-int32-stream-aggregation",
        sources=(
            _resource_path(_DAU_CORE_HDL.joinpath("dau_int32_aggregation_tile.sv")),
            _resource_path(_SV_TESTBENCH_DIR.joinpath("dau_int32_stream_aggregation_tb.sv")),
        ),
        top_module="dau_int32_stream_aggregation_tb",
        expect_stdout="DAU_INT32_STREAM_AGGREGATION_TB_OK",
    ),
    "dau-int32-arrow-lite-stream-aggregation": VerilatorProfile(
        name="dau-int32-arrow-lite-stream-aggregation",
        sources=(_resource_path(_SV_TESTBENCH_DIR.joinpath("dau_int32_arrow_lite_stream_aggregation_tb.sv")),),
        top_module="dau_int32_arrow_lite_stream_aggregation_tb",
        expect_stdout="DAU_INT32_ARROW_LITE_STREAM_AGGREGATION_TB_OK",
    ),
}


def available_verilator_profiles() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def resolve_verilator_profile(name: str) -> VerilatorProfile:
    try:
        return _PROFILES[name]
    except KeyError as exc:
        known = ", ".join(available_verilator_profiles())
        raise KeyError(f"unknown DAU Verilator profile {name!r}; expected one of: {known}") from exc
