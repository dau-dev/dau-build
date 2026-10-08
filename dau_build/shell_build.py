"""Shell project build execution and artifact packaging.

Runs a generated shell project script (the output of a shell artifact
writer such as ``mm_shell.write_mm_job_shell_artifacts``) through Vivado
and packages every build output — bitstream, reports, log, and the
generated/contributing sources — as one artlink manifest with content
digests and build metadata. The manifest is the provenance record: a
flashed bitstream must be identifiable from it alone, never from a
filename.
"""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from artlink import Artifact, Digest
from ccflow import BaseModel

from .packaging import ArtifactManifest

__all__ = (
    "SHELL_BUILD_MANIFEST_NAME",
    "ShellBuildError",
    "ShellBuildStatus",
    "parse_shell_build_console",
    "run_shell_project_build",
    "shell_build_manifest",
    "write_shell_build_manifest",
)

SHELL_BUILD_MANIFEST_NAME = "shell-build.artifacts.yaml"
_BUILD_OK_PATTERN = re.compile(r"^DAU_MM_JOB_BUILD_OK wns=(?P<wns>\S*)\s*$", re.MULTILINE)
_TIMING_FAILED_PATTERN = re.compile(r"^DAU_MM_JOB_BUILD_TIMING_FAILED wns=(?P<wns>\S*)\s*$", re.MULTILINE)
_BUILD_FAILED_PATTERN = re.compile(r"^DAU_MM_JOB_BUILD_FAILED (?P<stage>.+?)\s*$", re.MULTILINE)


class ShellBuildError(ValueError):
    pass


class ShellBuildStatus(BaseModel):
    """The outcome of a shell project build, parsed from the Vivado console.

    ``built`` means the bitstream exists and the routed worst slack is a
    number at or above zero. ``timing-failed`` means the bitstream exists
    but the slack is negative or could not be read: an image that must not
    be flashed, and must not be mistaken for one that can."""

    build_status: Literal["built", "timing-failed", "failed", "unknown"]
    wns_ns: float | None = None
    failed_stage: str | None = None
    return_code: int | None = None


def run_shell_project_build(
    output_root: Path,
    *,
    script: str = "build_mm_job.tcl",
    vivado_executable: str = "vivado",
    console_log: str = "console.log",
) -> ShellBuildStatus:
    """Execute the generated project script in batch mode from inside the
    output root (the scripts resolve their artifacts relative to
    themselves) and return the parsed build status, whatever it is; the
    caller decides what to record and what to refuse."""
    script_path = output_root / script
    if not script_path.is_file():
        raise ShellBuildError(f"shell project script does not exist: {script_path.as_posix()}")
    log_path = output_root / console_log
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            [vivado_executable, "-mode", "batch", "-source", script],
            cwd=output_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    status = parse_shell_build_console(log_path.read_text(encoding="utf-8"))
    status.return_code = completed.returncode
    if completed.returncode != 0 and status.build_status == "built":
        # the marker says built but vivado did not exit cleanly: not provable
        status.build_status = "unknown"
    return status


def describe_failure(status: ShellBuildStatus, log_path: Path) -> str:
    """One line saying why a build is not ``built``."""
    detail = f"stage {status.failed_stage}" if status.failed_stage else f"wns_ns={status.wns_ns}"
    return f"shell build {status.build_status} (exit {status.return_code}, {detail}): see {log_path.as_posix()}"


