from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from dau_build.build_steps import BuildShellProjectTask, BuildStepError, FlashTask
from dau_build.packaging import load_artifact_manifest
from dau_build.shell_build import (
    SHELL_BUILD_MANIFEST_NAME,
    ShellBuildError,
    ShellBuildStatus,
    parse_shell_build_console,
    run_shell_project_build,
    shell_build_manifest,
    write_shell_build_manifest,
)


def _fake_shell_output(tmp_path: Path) -> Path:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "dau_mm_job.bit").write_bytes(b"\x00\x01bitstream")
    (output_root / "utilization_mm.rpt").write_text("| Slice LUTs | 30805 |\n")
    (output_root / "timing_mm.rpt").write_text("WNS 0.459\n")
    (output_root / "console.log").write_text("DAU_MM_JOB_BUILD_OK wns=0.459\n")
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    (output_root / "constraints.xdc").write_text("# pins\n")
    return output_root


def _fake_vivado(tmp_path: Path) -> Path:
    vivado = tmp_path / "fake-vivado"
    vivado.write_text("#!/bin/sh\necho 'DAU_MM_JOB_BUILD_OK wns=0.123'\ntouch dau_mm_job.bit\n")
    vivado.chmod(vivado.stat().st_mode | stat.S_IXUSR)
    return vivado


def test_parse_console_markers() -> None:
    assert parse_shell_build_console("noise\nDAU_MM_JOB_BUILD_OK wns=0.321\n") == ShellBuildStatus(build_status="built", wns_ns=0.321)
    assert parse_shell_build_console("DAU_MM_JOB_BUILD_FAILED synthesis\n") == ShellBuildStatus(build_status="failed", failed_stage="synthesis")
    assert parse_shell_build_console("vivado died\n") == ShellBuildStatus(build_status="unknown")


def test_manifest_packages_outputs_with_digests(tmp_path: Path) -> None:
    output_root = _fake_shell_output(tmp_path)
    source = tmp_path / "tile.sv"
    source.write_text("module tile; endmodule\n")

    manifest = shell_build_manifest(output_root, name="dpv1-bar-noc", source_paths=(source,), metadata={"wns_ns": 0.459, "build_status": "built"})

    roles = sorted(artifact.role for artifact in manifest.artifacts)
    assert roles.count("bitstream") == 1
    assert "report" in roles and "build-log" in roles and "generated-project-input" in roles and "hdl-source" in roles
    bitstream = next(artifact for artifact in manifest.artifacts if artifact.role == "bitstream")
    assert bitstream.digest is not None
    assert bitstream.digest.value == hashlib.sha256((output_root / "dau_mm_job.bit").read_bytes()).hexdigest()
    assert manifest.metadata["wns_ns"] == 0.459
    # contributing sources are digested too — the provenance record
    hdl = next(artifact for artifact in manifest.artifacts if artifact.role == "hdl-source")
    assert hdl.digest is not None


def test_manifest_requires_bitstream(tmp_path: Path) -> None:
    output_root = tmp_path / "empty"
    output_root.mkdir()
    with pytest.raises(ShellBuildError):
        shell_build_manifest(output_root, name="x")


def test_run_build_with_stub_vivado(tmp_path: Path) -> None:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    status = run_shell_project_build(output_root, vivado_executable=str(_fake_vivado(tmp_path)))
    assert status.build_status == "built"
    assert status.wns_ns == 0.123
    assert (output_root / "dau_mm_job.bit").is_file()


def test_run_build_raises_on_failure_marker(tmp_path: Path) -> None:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    vivado = tmp_path / "fail-vivado"
    vivado.write_text("#!/bin/sh\necho 'DAU_MM_JOB_BUILD_FAILED implementation'\nexit 1\n")
    vivado.chmod(vivado.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(ShellBuildError, match="implementation"):
        run_shell_project_build(output_root, vivado_executable=str(vivado))


def test_task_plan_mode_does_not_execute(tmp_path: Path) -> None:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    result = BuildShellProjectTask(output_root=output_root, vivado="definitely-not-vivado")(None)
    assert "status=planned" in result.message
    assert not (output_root / "dau_mm_job.bit").exists()


def test_task_execute_builds_and_writes_manifest(tmp_path: Path) -> None:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    result = BuildShellProjectTask(
        output_root=output_root,
        vivado=str(_fake_vivado(tmp_path)),
        manifest_name="dpv1-test",
        metadata={"shell": "bar-noc"},
        execute=True,
    )(None)
    assert "status=built" in result.message
    manifest = load_artifact_manifest(output_root / SHELL_BUILD_MANIFEST_NAME)
    assert manifest.metadata["build_status"] == "built"
    assert manifest.metadata["shell"] == "bar-noc"
    assert manifest.metadata["wns_ns"] == 0.123


def test_flash_task_resolves_and_verifies_shell_build_manifest(tmp_path: Path) -> None:
    output_root = _fake_shell_output(tmp_path)
    manifest_path = write_shell_build_manifest(output_root, name="dpv1-test", metadata={"build_status": "built"})

    result = FlashTask(manifest_path=manifest_path)(None)
    assert "dau_mm_job.bit" in result.message

    # a tampered bitstream must be refused
    (output_root / "dau_mm_job.bit").write_bytes(b"tampered")
    with pytest.raises(BuildStepError, match="digest mismatch"):
        FlashTask(manifest_path=manifest_path)(None)


def test_flash_task_rejects_unbuilt_manifest(tmp_path: Path) -> None:
    output_root = _fake_shell_output(tmp_path)
    manifest_path = write_shell_build_manifest(output_root, name="dpv1-test", metadata={"build_status": "failed"})
    with pytest.raises(BuildStepError, match="not built"):
        FlashTask(manifest_path=manifest_path)(None)


@pytest.mark.skipif(os.name != "posix", reason="stub executables require posix")
def test_task_reachable_from_config_tree() -> None:
    from dau_build.build_steps import available_task_names

    assert "tasks/build/build-shell-project" in available_task_names()


def test_overlay_build_manifest_packages_built_runs_only(tmp_path: Path) -> None:
    from dau_build.shell_build import write_overlay_build_manifest

    work = tmp_path / "work"
    work.mkdir()
    (work / "overlay.bit").write_bytes(b"\x01\x02")
    (work / "util.rpt").write_text("luts\n")
    (work / "vivado.log").write_text("done\n")
    kv = work / "dau-vivado.manifest"

    kv.write_text("build_status=planned\nbitstream=overlay.bit\n")
    assert write_overlay_build_manifest(work, kv, name="dau-vivado") is None

    kv.write_text("build_status=built\nbitstream=overlay.bit\nresource_summary=util.rpt\nvivado_log=vivado.log\n")
    packaged = write_overlay_build_manifest(work, kv, name="dau-vivado")
    manifest = load_artifact_manifest(packaged)
    assert manifest.metadata["build_status"] == "built"
    roles = [artifact.role for artifact in manifest.artifacts]
    assert roles.count("bitstream") == 1 and "report" in roles and "build-log" in roles
    bitstream = next(a for a in manifest.artifacts if a.role == "bitstream")
    assert bitstream.digest is not None
