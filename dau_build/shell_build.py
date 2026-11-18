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
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .packaging import ArtifactManifest

__all__ = (
    "SHELL_BUILD_MANIFEST_NAME",
    "ShellBuildError",
    "ShellBuildRecord",
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
    be flashed, and must not be mistaken for one that can. The validator
    holds that rule for every status built, parsed or read back, so no
    reader can accept a ``built`` that never proved it closed timing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    build_status: Literal["built", "timing-failed", "failed", "unknown"]
    wns_ns: float | None = None
    failed_stage: str | None = None
    return_code: int | None = None

    @model_validator(mode="after")
    def _built_closed_timing(self):
        if self.build_status == "built" and (self.wns_ns is None or not math.isfinite(self.wns_ns) or self.wns_ns < 0.0):
            raise ValueError(
                f"build_status=built needs a finite, non-negative wns_ns, got wns_ns={self.wns_ns}: "
                "a build that missed timing or recorded no slack is timing-failed"
            )
        return self


class ShellBuildRecord(ShellBuildStatus):
    """The metadata of a shell-build manifest, read through one model.

    The status fields and the timing rule come from ``ShellBuildStatus``;
    the identity fields are the keys the design cache, the advertisement
    check and the flash path read back. Every writer adds its own
    provenance keys (source repositories, priced resources, the backend's
    key=value items), so keys beyond these are kept as they are rather
    than refused: the typed keys are the contract, the rest is record."""

    model_config = ConfigDict(frozen=True, extra="allow")

    platform: str | None = None
    part: str | None = None
    spec_hash: str | None = None
    # canonical JSON texts, decoded by the packages that own their schemas
    composition_spec: str | None = None
    capability_contract: str | None = None
    job_clock_mhz: float | None = None

    @classmethod
    def from_metadata(cls, metadata: Any) -> ShellBuildRecord:
        """Validate a manifest's metadata mapping, raising ShellBuildError."""
        try:
            return cls.model_validate(metadata or {})
        except ValidationError as exc:
            raise ShellBuildError(f"invalid shell-build metadata: {exc}") from exc


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
    if completed.returncode != 0 and status.build_status == "built":
        # the marker says built but vivado did not exit cleanly: not provable
        return status.model_copy(update={"build_status": "unknown", "return_code": completed.returncode})
    return status.model_copy(update={"return_code": completed.returncode})


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


# the snapshot suffix of each programmable image role
_IMAGE_SUFFIX = {"bitstream": ".bit", "cfgmem": ".mcs"}


def flash_snapshot_path(work_root: Path, digest: Digest, *, role: str = "bitstream") -> Path:
    """Where a manifest's verified image is snapshotted before programming:
    named by its digest, so the same image always lands at the same path and
    two flashes of one design cannot disagree about the bytes."""
    return work_root / "flash" / f"{digest.value[:16]}{_IMAGE_SUFFIX[role]}"


def flash_snapshot(manifest_path: Path, *, work_root: Path, write: bool, role: str = "bitstream") -> tuple[Path, Path]:
    """Resolve the image of ``role`` a built manifest names (the bitstream,
    or the cfgmem image an SPI-boot board is flashed with), verify its status
    and digest, and (when ``write``) copy the exact verified bytes to the
    digest-named snapshot atomically. Returns ``(source, snapshot)``. The
    programmer reads the snapshot, so a file replaced under the manifest
    after verification cannot reach the device: what was verified is what is
    programmed."""
    import os
    import tempfile

    from dau_build.build_steps import bitstream_from_shell_build_manifest
    from dau_build.packaging import load_artifact_manifest

    source = bitstream_from_shell_build_manifest(manifest_path, role=role)
    image = next(artifact for artifact in load_artifact_manifest(manifest_path).artifacts if artifact.role == role)
    digest = image.digest
    # bitstream_from_shell_build_manifest above refuses an undigested image
    assert digest is not None, f"{role} in {manifest_path} carries no digest"
    snapshot = flash_snapshot_path(work_root, digest, role=role)
    if write:
        data = source.read_bytes()
        if hashlib.new(digest.algorithm, data).hexdigest() != digest.value:
            raise ShellBuildError(f"{role} digest changed after manifest verification: {source}")
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
    cfgmem: str = "dau_mm_job.mcs",
    reports: tuple[str, ...] = ("utilization_mm.rpt", "timing_mm.rpt"),
    console_log: str = "console.log",
    source_paths: tuple[Path, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> ArtifactManifest:
    """Package a completed shell build: the bitstream (digested), the SPI
    configuration-memory image when the board boots from flash (digested),
    reports, console log, the generated project inputs found in the output
    root, and the contributing HDL sources (digested, with the git state of
    each containing repository recorded in the manifest metadata)."""
    bitstream_path = output_root / bitstream
    if not bitstream_path.is_file():
        raise ShellBuildError(f"bitstream does not exist: {bitstream_path.as_posix()}")

    artifacts: list[Artifact] = [
        Artifact(path=bitstream_path, kind="binary", role="bitstream", digest=_digest(bitstream_path)),
    ]
    cfgmem_path = output_root / cfgmem
    if cfgmem_path.is_file():
        artifacts.append(Artifact(path=cfgmem_path, kind="binary", role="cfgmem", digest=_digest(cfgmem_path)))
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
    record = ShellBuildRecord.from_metadata(manifest_metadata)
    return ArtifactManifest(name=name, intent="output", artifacts=tuple(artifacts), metadata=record.model_dump(mode="json", exclude_none=True))


def write_shell_build_manifest(
    output_root: Path,
    *,
    name: str,
    source_paths: tuple[Path, ...] = (),
    metadata: dict[str, Any] | None = None,
    bitstream: str = "dau_mm_job.bit",
) -> Path:
    """Build and write the shell-build manifest into the output root."""

    manifest = shell_build_manifest(
        output_root,
        name=name,
        bitstream=bitstream,
        source_paths=source_paths,
        metadata=metadata,
    )
    manifest_path = output_root / SHELL_BUILD_MANIFEST_NAME
    manifest_path.write_text(manifest.to_yaml_text(), encoding="utf-8")
    return manifest_path


def write_overlay_build_manifest(work_root: Path, key_value_manifest_path: Path, *, name: str) -> Path | None:
    """Package a *built* overlay backend run as an artlink manifest beside
    its key=value handoff (the Tcl-side format stays as the in-band
    mechanism; provenance converges on artlink). Returns None when the
    key=value manifest is still ``planned`` — there is nothing to package
    yet."""
    from dau_build.key_value_manifest import KeyValueManifestError
    from dau_build.vivado_backend import VivadoBackendManifest

    try:
        manifest = VivadoBackendManifest.read(key_value_manifest_path)
    except KeyValueManifestError as exc:
        raise ShellBuildError(f"invalid key=value manifest: {exc}") from exc
    if manifest.build_status == "planned":
        return None

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else work_root / path

    bitstream_path = resolve(manifest.bitstream)
    if not bitstream_path.is_file():
        raise ShellBuildError(f"built manifest names no existing bitstream: {key_value_manifest_path.as_posix()}")

    artifacts: list[Artifact] = [Artifact(path=bitstream_path, kind="binary", role="bitstream", digest=_digest(bitstream_path))]
    for value, role in (
        (manifest.resource_summary, "report"),
        (manifest.timing_summary, "report"),
        (manifest.vivado_log, "build-log"),
        (manifest.overlay, "generated-project-input"),
    ):
        path = resolve(value)
        if path.is_file():
            artifacts.append(Artifact(path=path, kind="metadata" if role != "generated-project-input" else "source", role=role))

    # the slack is recorded as a number, the way the shell flow records it,
    # so a consumer reads one representation
    metadata: dict[str, Any] = {**dict(manifest.items()), "build_status": manifest.build_status, "wns_ns": manifest.wns_ns}
    record = ShellBuildRecord.from_metadata(metadata)
    packaged = ArtifactManifest(name=name, intent="output", artifacts=tuple(artifacts), metadata=record.model_dump(mode="json", exclude_none=True))
    manifest_path = key_value_manifest_path.with_suffix(".artifacts.yaml")
    manifest_path.write_text(packaged.to_yaml_text(), encoding="utf-8")
    return manifest_path