def parse_shell_build_console(console_text: str) -> ShellBuildStatus:
    """Extract the build outcome the generated scripts print: the
    DAU_MM_JOB_BUILD_OK / BUILD_TIMING_FAILED / BUILD_FAILED marker and the
    routed worst slack. An OK marker whose slack is negative or unreadable
    (scripts generated before the timing guard printed one) reads as
    ``timing-failed`` too: the marker is not the proof, the number is."""
    timing_failed = _TIMING_FAILED_PATTERN.search(console_text)
    if timing_failed:
        return ShellBuildStatus(build_status="timing-failed", wns_ns=_slack(timing_failed.group("wns")))
    ok = _BUILD_OK_PATTERN.search(console_text)
    if ok:
        wns = _slack(ok.group("wns"))
        if wns is None or wns < 0.0:
            return ShellBuildStatus(build_status="timing-failed", wns_ns=wns)
        return ShellBuildStatus(build_status="built", wns_ns=wns)
    failed = _BUILD_FAILED_PATTERN.search(console_text)
    if failed:
        return ShellBuildStatus(build_status="failed", failed_stage=failed.group("stage"))
    return ShellBuildStatus(build_status="unknown")


def flash_snapshot_path(work_root: Path, digest: Digest) -> Path:
    """Where a manifest's verified bitstream is snapshotted before programming:
    named by its digest, so the same image always lands at the same path and
    two flashes of one design cannot disagree about the bytes."""
    return work_root / "flash" / f"{digest.value[:16]}.bit"


def flash_snapshot(manifest_path: Path, *, work_root: Path, write: bool) -> tuple[Path, Path]:
    """Resolve the bitstream a built manifest names, verify its status and
    digest, and (when ``write``) copy the exact verified bytes to the
    digest-named snapshot atomically. Returns ``(source, snapshot)``. The
    programmer reads the snapshot, so a file replaced under the manifest
    after verification cannot reach the device: what was verified is what is
    programmed."""
    import os
    import tempfile

    from dau_build.build_steps import bitstream_from_shell_build_manifest
    from dau_build.packaging import load_artifact_manifest

    source = bitstream_from_shell_build_manifest(manifest_path)
    bitstream = next(artifact for artifact in load_artifact_manifest(manifest_path).artifacts if artifact.role == "bitstream")
    snapshot = flash_snapshot_path(work_root, bitstream.digest)
    if write:
        data = source.read_bytes()
        if hashlib.new(bitstream.digest.algorithm, data).hexdigest() != bitstream.digest.value:
            raise ShellBuildError(f"bitstream digest changed after manifest verification: {source}")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=snapshot.parent, prefix=f".{snapshot.name}.")
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, snapshot)
    return source, snapshot


