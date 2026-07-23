"""Shell project build execution and artifact packaging.

Runs a generated shell project script (the output of a shell artifact
writer such as ``dpv1_shell.write_mm_job_shell_artifacts``) through Vivado
and packages every build output — bitstream, reports, log, and the
generated/contributing sources — as one artlink manifest with content
digests and build metadata. The manifest is the provenance record: a
flashed bitstream must be identifiable from it alone, never from a
filename.
"""

from __future__ import annotations

import hashlib
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
_BUILD_OK_PATTERN = re.compile(r"^DAU_MM_JOB_BUILD_OK wns=(?P<wns>[-0-9.]+)\s*$", re.MULTILINE)
_BUILD_FAILED_PATTERN = re.compile(r"^DAU_MM_JOB_BUILD_FAILED (?P<stage>.+?)\s*$", re.MULTILINE)


class ShellBuildError(ValueError):
    pass


class ShellBuildStatus(BaseModel):
    """The outcome of a shell project build, parsed from the Vivado console."""

    build_status: Literal["built", "failed", "unknown"]
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
    themselves) and return the parsed build status."""
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
    if completed.returncode != 0 or status.build_status != "built":
        raise ShellBuildError(
            f"shell build failed (exit {completed.returncode}, status {status.build_status}"
            + (f", stage {status.failed_stage}" if status.failed_stage else "")
            + f"): see {log_path.as_posix()}"
        )
    return status


def parse_shell_build_console(console_text: str) -> ShellBuildStatus:
    """Extract the build outcome the generated scripts print: the
    DAU_MM_JOB_BUILD_OK/FAILED marker and the routed worst negative slack."""
    ok = _BUILD_OK_PATTERN.search(console_text)
    if ok:
        return ShellBuildStatus(build_status="built", wns_ns=float(ok.group("wns")))
    failed = _BUILD_FAILED_PATTERN.search(console_text)
    if failed:
        return ShellBuildStatus(build_status="failed", failed_stage=failed.group("stage"))
    return ShellBuildStatus(build_status="unknown")


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
    if manifest.get("build_status") != "built":
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

    packaged = ArtifactManifest(
        name=name,
        intent="output",
        artifacts=tuple(artifacts),
        metadata={"build_status": "built", **{k: v for k, v in manifest.items() if k not in ("build_status",)}},
    )
    import yaml

    manifest_path = key_value_manifest_path.with_suffix(".artifacts.yaml")
    manifest_path.write_text(yaml.safe_dump(packaged.model_dump(mode="json", exclude_defaults=True), sort_keys=False), encoding="utf-8")
    return manifest_path
