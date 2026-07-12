from pathlib import Path
from shutil import which

import pytest

from dau_build.build_steps import BuildStepError
from dau_build.config import run_request_config
from dau_build.tests.test_build_steps import _write_counter_testbench


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_profile_only_simulate_runs(tmp_path):
    manifest_path = _write_self_contained_counter_manifest(tmp_path)
    result = run_request_config(
        "task",
        "tasks/sim/simulate",
        overrides=[
            "simulator=simulators/verilator",
            "simulator.profile=counter-profile",
            f"simulator.profile_manifest=[{manifest_path}]",
        ],
        model_values={"output_root": str(tmp_path)},
    )
    assert "status=passed" in result.message
    assert "profile=counter-profile" in result.message


def test_simulate_without_spec_or_profile_fails_typed(tmp_path):
    with pytest.raises(BuildStepError, match="requires a spec"):
        run_request_config("task", "tasks/sim/simulate", overrides=["simulator=simulators/verilator"])


def _write_self_contained_counter_manifest(tmp_path) -> Path:
    """Profile-only runs carry all their sources in the profile: DUT + bench."""
    testbench_path = _write_counter_testbench(tmp_path)
    counter_source = Path(__file__).parent / "sv" / "counter.sv"
    profile_path = tmp_path / "counter-only-profiles.yaml"
    profile_path.write_text(
        "\n".join(
            (
                "schema: dau.simulation-profile/v0",
                "profiles:",
                "  - name: counter-profile",
                "    simulator: verilator",
                "    top_module: counter_tb",
                "    expect_stdout: DAU_BUILD_COUNTER_TB_OK",
                "    sources:",
                "      - artifact: counter-source",
                "      - artifact: counter-tb",
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "counter-only-profiles.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: counter-only-profiles",
                "artifacts:",
                "  - id: counter-profile-metadata",
                f"    path: {profile_path.name}",
                "    kind: metadata",
                "    role: simulation-profile",
                "    format: dau.simulation-profile/v0",
                "  - id: counter-source",
                f"    path: {counter_source.as_posix()}",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "  - id: counter-tb",
                f"    path: {testbench_path.as_posix()}",
                "    kind: source",
                "    role: testbench-source",
                "    language: systemverilog",
                "",
            )
        ),
        encoding="utf-8",
    )
    return manifest_path
