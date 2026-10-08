from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from dau_build.build_steps import BuildShellProjectTask
from dau_build.packaging import load_artifact_manifest
from dau_build.shell_build import (
    SHELL_BUILD_MANIFEST_NAME,
    ShellBuildError,
    ShellBuildStatus,
    parse_shell_build_console,
    run_shell_project_build,
    shell_build_manifest,
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


def test_a_timing_miss_is_its_own_status_whatever_marker_printed_it() -> None:
    """The guard in the generated script prints TIMING_FAILED; a script
    generated before the guard printed OK with a negative slack, and an
    unreadable slack proves nothing either way. All three are timing-failed:
    the number is the proof, not the marker."""
    assert parse_shell_build_console("DAU_MM_JOB_BUILD_TIMING_FAILED wns=-0.525\n") == ShellBuildStatus(build_status="timing-failed", wns_ns=-0.525)
    assert parse_shell_build_console("DAU_MM_JOB_BUILD_OK wns=-0.004\n") == ShellBuildStatus(build_status="timing-failed", wns_ns=-0.004)
    assert parse_shell_build_console("DAU_MM_JOB_BUILD_OK wns=\n") == ShellBuildStatus(build_status="timing-failed", wns_ns=None)
    assert parse_shell_build_console("DAU_MM_JOB_BUILD_OK wns=nan\n") == ShellBuildStatus(build_status="timing-failed", wns_ns=None)
    assert parse_shell_build_console("DAU_MM_JOB_BUILD_OK wns=0.0\n") == ShellBuildStatus(build_status="built", wns_ns=0.0)


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


def test_run_build_returns_the_failure_status(tmp_path: Path) -> None:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    vivado = tmp_path / "fail-vivado"
    vivado.write_text("#!/bin/sh\necho 'DAU_MM_JOB_BUILD_FAILED implementation'\nexit 1\n")
    vivado.chmod(vivado.stat().st_mode | stat.S_IXUSR)
    status = run_shell_project_build(output_root, vivado_executable=str(vivado))
    assert (status.build_status, status.failed_stage, status.return_code) == ("failed", "implementation", 1)


def test_an_ok_marker_with_a_bad_exit_is_not_proof(tmp_path: Path) -> None:
    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    vivado = tmp_path / "odd-vivado"
    vivado.write_text("#!/bin/sh\necho 'DAU_MM_JOB_BUILD_OK wns=0.1'\nexit 3\n")
    vivado.chmod(vivado.stat().st_mode | stat.S_IXUSR)
    assert run_shell_project_build(output_root, vivado_executable=str(vivado)).build_status == "unknown"


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


def test_task_execute_refuses_a_placeholder_platform(tmp_path: Path) -> None:
    """A board whose hardware-derived values are placeholders may generate
    and plan, never build."""
    from dau_build.platforms import PlaceholderPlatformError
    from dau_build.tests.platform_fixtures import probe_platform

    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    placeholder = probe_platform().model_copy(update={"name": "probe", "placeholders": ("host_link.xdma_personality",)})
    task = BuildShellProjectTask(output_root=output_root, vivado=str(_fake_vivado(tmp_path)), platform=placeholder, execute=True)
    with pytest.raises(PlaceholderPlatformError, match="xdma_personality"):
        task(None)
    assert not (output_root / "dau_mm_job.bit").exists()
    # planning stays open, and a measured platform builds
    planned = BuildShellProjectTask(output_root=output_root, vivado="definitely-not-vivado", platform=placeholder)(None)
    assert "status=planned" in planned.message
    built = BuildShellProjectTask(output_root=output_root, vivado=str(_fake_vivado(tmp_path)), platform=probe_platform(), execute=True)(None)
    assert "status=built" in built.message


@pytest.mark.skipif(os.name != "posix", reason="stub executables require posix")
def test_task_reachable_from_config_tree() -> None:
    from dau_build.build_steps import available_task_names

    assert "tasks/build/build-shell-project" in available_task_names()


def test_task_execute_records_a_timing_miss_and_refuses(tmp_path: Path) -> None:
    """A build that routed but missed timing leaves a bitstream nobody should
    flash. The task records the manifest as timing-failed (the design cache
    and the hardware plans read it) and still fails."""
    from dau_build.build_steps import BuildStepError

    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    vivado = tmp_path / "late-vivado"
    vivado.write_text("#!/bin/sh\ntouch dau_mm_job.bit\necho 'DAU_MM_JOB_BUILD_TIMING_FAILED wns=-0.525'\nexit 1\n")
    vivado.chmod(vivado.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(BuildStepError, match="timing-failed") as exc_info:
        BuildShellProjectTask(output_root=output_root, vivado=str(vivado), manifest_name="dpv1-test", execute=True)(None)

    manifest = load_artifact_manifest(output_root / SHELL_BUILD_MANIFEST_NAME)
    assert manifest.metadata["build_status"] == "timing-failed"
    assert manifest.metadata["wns_ns"] == -0.525
    assert str(output_root / SHELL_BUILD_MANIFEST_NAME) in str(exc_info.value)


def test_task_execute_refuses_a_failed_build_with_no_manifest(tmp_path: Path) -> None:
    from dau_build.build_steps import BuildStepError

    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    vivado = tmp_path / "fail-vivado"
    vivado.write_text("#!/bin/sh\necho 'DAU_MM_JOB_BUILD_FAILED synthesis'\nexit 1\n")
    vivado.chmod(vivado.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(BuildStepError, match="synthesis"):
        BuildShellProjectTask(output_root=output_root, vivado=str(vivado), execute=True)(None)
    assert not (output_root / SHELL_BUILD_MANIFEST_NAME).exists()


def test_flash_snapshot_programs_what_was_verified(tmp_path: Path) -> None:
    """The snapshot is the manifest's verified bytes at a digest-named path,
    written atomically; a file replaced under the manifest after the manifest
    was written is refused rather than snapshotted."""
    from dau_build.shell_build import flash_snapshot, flash_snapshot_path, write_shell_build_manifest

    output_root = tmp_path / "shell"
    output_root.mkdir()
    (output_root / "dau_mm_job.bit").write_bytes(b"verified bytes")
    (output_root / "build_mm_job.tcl").write_text("# generated\n")
    manifest_path = write_shell_build_manifest(output_root, name="t", metadata={"build_status": "built", "wns_ns": 0.2})
    work_root = tmp_path / "work"

    source, snapshot = flash_snapshot(manifest_path, work_root=work_root, write=False)
    assert source == output_root / "dau_mm_job.bit"
    assert snapshot.parent == work_root / "flash" and not snapshot.exists()

    _, written = flash_snapshot(manifest_path, work_root=work_root, write=True)
    assert written == snapshot and snapshot.read_bytes() == b"verified bytes"
    digest = next(a for a in load_artifact_manifest(manifest_path).artifacts if a.role == "bitstream").digest
    assert snapshot == flash_snapshot_path(work_root, digest)

    (output_root / "dau_mm_job.bit").write_bytes(b"replaced after the manifest")
    with pytest.raises(Exception, match="digest"):
        flash_snapshot(manifest_path, work_root=work_root, write=True)
    assert snapshot.read_bytes() == b"verified bytes"  # the earlier snapshot is untouched


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

    # a timing miss is packaged too, as what it is, with the slack as a number
    kv.write_text("build_status=timing-failed\nwns_ns=-0.117\nbitstream=overlay.bit\nresource_summary=util.rpt\nvivado_log=vivado.log\n")
    missed = load_artifact_manifest(write_overlay_build_manifest(work, kv, name="dau-vivado"))
    assert (missed.metadata["build_status"], missed.metadata["wns_ns"]) == ("timing-failed", -0.117)