def _slack(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _digest(path: Path) -> Digest:
    return Digest(algorithm="sha256", value=hashlib.sha256(path.read_bytes()).hexdigest())


def _git_describe(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "describe", "--always", "--dirty", "--tags"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout.strip() or None


def shell_build_manifest(
    output_root: Path,
    *,
    name: str,
    bitstream: str = "dau_mm_job.bit",
    reports: tuple[str, ...] = ("utilization_mm.rpt", "timing_mm.rpt"),
    console_log: str = "console.log",
    source_paths: tuple[Path, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> ArtifactManifest:
    """Package a completed shell build: the bitstream (digested), reports,
    console log, the generated project inputs found in the output root, and
    the contributing HDL sources (digested, with the git state of each
    containing repository recorded in the manifest metadata)."""
    bitstream_path = output_root / bitstream
    if not bitstream_path.is_file():
        raise ShellBuildError(f"bitstream does not exist: {bitstream_path.as_posix()}")

    artifacts: list[Artifact] = [
        Artifact(path=bitstream_path, kind="binary", role="bitstream", digest=_digest(bitstream_path)),
    ]
    for report in reports:
        report_path = output_root / report
        if report_path.is_file():
            artifacts.append(Artifact(path=report_path, kind="metadata", role="report"))
    log_path = output_root / console_log
    if log_path.is_file():
        artifacts.append(Artifact(path=log_path, kind="metadata", role="build-log"))
    for generated in sorted(output_root.iterdir()):
        if generated.suffix in (".tcl", ".xdc", ".prj", ".v", ".sv") and generated.is_file():
            artifacts.append(
                Artifact(
                    path=generated,
                    kind="source",
                    role="generated-project-input",
                    digest=_digest(generated),
                )
            )

    source_repos: dict[str, str] = {}
    for source in source_paths:
        source_path = Path(source)
        if not source_path.is_file():
            raise ShellBuildError(f"contributing source does not exist: {source_path.as_posix()}")
        artifacts.append(Artifact(path=source_path, kind="source", role="hdl-source", digest=_digest(source_path)))
        describe = _git_describe(source_path.parent)
        if describe:
            source_repos.setdefault(source_path.parent.as_posix(), describe)

    manifest_metadata: dict[str, Any] = {"source_repositories": source_repos}
    if metadata:
        manifest_metadata.update(metadata)
    return ArtifactManifest(name=name, intent="output", artifacts=tuple(artifacts), metadata=manifest_metadata)


def write_shell_build_manifest(
    output_root: Path,
    *,
    name: str,
    source_paths: tuple[Path, ...] = (),
    metadata: dict[str, Any] | None = None,
    bitstream: str = "dau_mm_job.bit",
) -> Path:
    """Build and write the shell-build manifest into the output root."""
    import yaml

    manifest = shell_build_manifest(
        output_root,
        name=name,
        bitstream=bitstream,
        source_paths=source_paths,
        metadata=metadata,
    )
    manifest_path = output_root / SHELL_BUILD_MANIFEST_NAME
    manifest_path.write_text(yaml.safe_dump(manifest.model_dump(mode="json", exclude_defaults=True), sort_keys=False), encoding="utf-8")
    return manifest_path


def write_overlay_build_manifest(work_root: Path, key_value_manifest_path: Path, *, name: str) -> Path | None:
    """Package a *built* overlay backend run as an artlink manifest beside
    its key=value handoff (the Tcl-side format stays as the in-band
    mechanism; provenance converges on artlink). Returns None when the
    key=value manifest is still ``planned`` — there is nothing to package
    yet."""
    from dau_build.vivado_backend import _parse_manifest_text

    items, errors = _parse_manifest_text(key_value_manifest_path.read_text(encoding="utf-8"))
    if errors:
        raise ShellBuildError(f"invalid key=value manifest {key_value_manifest_path.as_posix()}: {'; '.join(errors)}")
    manifest = dict(items)
    build_status = manifest.get("build_status")
    if build_status not in ("built", "timing-failed"):
        return None

    def resolve(key: str) -> Path | None:
        value = manifest.get(key)
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else work_root / path

    bitstream_path = resolve("bitstream")
    if bitstream_path is None or not bitstream_path.is_file():
        raise ShellBuildError(f"built manifest names no existing bitstream: {key_value_manifest_path.as_posix()}")

    artifacts: list[Artifact] = [Artifact(path=bitstream_path, kind="binary", role="bitstream", digest=_digest(bitstream_path))]
    for key, role in (
        ("resource_summary", "report"),
        ("timing_summary", "report"),
        ("vivado_log", "build-log"),
        ("overlay", "generated-project-input"),
    ):
        path = resolve(key)
        if path is not None and path.is_file():
            artifacts.append(Artifact(path=path, kind="metadata" if role != "generated-project-input" else "source", role=role))

    metadata: dict[str, Any] = {k: v for k, v in manifest.items() if k not in ("build_status", "wns_ns")}
    metadata["build_status"] = build_status
    if "wns_ns" in manifest:
        # recorded as a number, the way the shell flow records it, so a
        # consumer reads one representation
        metadata["wns_ns"] = _slack(manifest["wns_ns"])
    packaged = ArtifactManifest(name=name, intent="output", artifacts=tuple(artifacts), metadata=metadata)
    import yaml

    manifest_path = key_value_manifest_path.with_suffix(".artifacts.yaml")
    manifest_path.write_text(yaml.safe_dump(packaged.model_dump(mode="json", exclude_defaults=True), sort_keys=False), encoding="utf-8")
    return manifest_path
