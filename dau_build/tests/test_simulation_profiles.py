from __future__ import annotations

from pathlib import Path

import pytest

from dau_build.simulation_profiles import (
    SIMULATION_PROFILE_SCHEMA,
    SimulationProfileError,
    available_verilator_profiles,
    default_profile_manifest_paths,
    load_verilator_profiles_from_manifest,
    resolve_verilator_profile,
)


def test_default_verilator_profiles_are_loaded_from_packaged_artlink_manifest() -> None:
    assert default_profile_manifest_paths()[0].is_file()
    assert "dau_core.tests.sv" not in default_profile_manifest_paths()[0].read_text(encoding="utf-8")
    assert available_verilator_profiles() == (
        "dau-int32-aggregation-tile",
        "dau-int32-arrow-lite-stream-aggregation",
        "dau-int32-stream-aggregation",
    )

    profile = resolve_verilator_profile("dau-int32-aggregation-tile")

    assert profile.top_module == "dau_int32_aggregation_tile_tb"
    assert profile.expect_stdout == "DAU_INT32_AGGREGATION_TILE_TB_OK"
    assert len(profile.sources) == 1
    assert profile.sources[0].name == "dau_int32_aggregation_tile_tb.sv"
    assert "dau_build/profiles/sv" in profile.sources[0].as_posix()
    assert profile.sources[0].is_file()


def test_custom_verilator_profile_loads_sources_from_artlink_manifest(tmp_path: Path) -> None:
    testbench = tmp_path / "counter_tb.sv"
    testbench.write_text("module counter_tb; endmodule\n", encoding="utf-8")
    profile_yaml = tmp_path / "profiles.yaml"
    profile_yaml.write_text(
        "\n".join(
            (
                f"schema: {SIMULATION_PROFILE_SCHEMA}",
                "profiles:",
                "  - name: counter-profile",
                "    simulator: verilator",
                "    top_module: counter_tb",
                "    expect_stdout: COUNTER_OK",
                "    sources:",
                "      - artifact: counter-tb",
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "profiles.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: counter-profiles",
                "artifacts:",
                "  - id: counter-profile-metadata",
                "    path: profiles.yaml",
                "    kind: metadata",
                "    role: simulation-profile",
                f"    format: {SIMULATION_PROFILE_SCHEMA}",
                "    provides:",
                "      - kind: simulation-profile",
                "        name: counter-profile",
                "  - id: counter-tb",
                "    path: counter_tb.sv",
                "    kind: source",
                "    role: testbench-source",
                "    language: systemverilog",
                "",
            )
        ),
        encoding="utf-8",
    )

    profiles = load_verilator_profiles_from_manifest(manifest_path)

    assert tuple(profiles) == ("counter-profile",)
    assert profiles["counter-profile"].sources == (testbench.resolve(),)


def test_unknown_profile_error_names_available_profiles() -> None:
    with pytest.raises(SimulationProfileError, match="dau-int32-aggregation-tile"):
        resolve_verilator_profile("missing-profile")
