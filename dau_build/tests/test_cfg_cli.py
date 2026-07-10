from __future__ import annotations

from pathlib import Path
from shutil import which

import pytest

from dau_build.cli import explain, main
from dau_build.tests.test_simulate_profile_task import _write_self_contained_counter_manifest


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_cfg_cli_runs_profile_only_simulate(tmp_path: Path, capsys) -> None:
    manifest_path = _write_self_contained_counter_manifest(tmp_path)
    exit_code = main(
        [
            "task=simulate",
            "model.simulator=verilator",
            "model.spec_path=null",
            "model.module=''",
            "model.profile=counter-profile",
            f"model.profile_manifest={manifest_path}",
            f"model.output_root={tmp_path}",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "status=passed" in captured.out
    assert "profile=counter-profile" in captured.out


def test_profile_only_simulate_falls_back_to_manifest_registry() -> None:
    # unknown in dau-sim's registry -> the manifest registry is consulted;
    # its error message proves the fallback chain ran (no verilator needed:
    # resolution precedes execution)
    with pytest.raises(Exception, match="unknown DAU Verilator profile"):
        main(
            [
                "task=simulate",
                "model.simulator=verilator",
                "model.spec_path=null",
                "model.module=''",
                "model.profile=not-a-registered-profile",
            ]
        )


def test_cfg_explain_prints_resolved_config(capsys) -> None:
    exit_code = explain(["task=synthesize", "model.spec_path=spec.yaml", "model.module=m", "model.output_root=out"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "_target_: dau_build.build_steps.SynthesizeTask" in captured.out
    assert "spec_path: spec.yaml" in captured.out
    assert "callable: /model" in captured.out


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_cfg_cli_open_registration_via_config_dir_overlay(tmp_path: Path, capsys) -> None:
    # a user config overlay adds a brand-new task config without touching
    # dau-build source: open registration through hydra composition
    manifest_path = _write_self_contained_counter_manifest(tmp_path)
    overlay = tmp_path / "user-configs"
    (overlay / "task").mkdir(parents=True)
    (overlay / "task" / "smoke-sim.yaml").write_text(
        "\n".join(
            (
                "# @package model",
                "_target_: dau_build.build_steps.SimulateTask",
                "spec_path: null",
                "module: ''",
                "simulator: verilator",
                "profile: counter-profile",
                f"profile_manifest: [{manifest_path}]",
                f"output_root: {tmp_path / 'work'}",
            )
        )
    )
    exit_code = main(["--config-dir", str(overlay), "task=smoke-sim"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "profile=counter-profile" in captured.out
    assert "status=passed" in captured.out
