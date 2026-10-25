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
            f"model.output_root={tmp_path}",
        ],
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
        "schema: dau.simulation-profile/v0\nprofiles:\n  - name: counter-profile\n    simulator: verilator\n    top_module: counter_tb\n    expect_stdout: DAU_BUILD_COUNTER_TB_OK\n    sources:\n      - artifact: counter-source\n      - artifact: counter-tb\n",
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


def _stub_verilator(monkeypatch, stdout: str) -> None:
    from types import SimpleNamespace

    import dau_sim.integrations.verilator

    monkeypatch.setattr(dau_sim.integrations.verilator, "run_verilator_testbench", lambda **_kwargs: SimpleNamespace(stdout=stdout))


def test_a_registered_dau_sim_profile_passes_on_a_clean_run(tmp_path, monkeypatch):
    # dau-sim's registered profiles carry no success marker: a run that exits
    # clean passes unless the caller names one
    _stub_verilator(monkeypatch, "done\n")
    overrides = ["simulator=simulators/verilator", "simulator.profile=ready-valid-sum", f"model.output_root={tmp_path}"]

    result = run_request_config("task", "tasks/sim/simulate", overrides=overrides)
    assert "status=passed" in result.message
    assert "marker=" not in result.message

    with pytest.raises(BuildStepError, match="did not report 'READY_OK'"):
        run_request_config("task", "tasks/sim/simulate", overrides=[*overrides, "simulator.expect_stdout=READY_OK"])
