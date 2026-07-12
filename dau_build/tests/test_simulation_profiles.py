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


def test_default_profile_manifests_are_empty() -> None:
    # dau-build is generic: it ships no profiles of its own
    assert default_profile_manifest_paths() == ()
    assert available_verilator_profiles() == ()


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


def test_unknown_profile_error_lists_available_profiles() -> None:
    with pytest.raises(SimulationProfileError, match="expected one of: $"):
        resolve_verilator_profile("missing-profile")


def test_cocotb_profile_parses_and_runs_through_simulate_task(tmp_path) -> None:
    """A simulator: cocotb profile resolves through the same manifest chain
    and task=simulate runs it via dau-sim's canonical cocotb launcher."""
    from shutil import which

    import pytest as _pytest
    from dau_sim.integrations.cocotb import CocotbProfile

    from dau_build.build_steps import CocotbSimulator, SimulateTask
    from dau_build.simulation_profiles import resolve_profile

    profiles_yaml = tmp_path / "profiles.yaml"
    profiles_yaml.write_text(
        "schema: dau.simulation-profile/v0\n"
        "profiles:\n"
        "  - name: ready-valid-sum-cocotb\n"
        "    simulator: cocotb\n"
        "    top_module: ready_valid_sum\n"
        "    test_module: dau_sim.tests.cocotb_benches.ready_valid_sum_tb\n"
        "    sources:\n"
        "      - uri: package://dau_sim/tests/sv/ready_valid_sum.sv\n"
    )
    manifest_yaml = tmp_path / "profiles.artifacts.yaml"
    manifest_yaml.write_text(
        "schema: artlink.manifest/v0\n"
        "name: test-cocotb-profiles\n"
        "intent: simulation-profiles\n"
        "artifacts:\n"
        "  - id: profiles\n"
        f"    path: {profiles_yaml.name}\n"
        "    kind: metadata\n"
        "    role: simulation-profile\n"
        "    format: dau.simulation-profile/v0\n"
    )

    profile = resolve_profile("ready-valid-sum-cocotb", profile_manifests=(manifest_yaml,))
    assert isinstance(profile, CocotbProfile)
    assert profile.hdl_toplevel == "ready_valid_sum"

    if which("verilator") is None:
        _pytest.skip("verilator not found")
    result = SimulateTask(
        simulator=CocotbSimulator(profile="ready-valid-sum-cocotb", profile_manifest=(manifest_yaml,)),
        output_root=tmp_path / "work",
    )(None)
    assert "simulator=cocotb" in result.message and "status=passed" in result.message
