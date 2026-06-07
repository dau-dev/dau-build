from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from dau_core.registers import DEFAULT_STREAM_JOB_REGISTER_CONTRACT

from dau_build.artifact_bundle import ArtifactBundle, ArtifactBundleError, load_artifact_bundle

SUPPORTED_VIVADO_INVOCATIONS = frozenset(("standard", "source-only"))


@dataclass(frozen=True)
class VivadoBackendRequest:
    dau_core_hdl_root: Path
    build_root: Path
    dau_artifact_bundle_path: Path | None = None
    artifact_stem: str = "dau-vivado"
    platform: str = "vivado-xdma"
    shell: str = "xdma-shell"
    operator_set: tuple[str, ...] = ("identity",)
    selected_module: str | None = None
    register_map_version: str = "0.1"
    stream_protocol_version: str = "0.1"
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    build_tcl: Path = Path("scripts/dau_build.tcl")
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit")
    resource_summary_path: Path = Path("reports/dau_utilization.rpt")
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt")
    vivado_log_path: Path = Path("vivado.log")
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    vivado_executable: str = "vivado"
    vivado_invocation: str = "standard"
    vivado_mount_root: Path | None = None

    def __post_init__(self) -> None:
        if self.vivado_invocation not in SUPPORTED_VIVADO_INVOCATIONS:
            raise ValueError(f"unsupported Vivado invocation: {self.vivado_invocation}")

    @property
    def resolved_manifest_path(self) -> Path:
        return self.manifest_path if self.manifest_path is not None else Path(f"{self.artifact_stem}.manifest")

    @property
    def resolved_command_plan_path(self) -> Path:
        return self.command_plan_path if self.command_plan_path is not None else Path(f"{self.artifact_stem}.plan")

    @property
    def resolved_dau_artifact_bundle_path(self) -> Path | None:
        if self.dau_artifact_bundle_path is None:
            return None
        return _build_artifact_path(self.build_root, self.dau_artifact_bundle_path).resolve()

    @property
    def resolved_vivado_mount_root(self) -> Path | None:
        if self.vivado_mount_root is None:
            return None
        return self.vivado_mount_root.resolve(strict=False)

    @property
    def uses_mounted_source_only_vivado(self) -> bool:
        return self.vivado_invocation == "source-only" and self.vivado_mount_root is not None


@dataclass(frozen=True)
class VivadoProjectGenerationRequest:
    source_shell_root: Path
    work_root: Path
    dau_core_root: Path
    dau_driver_root: Path
    dau_utils_root: Path | None = None
    dau_build_manifest_path: Path | None = None
    dau_top_sv_path: Path | None = None
    dau_artifact_bundle_path: Path | None = None
    artifact_stem: str = "dau-vivado"
    platform: str = "vivado-xdma"
    shell: str = "xdma-shell"
    operator_set: tuple[str, ...] = ("identity",)
    selected_module: str | None = None
    register_map_version: str = "0.1"
    stream_protocol_version: str = "0.1"
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    build_tcl: Path = Path("scripts/dau_build.tcl")
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    project_manifest_path: Path | None = None
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit")
    resource_summary_path: Path = Path("reports/dau_utilization.rpt")
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt")
    vivado_log_path: Path = Path("vivado.log")
    xdma_module_path: Path = Path("sw/xdma/xdma.ko")
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    vivado_executable: str = "vivado"
    vivado_invocation: str = "standard"
    vivado_mount_root: Path | None = None
    plan_executable: str = "dau-build"

    def __post_init__(self) -> None:
        if self.vivado_invocation not in SUPPORTED_VIVADO_INVOCATIONS:
            raise ValueError(f"unsupported Vivado invocation: {self.vivado_invocation}")

    @property
    def dau_core_hdl_root(self) -> Path:
        return self.dau_core_root / "dau_core" / "hdl"

    @property
    def resolved_project_manifest_path(self) -> Path:
        return self.project_manifest_path if self.project_manifest_path is not None else Path(f"{self.artifact_stem}.project")

    @property
    def backend_request(self) -> VivadoBackendRequest:
        return VivadoBackendRequest(
            dau_core_hdl_root=self.dau_core_hdl_root,
            build_root=self.work_root,
            artifact_stem=self.artifact_stem,
            platform=self.platform,
            shell=self.shell,
            operator_set=self.operator_set,
            selected_module=self.selected_module,
            register_map_version=self.register_map_version,
            stream_protocol_version=self.stream_protocol_version,
            dau_artifact_bundle_path=self.dau_artifact_bundle_path,
            overlay_tcl=self.overlay_tcl,
            build_tcl=self.build_tcl,
            manifest_path=self.manifest_path,
            command_plan_path=self.command_plan_path,
            bitstream_path=self.bitstream_path,
            resource_summary_path=self.resource_summary_path,
            timing_summary_path=self.timing_summary_path,
            vivado_log_path=self.vivado_log_path,
            vivado_settings=self.vivado_settings,
            vivado_executable=self.vivado_executable,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
        )


@dataclass(frozen=True)
class VivadoBackendArtifacts:
    overlay_tcl_path: Path
    manifest_path: Path
    command_plan_path: Path
    build_tcl_path: Path
    bitstream_path: Path
    resource_summary_path: Path
    timing_summary_path: Path
    vivado_log_path: Path
    overlay_driver_tcl_path: Path | None
    build_driver_tcl_path: Path | None
    overlay_tcl_text: str
    build_tcl_text: str
    overlay_driver_tcl_text: str | None
    build_driver_tcl_text: str | None
    manifest_text: str
    command_plan_text: str


@dataclass(frozen=True)
class VivadoProjectGenerationArtifacts:
    project_manifest_path: Path
    project_manifest_text: str
    backend_artifacts: VivadoBackendArtifacts


@dataclass(frozen=True)
class VivadoBackendArtifactValidation:
    manifest_path: Path
    command_plan_path: Path
    overlay_tcl_path: Path | None
    bitstream_path: Path | None
    resource_summary_path: Path | None
    timing_summary_path: Path | None
    vivado_log_path: Path | None
    build_status: str | None
    manifest_items: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class VivadoProjectArtifactValidation:
    project_manifest_path: Path
    project_manifest_items: tuple[tuple[str, str], ...]
    backend_validation: VivadoBackendArtifactValidation
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def manifest_path(self) -> Path:
        return self.backend_validation.manifest_path

    @property
    def command_plan_path(self) -> Path:
        return self.backend_validation.command_plan_path

    @property
    def overlay_tcl_path(self) -> Path | None:
        return self.backend_validation.overlay_tcl_path

    @property
    def bitstream_path(self) -> Path | None:
        return self.backend_validation.bitstream_path

    @property
    def resource_summary_path(self) -> Path | None:
        return self.backend_validation.resource_summary_path

    @property
    def timing_summary_path(self) -> Path | None:
        return self.backend_validation.timing_summary_path

    @property
    def vivado_log_path(self) -> Path | None:
        return self.backend_validation.vivado_log_path

    @property
    def build_status(self) -> str | None:
        return self.backend_validation.build_status


def generate_vivado_backend_artifacts(request: VivadoBackendRequest) -> VivadoBackendArtifacts:
    manifest_path = request.resolved_manifest_path
    command_plan_path = request.resolved_command_plan_path
    artifact_bundle = _load_request_artifact_bundle(request)
    vivado_path_base = _request_vivado_path_base(request)
    overlay_driver_tcl = source_only_vivado_driver_path(request.overlay_tcl) if request.uses_mounted_source_only_vivado else None
    build_driver_tcl = source_only_vivado_driver_path(request.build_tcl) if request.uses_mounted_source_only_vivado else None
    return VivadoBackendArtifacts(
        overlay_tcl_path=_build_artifact_path(request.build_root, request.overlay_tcl),
        manifest_path=_build_artifact_path(request.build_root, manifest_path),
        command_plan_path=_build_artifact_path(request.build_root, command_plan_path),
        build_tcl_path=_build_artifact_path(request.build_root, request.build_tcl),
        bitstream_path=_build_artifact_path(request.build_root, request.bitstream_path),
        resource_summary_path=_build_artifact_path(request.build_root, request.resource_summary_path),
        timing_summary_path=_build_artifact_path(request.build_root, request.timing_summary_path),
        vivado_log_path=_build_artifact_path(request.build_root, request.vivado_log_path),
        overlay_driver_tcl_path=None if overlay_driver_tcl is None else _build_artifact_path(request.build_root, overlay_driver_tcl),
        build_driver_tcl_path=None if build_driver_tcl is None else _build_artifact_path(request.build_root, build_driver_tcl),
        overlay_tcl_text=dau_overlay_tcl(
            request.dau_core_hdl_root,
            manifest_path=manifest_path,
            overlay_tcl=request.overlay_tcl,
            bitstream_path=request.bitstream_path,
            resource_summary_path=request.resource_summary_path,
            timing_summary_path=request.timing_summary_path,
            vivado_log_path=request.vivado_log_path,
            dau_artifact_bundle_path=request.resolved_dau_artifact_bundle_path,
            dau_generated_top=_bundle_generated_top_path(artifact_bundle),
            dau_bundle_hdl_sources=_bundle_hdl_source_paths(artifact_bundle),
            selected_module=request.selected_module,
            vivado_path_base=vivado_path_base,
        ),
        build_tcl_text=vivado_build_tcl(
            manifest_path=manifest_path,
            bitstream_path=request.bitstream_path,
            resource_summary_path=request.resource_summary_path,
            timing_summary_path=request.timing_summary_path,
            vivado_log_path=request.vivado_log_path,
        ),
        overlay_driver_tcl_text=None
        if overlay_driver_tcl is None
        else source_only_vivado_driver_tcl(
            work_root=request.build_root,
            vivado_mount_root=request.resolved_vivado_mount_root,
            tcl_path=request.overlay_tcl,
        ),
        build_driver_tcl_text=None
        if build_driver_tcl is None
        else source_only_vivado_driver_tcl(
            work_root=request.build_root,
            vivado_mount_root=request.resolved_vivado_mount_root,
            tcl_path=request.build_tcl,
        ),
        manifest_text=vivado_backend_manifest_text(request, artifact_bundle=artifact_bundle),
        command_plan_text=overlay_command_plan_text(
            work_root=request.build_root,
            overlay_tcl=request.overlay_tcl,
            build_tcl=request.build_tcl,
            vivado_settings=request.vivado_settings,
            vivado_executable=request.vivado_executable,
            vivado_invocation=request.vivado_invocation,
            vivado_mount_root=request.resolved_vivado_mount_root,
        ),
    )


def generate_vivado_project_generation_artifacts(request: VivadoProjectGenerationRequest) -> VivadoProjectGenerationArtifacts:
    return VivadoProjectGenerationArtifacts(
        project_manifest_path=_build_artifact_path(request.work_root, request.resolved_project_manifest_path),
        project_manifest_text=vivado_project_generation_manifest_text(request),
        backend_artifacts=generate_vivado_backend_artifacts(request.backend_request),
    )


def validate_vivado_backend_artifact_bundle(
    build_root: Path,
    *,
    manifest_path: Path = Path("dau-vivado.manifest"),
    command_plan_path: Path = Path("dau-vivado.plan"),
) -> VivadoBackendArtifactValidation:
    resolved_manifest_path = _build_artifact_path(build_root, manifest_path)
    resolved_command_plan_path = _build_artifact_path(build_root, command_plan_path)
    errors: list[str] = []
    manifest_items: tuple[tuple[str, str], ...] = ()
    overlay_tcl_path: Path | None = None
    bitstream_path: Path | None = None
    resource_summary_path: Path | None = None
    timing_summary_path: Path | None = None
    vivado_log_path: Path | None = None
    build_status: str | None = None

    if not resolved_manifest_path.is_file():
        errors.append(f"missing manifest: {resolved_manifest_path}")
    else:
        manifest_items, parse_errors = _parse_manifest_text(resolved_manifest_path.read_text(encoding="utf-8"))
        errors.extend(parse_errors)

    manifest = dict(manifest_items)
    if manifest:
        errors.extend(
            _validate_manifest_contract(build_root=build_root, manifest_path=manifest_path, command_plan_path=command_plan_path, manifest=manifest)
        )
        overlay_tcl_path = _build_artifact_path(build_root, Path(manifest["overlay"])) if "overlay" in manifest else None
        bitstream_path = _build_artifact_path(build_root, Path(manifest["bitstream"])) if "bitstream" in manifest else None
        resource_summary_path = _build_artifact_path(build_root, Path(manifest["resource_summary"])) if "resource_summary" in manifest else None
        timing_summary_path = _build_artifact_path(build_root, Path(manifest["timing_summary"])) if "timing_summary" in manifest else None
        vivado_log_path = _build_artifact_path(build_root, Path(manifest["vivado_log"])) if "vivado_log" in manifest else None
        build_status = manifest.get("build_status")

    if not resolved_command_plan_path.is_file():
        errors.append(f"missing command plan: {resolved_command_plan_path}")

    command_plan_text = resolved_command_plan_path.read_text(encoding="utf-8") if resolved_command_plan_path.is_file() else ""
    overlay_tcl_text = ""
    if overlay_tcl_path is not None:
        if overlay_tcl_path.is_file():
            overlay_tcl_text = overlay_tcl_path.read_text(encoding="utf-8")
        else:
            errors.append(f"missing overlay Tcl: {overlay_tcl_path}")

    if manifest and command_plan_text:
        errors.extend(_validate_command_plan_contract(build_root=build_root, manifest=manifest, command_plan_text=command_plan_text))
    if manifest and overlay_tcl_text:
        errors.extend(_validate_overlay_tcl_contract(manifest=manifest, overlay_tcl_text=overlay_tcl_text))

    return VivadoBackendArtifactValidation(
        manifest_path=resolved_manifest_path,
        command_plan_path=resolved_command_plan_path,
        overlay_tcl_path=overlay_tcl_path,
        bitstream_path=bitstream_path,
        resource_summary_path=resource_summary_path,
        timing_summary_path=timing_summary_path,
        vivado_log_path=vivado_log_path,
        build_status=build_status,
        manifest_items=manifest_items,
        errors=tuple(errors),
    )


def validate_vivado_project_artifact_bundle(
    build_root: Path,
    *,
    project_manifest_path: Path = Path("dau-vivado.project"),
    manifest_path: Path = Path("dau-vivado.manifest"),
    command_plan_path: Path = Path("dau-vivado.plan"),
) -> VivadoProjectArtifactValidation:
    backend_validation = validate_vivado_backend_artifact_bundle(
        build_root,
        manifest_path=manifest_path,
        command_plan_path=command_plan_path,
    )
    resolved_project_manifest_path = _build_artifact_path(build_root, project_manifest_path)
    errors = list(backend_validation.errors)
    project_manifest_items: tuple[tuple[str, str], ...] = ()

    if not resolved_project_manifest_path.is_file():
        errors.append(f"missing project manifest: {resolved_project_manifest_path}")
    else:
        project_manifest_items, parse_errors = _parse_manifest_text(resolved_project_manifest_path.read_text(encoding="utf-8"))
        errors.extend(parse_errors)

    project_manifest = dict(project_manifest_items)
    backend_manifest = dict(backend_validation.manifest_items)
    if project_manifest:
        errors.extend(
            _validate_project_manifest_contract(
                build_root=build_root,
                project_manifest_path=project_manifest_path,
                manifest_path=manifest_path,
                command_plan_path=command_plan_path,
                project_manifest=project_manifest,
                backend_manifest=backend_manifest,
            )
        )

    return VivadoProjectArtifactValidation(
        project_manifest_path=resolved_project_manifest_path,
        project_manifest_items=project_manifest_items,
        backend_validation=backend_validation,
        errors=tuple(errors),
    )


def vivado_backend_manifest(request: VivadoBackendRequest, *, artifact_bundle: ArtifactBundle | None = None) -> tuple[tuple[str, str], ...]:
    vivado_path_base = _request_vivado_path_base(request)
    return (
        *dau_overlay_manifest(
            request.dau_core_hdl_root,
            overlay_tcl=request.overlay_tcl,
            bitstream_path=request.bitstream_path,
            dau_artifact_bundle_path=request.resolved_dau_artifact_bundle_path,
            artifact_bundle=artifact_bundle,
            vivado_path_base=vivado_path_base,
        ),
        ("platform", request.platform),
        ("shell", request.shell),
        ("artifact_stem", request.artifact_stem),
        ("build_root", request.build_root.as_posix()),
        ("manifest", request.resolved_manifest_path.as_posix()),
        ("command_plan", request.resolved_command_plan_path.as_posix()),
        ("build_tcl", request.build_tcl.as_posix()),
        ("resource_summary", request.resource_summary_path.as_posix()),
        ("timing_summary", request.timing_summary_path.as_posix()),
        ("vivado_log", request.vivado_log_path.as_posix()),
        ("build_status", "planned"),
        ("selected_module", "" if request.selected_module is None else request.selected_module),
        ("register_map_version", request.register_map_version),
        ("stream_protocol_version", request.stream_protocol_version),
        ("operator_set", ",".join(request.operator_set)),
        ("vivado_settings", request.vivado_settings.as_posix()),
        ("vivado_executable", request.vivado_executable),
        ("vivado_invocation", request.vivado_invocation),
        ("vivado_mount_root", "" if request.resolved_vivado_mount_root is None else request.resolved_vivado_mount_root.as_posix()),
    )


def vivado_backend_manifest_text(request: VivadoBackendRequest, *, artifact_bundle: ArtifactBundle | None = None) -> str:
    lines = (f"{key}={value}" for key, value in vivado_backend_manifest(request, artifact_bundle=artifact_bundle))
    return "\n".join(lines) + "\n"


def vivado_project_generation_manifest(request: VivadoProjectGenerationRequest) -> tuple[tuple[str, str], ...]:
    backend_request = request.backend_request
    items = [
        ("project_generator", "dau_build.vivado_backend.vivado_project"),
        ("platform", request.platform),
        ("shell", request.shell),
        ("artifact_stem", request.artifact_stem),
        ("source_shell_root", request.source_shell_root.as_posix()),
        ("work_root", request.work_root.as_posix()),
        ("dau_core_root", request.dau_core_root.as_posix()),
        ("dau_core_hdl_root", request.dau_core_hdl_root.as_posix()),
        ("dau_driver_root", request.dau_driver_root.as_posix()),
        ("dau_utils_root", "" if request.dau_utils_root is None else request.dau_utils_root.as_posix()),
        ("dau_build_manifest", "" if request.dau_build_manifest_path is None else request.dau_build_manifest_path.as_posix()),
        ("dau_top_sv", "" if request.dau_top_sv_path is None else request.dau_top_sv_path.as_posix()),
        ("dau_artifact_bundle", "" if request.dau_artifact_bundle_path is None else request.dau_artifact_bundle_path.as_posix()),
        ("project_manifest", request.resolved_project_manifest_path.as_posix()),
        ("backend_manifest", backend_request.resolved_manifest_path.as_posix()),
        ("backend_command_plan", backend_request.resolved_command_plan_path.as_posix()),
        ("overlay_tcl", request.overlay_tcl.as_posix()),
        ("build_tcl", request.build_tcl.as_posix()),
        ("bitstream", request.bitstream_path.as_posix()),
        ("resource_summary", request.resource_summary_path.as_posix()),
        ("timing_summary", request.timing_summary_path.as_posix()),
        ("vivado_log", request.vivado_log_path.as_posix()),
        ("build_status", "planned"),
        ("xdma_module", request.xdma_module_path.as_posix()),
        ("register_map_version", request.register_map_version),
        ("stream_protocol_version", request.stream_protocol_version),
        ("operator_set", ",".join(request.operator_set)),
        ("vivado_settings", request.vivado_settings.as_posix()),
        ("vivado_executable", request.vivado_executable),
        ("vivado_invocation", request.vivado_invocation),
        ("vivado_mount_root", "" if request.vivado_mount_root is None else request.vivado_mount_root.resolve(strict=False).as_posix()),
        ("stage_command", vivado_project_stage_command(request)),
        ("build_command", vivado_project_build_command(request)),
        ("validate_command", vivado_project_validate_command(request)),
    ]
    return tuple(items)


def vivado_project_generation_manifest_text(request: VivadoProjectGenerationRequest) -> str:
    lines = (f"{key}={value}" for key, value in vivado_project_generation_manifest(request))
    return "\n".join(lines) + "\n"


def vivado_project_stage_command(request: VivadoProjectGenerationRequest) -> str:
    overrides = [
        ("source_shell_root", request.source_shell_root),
        ("work_root", request.work_root),
        ("dau_core_root", request.dau_core_root),
        ("artifact_stem", request.artifact_stem),
        ("backend_platform", request.platform),
        ("backend_shell", request.shell),
        ("register_map_version", request.register_map_version),
        ("stream_protocol_version", request.stream_protocol_version),
        ("overlay_tcl", request.overlay_tcl),
        ("manifest_path", request.backend_request.resolved_manifest_path),
        ("command_plan_path", request.backend_request.resolved_command_plan_path),
        ("vivado_settings", request.vivado_settings),
        ("vivado", request.vivado_executable),
    ]
    if request.vivado_invocation != "standard":
        overrides.append(("vivado_invocation", request.vivado_invocation))
    if request.vivado_mount_root is not None:
        overrides.append(("vivado_mount_root", request.vivado_mount_root.resolve(strict=False)))
    if request.dau_artifact_bundle_path is not None:
        overrides.append(("dau_artifact_bundle", request.dau_artifact_bundle_path))
    overrides.append(("operator", ",".join(request.operator_set)))
    return _task_command(request.plan_executable, "stage-vivado-overlay", tuple(overrides))


def vivado_project_build_command(request: VivadoProjectGenerationRequest) -> str:
    overrides = [
        ("work_root", request.work_root),
        ("overlay_tcl", request.overlay_tcl),
        ("manifest_path", request.backend_request.resolved_manifest_path),
        ("command_plan_path", request.backend_request.resolved_command_plan_path),
        ("project_manifest_path", request.resolved_project_manifest_path),
        ("vivado_settings", request.vivado_settings),
        ("vivado", request.vivado_executable),
    ]
    if request.vivado_invocation != "standard":
        overrides.append(("vivado_invocation", request.vivado_invocation))
    if request.vivado_mount_root is not None:
        overrides.append(("vivado_mount_root", request.vivado_mount_root.resolve(strict=False)))
    return _task_command(request.plan_executable, "build-vivado-artifacts", tuple(overrides))


def vivado_project_validate_command(request: VivadoProjectGenerationRequest) -> str:
    overrides = [
        ("work_root", request.work_root),
        ("manifest_path", request.backend_request.resolved_manifest_path),
        ("command_plan_path", request.backend_request.resolved_command_plan_path),
        ("project_manifest_path", request.resolved_project_manifest_path),
    ]
    return _task_command(request.plan_executable, "validate-vivado-artifacts", tuple(overrides))


def _task_command(executable: str, task: str, overrides: tuple[tuple[str, object], ...]) -> str:
    argv = [executable, f"task={task}"]
    argv.extend(f"{key}={value}" for key, value in overrides)
    return shlex.join(argv)


def _parse_manifest_text(text: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    items: list[tuple[str, str]] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"manifest line {line_number} is missing '='")
            continue
        key, value = line.split("=", 1)
        if not key:
            errors.append(f"manifest line {line_number} has an empty key")
            continue
        items.append((key, value))
    return tuple(items), tuple(errors)


def _validate_manifest_contract(*, build_root: Path, manifest_path: Path, command_plan_path: Path, manifest: dict[str, str]) -> tuple[str, ...]:
    errors: list[str] = []
    required_keys = (
        "backend",
        "platform",
        "shell",
        "artifact_stem",
        "build_root",
        "manifest",
        "command_plan",
        "build_tcl",
        "overlay",
        "bitstream",
        "resource_summary",
        "timing_summary",
        "vivado_log",
        "build_status",
        "register_map_version",
        "stream_protocol_version",
        "operator_set",
        "vivado_settings",
        "vivado_executable",
        "vivado_invocation",
        "vivado_mount_root",
    )
    optional_empty_keys = {"vivado_mount_root"}
    for key in required_keys:
        if key not in manifest:
            errors.append(f"manifest missing required key: {key}")
        elif key not in optional_empty_keys and not manifest[key]:
            errors.append(f"manifest missing required key: {key}")
    if manifest.get("backend") and manifest["backend"] != "dau_build.vivado_backend.vivado_overlay":
        errors.append(f"unexpected backend: {manifest['backend']}")
    if manifest.get("build_root") and manifest["build_root"] != build_root.as_posix():
        errors.append(f"manifest build_root mismatch: {manifest['build_root']} != {build_root.as_posix()}")
    if manifest.get("manifest") and manifest["manifest"] != manifest_path.as_posix():
        errors.append(f"manifest path mismatch: {manifest['manifest']} != {manifest_path.as_posix()}")
    if manifest.get("command_plan") and manifest["command_plan"] != command_plan_path.as_posix():
        errors.append(f"command plan path mismatch: {manifest['command_plan']} != {command_plan_path.as_posix()}")
    build_status = manifest.get("build_status")
    if build_status and build_status not in {"planned", "built"}:
        errors.append(f"unsupported build status: {build_status}")
    if build_status == "built":
        errors.extend(_validate_built_output_paths(build_root=build_root, manifest=manifest))
    if manifest.get("dau_artifact_bundle"):
        errors.extend(_validate_dau_artifact_bundle_reference(build_root=build_root, manifest=manifest))
    return tuple(errors)


def _validate_built_output_paths(*, build_root: Path, manifest: dict[str, str]) -> tuple[str, ...]:
    errors: list[str] = []
    for key in ("bitstream", "resource_summary", "timing_summary", "vivado_log"):
        value = manifest.get(key, "")
        if not value:
            continue
        path = _build_artifact_path(build_root, Path(value))
        if not path.is_file():
            errors.append(f"build_status=built but missing {key}: {path}")
    return tuple(errors)


def _validate_dau_artifact_bundle_reference(*, build_root: Path, manifest: dict[str, str]) -> tuple[str, ...]:
    bundle_path = _build_artifact_path(build_root, Path(manifest["dau_artifact_bundle"]))
    try:
        artifact_bundle = load_artifact_bundle((bundle_path,), required_roles=("generated-top",), validate_paths=True)
    except ArtifactBundleError as exc:
        return (f"invalid DAU artifact bundle: {exc}",)
    expected_sources = tuple(_split_manifest_list(manifest.get("dau_bundle_hdl_sources", "")))
    actual_sources = tuple(path.as_posix() for path in _bundle_hdl_source_paths(artifact_bundle))
    resolved_expected_sources = tuple(_build_artifact_path(build_root, Path(path)).resolve(strict=False).as_posix() for path in expected_sources)
    if expected_sources and resolved_expected_sources != actual_sources:
        return (f"DAU artifact bundle source mismatch: {','.join(expected_sources)} != {','.join(actual_sources)}",)
    generated_top = _bundle_generated_top_path(artifact_bundle)
    if generated_top is not None and manifest.get("dau_generated_top"):
        resolved_expected_top = _build_artifact_path(build_root, Path(manifest["dau_generated_top"])).resolve(strict=False)
        if resolved_expected_top != generated_top:
            return (f"DAU generated top mismatch: {manifest['dau_generated_top']} != {generated_top.as_posix()}",)
    return ()


def _validate_project_manifest_contract(
    *,
    build_root: Path,
    project_manifest_path: Path,
    manifest_path: Path,
    command_plan_path: Path,
    project_manifest: dict[str, str],
    backend_manifest: dict[str, str],
) -> tuple[str, ...]:
    errors: list[str] = []
    required_keys = (
        "project_generator",
        "platform",
        "shell",
        "artifact_stem",
        "source_shell_root",
        "work_root",
        "dau_core_root",
        "dau_core_hdl_root",
        "dau_driver_root",
        "dau_utils_root",
        "dau_build_manifest",
        "dau_top_sv",
        "dau_artifact_bundle",
        "project_manifest",
        "backend_manifest",
        "backend_command_plan",
        "overlay_tcl",
        "build_tcl",
        "bitstream",
        "resource_summary",
        "timing_summary",
        "vivado_log",
        "build_status",
        "xdma_module",
        "register_map_version",
        "stream_protocol_version",
        "operator_set",
        "vivado_settings",
        "vivado_executable",
        "vivado_invocation",
        "vivado_mount_root",
        "stage_command",
        "build_command",
        "validate_command",
    )
    optional_empty_keys = {"dau_utils_root", "dau_build_manifest", "dau_top_sv", "dau_artifact_bundle", "vivado_mount_root"}
    for key in required_keys:
        if key not in project_manifest:
            errors.append(f"project manifest missing required key: {key}")
        elif key not in optional_empty_keys and not project_manifest[key]:
            errors.append(f"project manifest has empty required key: {key}")

    if project_manifest.get("project_generator") and project_manifest["project_generator"] != "dau_build.vivado_backend.vivado_project":
        errors.append(f"unexpected project generator: {project_manifest['project_generator']}")
    if project_manifest.get("work_root") and project_manifest["work_root"] != build_root.as_posix():
        errors.append(f"project work root mismatch: {project_manifest['work_root']} != {build_root.as_posix()}")
    if project_manifest.get("project_manifest") and project_manifest["project_manifest"] != project_manifest_path.as_posix():
        errors.append(f"project manifest path mismatch: {project_manifest['project_manifest']} != {project_manifest_path.as_posix()}")
    if project_manifest.get("backend_manifest") and project_manifest["backend_manifest"] != manifest_path.as_posix():
        errors.append(f"project backend manifest mismatch: {project_manifest['backend_manifest']} != {manifest_path.as_posix()}")
    if project_manifest.get("backend_command_plan") and project_manifest["backend_command_plan"] != command_plan_path.as_posix():
        errors.append(f"project backend command plan mismatch: {project_manifest['backend_command_plan']} != {command_plan_path.as_posix()}")
    if project_manifest.get("dau_core_root") and project_manifest.get("dau_core_hdl_root"):
        expected_hdl_root = (Path(project_manifest["dau_core_root"]) / "dau_core" / "hdl").as_posix()
        if project_manifest["dau_core_hdl_root"] != expected_hdl_root:
            errors.append(f"project DAU HDL root mismatch: {project_manifest['dau_core_hdl_root']} != {expected_hdl_root}")

    errors.extend(
        _validate_project_backend_manifest_cross_refs(build_root=build_root, project_manifest=project_manifest, backend_manifest=backend_manifest)
    )
    errors.extend(
        _validate_project_manifest_commands(project_manifest=project_manifest, manifest_path=manifest_path, command_plan_path=command_plan_path)
    )
    return tuple(errors)


def _validate_project_backend_manifest_cross_refs(
    *, build_root: Path, project_manifest: dict[str, str], backend_manifest: dict[str, str]
) -> tuple[str, ...]:
    errors: list[str] = []
    key_pairs = (
        ("platform", "platform"),
        ("shell", "shell"),
        ("artifact_stem", "artifact_stem"),
        ("overlay_tcl", "overlay"),
        ("build_tcl", "build_tcl"),
        ("bitstream", "bitstream"),
        ("resource_summary", "resource_summary"),
        ("timing_summary", "timing_summary"),
        ("vivado_log", "vivado_log"),
        ("register_map_version", "register_map_version"),
        ("stream_protocol_version", "stream_protocol_version"),
        ("operator_set", "operator_set"),
        ("dau_artifact_bundle", "dau_artifact_bundle"),
        ("vivado_settings", "vivado_settings"),
        ("vivado_executable", "vivado_executable"),
        ("vivado_invocation", "vivado_invocation"),
        ("vivado_mount_root", "vivado_mount_root"),
    )
    for project_key, backend_key in key_pairs:
        project_value = project_manifest.get(project_key)
        backend_value = backend_manifest.get(backend_key)
        if project_key == "dau_artifact_bundle" and project_value and backend_value:
            project_path = _build_artifact_path(build_root, Path(project_value)).resolve(strict=False)
            backend_path = _build_artifact_path(build_root, Path(backend_value)).resolve(strict=False)
            if project_path != backend_path:
                errors.append(f"project {project_key} mismatch: {project_value} != backend {backend_key} {backend_value}")
            continue
        if project_value and backend_value and project_value != backend_value:
            errors.append(f"project {project_key} mismatch: {project_value} != backend {backend_key} {backend_value}")
    return tuple(errors)


def _validate_project_manifest_commands(
    *,
    project_manifest: dict[str, str],
    manifest_path: Path,
    command_plan_path: Path,
) -> tuple[str, ...]:
    errors: list[str] = []
    source_shell_root = project_manifest.get("source_shell_root", "")
    work_root = project_manifest.get("work_root", "")
    dau_core_root = project_manifest.get("dau_core_root", "")
    overlay_tcl = project_manifest.get("overlay_tcl", "")
    dau_artifact_bundle = project_manifest.get("dau_artifact_bundle", "")
    vivado_settings = project_manifest.get("vivado_settings", "")
    vivado_executable = project_manifest.get("vivado_executable", "")
    vivado_invocation = project_manifest.get("vivado_invocation", "standard")
    vivado_mount_root = project_manifest.get("vivado_mount_root", "")
    project_manifest_path = project_manifest.get("project_manifest", "")

    stage_required_options = [
        ("--source-shell-root", source_shell_root),
        ("--work-root", work_root),
        ("--dau-core-root", dau_core_root),
        ("--artifact-stem", project_manifest.get("artifact_stem", "")),
        ("--backend-platform", project_manifest.get("platform", "")),
        ("--backend-shell", project_manifest.get("shell", "")),
        ("--register-map-version", project_manifest.get("register_map_version", "")),
        ("--stream-protocol-version", project_manifest.get("stream_protocol_version", "")),
        ("--overlay-tcl", overlay_tcl),
        ("--manifest-path", manifest_path.as_posix()),
        ("--command-plan-path", command_plan_path.as_posix()),
        ("--vivado-settings", vivado_settings),
        ("--vivado", vivado_executable),
    ]
    if dau_artifact_bundle:
        stage_required_options.append(("--dau-artifact-bundle", dau_artifact_bundle))
    if vivado_invocation != "standard":
        stage_required_options.append(("--vivado-invocation", vivado_invocation))
    if vivado_mount_root:
        stage_required_options.append(("--vivado-mount-root", vivado_mount_root))
    errors.extend(
        _validate_project_command(
            label="stage_command",
            command=project_manifest.get("stage_command", ""),
            expected_plan="stage-vivado-overlay",
            required_options=tuple(stage_required_options),
        )
    )
    build_required_options = [
        ("--work-root", work_root),
        ("--overlay-tcl", overlay_tcl),
        ("--manifest-path", manifest_path.as_posix()),
        ("--command-plan-path", command_plan_path.as_posix()),
        ("--project-manifest-path", project_manifest_path),
        ("--vivado-settings", vivado_settings),
        ("--vivado", vivado_executable),
    ]
    validate_required_options = [
        ("--work-root", work_root),
        ("--manifest-path", manifest_path.as_posix()),
        ("--command-plan-path", command_plan_path.as_posix()),
        ("--project-manifest-path", project_manifest_path),
    ]
    if vivado_invocation != "standard":
        build_required_options.append(("--vivado-invocation", vivado_invocation))
    if vivado_mount_root:
        build_required_options.append(("--vivado-mount-root", vivado_mount_root))
    errors.extend(
        _validate_project_command(
            label="build_command",
            command=project_manifest.get("build_command", ""),
            expected_plan="build-vivado-artifacts",
            required_options=tuple(build_required_options),
        )
    )
    errors.extend(
        _validate_project_command(
            label="validate_command",
            command=project_manifest.get("validate_command", ""),
            expected_plan="validate-vivado-artifacts",
            required_options=tuple(validate_required_options),
        )
    )
    return tuple(errors)


def _validate_project_command(
    *,
    label: str,
    command: str,
    expected_plan: str,
    required_options: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    if not command:
        return ()
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return (f"project {label} cannot be parsed: {exc}",)

    errors: list[str] = []
    hydra_overrides = _command_hydra_overrides(tokens)
    if hydra_overrides:
        actual_plan = hydra_overrides.get("plan", "") if hydra_overrides.get("task") == "hardware-plan" else hydra_overrides.get("task", "")
    else:
        actual_plan = tokens[1] if len(tokens) > 1 else ""
    if actual_plan != expected_plan:
        errors.append(f"project {label} plan mismatch: {actual_plan} != {expected_plan}")
    for option, expected_value in required_options:
        actual_value = hydra_overrides.get(_hydra_option_key(option)) if hydra_overrides else _command_option_value(tokens, option)
        if actual_value is None:
            errors.append(f"project {label} missing option: {option}")
        elif expected_value and actual_value != expected_value:
            errors.append(f"project {label} option {option} mismatch: {actual_value} != {expected_value}")
    return tuple(errors)


def _command_hydra_overrides(tokens: list[str]) -> dict[str, str]:
    if len(tokens) < 2:
        return {}
    overrides = dict(token.split("=", 1) for token in tokens[1:] if "=" in token)
    if "task" not in overrides:
        return {}
    return overrides


def _hydra_option_key(option: str) -> str:
    return option.removeprefix("--").replace("-", "_")


def _command_option_value(tokens: list[str], option: str) -> str | None:
    try:
        option_index = tokens.index(option)
    except ValueError:
        return None
    value_index = option_index + 1
    if value_index >= len(tokens):
        return ""
    return tokens[value_index]


def _validate_command_plan_contract(*, build_root: Path, manifest: dict[str, str], command_plan_text: str) -> tuple[str, ...]:
    errors: list[str] = []
    overlay_tcl = manifest.get("overlay")
    build_tcl = manifest.get("build_tcl")
    vivado_invocation = manifest.get("vivado_invocation", "standard")
    vivado_mount_root = Path(manifest["vivado_mount_root"]) if manifest.get("vivado_mount_root") else None
    command_root = vivado_mount_root or build_root
    if f"cd {shlex.quote(str(command_root))}" not in command_plan_text:
        errors.append(f"command plan does not cd into Vivado command root: {command_root}")
    overlay_tcl_for_command = (
        _command_plan_tcl_path(build_root=build_root, tcl_path=Path(overlay_tcl), vivado_mount_root=vivado_mount_root) if overlay_tcl else None
    )
    build_tcl_for_command = (
        _command_plan_tcl_path(build_root=build_root, tcl_path=Path(build_tcl), vivado_mount_root=vivado_mount_root) if build_tcl else None
    )
    if overlay_tcl and not _command_plan_sources_tcl(
        command_plan_text=command_plan_text,
        vivado_executable=manifest.get("vivado_executable", "vivado"),
        tcl_path=overlay_tcl_for_command or Path(overlay_tcl),
        vivado_invocation=vivado_invocation,
    ):
        errors.append(f"command plan does not source overlay Tcl: {overlay_tcl_for_command or overlay_tcl}")
    if build_tcl and not _command_plan_sources_tcl(
        command_plan_text=command_plan_text,
        vivado_executable=manifest.get("vivado_executable", "vivado"),
        tcl_path=build_tcl_for_command or Path(build_tcl),
        vivado_invocation=vivado_invocation,
    ):
        errors.append(f"command plan does not source build Tcl: {build_tcl_for_command or build_tcl}")
    return tuple(errors)


def _command_plan_sources_tcl(*, command_plan_text: str, vivado_executable: str, tcl_path: Path, vivado_invocation: str) -> bool:
    if vivado_invocation == "source-only":
        return shlex.join((vivado_executable, str(tcl_path))) in command_plan_text
    return f"-source {shlex.quote(str(tcl_path))}" in command_plan_text


def _validate_overlay_tcl_contract(*, manifest: dict[str, str], overlay_tcl_text: str) -> tuple[str, ...]:
    errors: list[str] = []
    for key in ("overlay", "bitstream", "resource_summary", "timing_summary", "vivado_log"):
        value = manifest.get(key)
        if value is None:
            continue
        expected = f'"{key}={value}"'
        if expected not in overlay_tcl_text:
            errors.append(f"overlay Tcl does not write manifest field: {key}={value}")
    for source in _split_manifest_list(manifest.get("dau_bundle_hdl_sources", "")):
        if source not in overlay_tcl_text:
            errors.append(f"overlay Tcl does not consume DAU bundle source: {source}")
    return tuple(errors)


def _build_artifact_path(build_root: Path, artifact_path: Path) -> Path:
    if artifact_path.is_absolute():
        return artifact_path
    return build_root / artifact_path


def _request_vivado_path_base(request: VivadoBackendRequest) -> Path | None:
    if not request.uses_mounted_source_only_vivado:
        return None
    return request.build_root.resolve(strict=False)


def _render_vivado_path(path: Path, *, vivado_path_base: Path | None) -> Path:
    if vivado_path_base is None:
        return path
    return Path(os.path.relpath(path.resolve(strict=False), start=vivado_path_base.resolve(strict=False)))


def _path_relative_to(path: Path, root: Path) -> Path:
    return Path(os.path.relpath(path.resolve(strict=False), start=root.resolve(strict=False)))


def source_only_vivado_driver_path(tcl_path: Path) -> Path:
    return tcl_path.with_name(f"{tcl_path.stem}.driver{tcl_path.suffix}")


def source_only_vivado_driver_tcl(*, work_root: Path, vivado_mount_root: Path | None, tcl_path: Path) -> str:
    if vivado_mount_root is None:
        raise ValueError("source-only Vivado driver requires a mount root")
    workdir = _path_relative_to(work_root, vivado_mount_root)
    return f"cd {workdir.as_posix()}\nsource {tcl_path.as_posix()}\n"


def _command_plan_tcl_path(*, build_root: Path, tcl_path: Path, vivado_mount_root: Path | None) -> Path:
    if vivado_mount_root is None:
        return tcl_path
    driver_path = source_only_vivado_driver_path(tcl_path)
    return _path_relative_to(_build_artifact_path(build_root, driver_path), vivado_mount_root)


def _load_request_artifact_bundle(request: VivadoBackendRequest) -> ArtifactBundle | None:
    bundle_path = request.resolved_dau_artifact_bundle_path
    if bundle_path is None:
        return None
    try:
        return load_artifact_bundle((bundle_path,), required_roles=("generated-top",), require_hdl_sources=True, validate_paths=True)
    except ArtifactBundleError as exc:
        raise ValueError(f"invalid DAU artifact bundle {bundle_path.as_posix()}: {exc}") from exc


def _bundle_hdl_source_paths(artifact_bundle: ArtifactBundle | None) -> tuple[Path, ...]:
    if artifact_bundle is None:
        return ()
    return tuple(entry.artifact.path for entry in artifact_bundle.hdl_source_entries() if entry.artifact.path is not None)


def _bundle_generated_top_path(artifact_bundle: ArtifactBundle | None) -> Path | None:
    if artifact_bundle is None:
        return None
    generated_top_entries = artifact_bundle.entries_for_role("generated-top")
    if not generated_top_entries:
        return None
    return generated_top_entries[0].artifact.path


def _dau_artifact_bundle_manifest(
    *,
    dau_artifact_bundle_path: Path | None,
    artifact_bundle: ArtifactBundle | None,
    vivado_path_base: Path | None = None,
) -> tuple[tuple[str, str], ...]:
    if dau_artifact_bundle_path is None and artifact_bundle is None:
        return ()
    hdl_sources = _bundle_hdl_source_paths(artifact_bundle)
    generated_top = _bundle_generated_top_path(artifact_bundle)
    rendered_bundle_path = (
        None if dau_artifact_bundle_path is None else _render_vivado_path(dau_artifact_bundle_path, vivado_path_base=vivado_path_base)
    )
    rendered_generated_top = None if generated_top is None else _render_vivado_path(generated_top, vivado_path_base=vivado_path_base)
    rendered_hdl_sources = tuple(_render_vivado_path(path, vivado_path_base=vivado_path_base) for path in hdl_sources)
    return (
        ("dau_artifact_bundle", "" if rendered_bundle_path is None else rendered_bundle_path.as_posix()),
        ("dau_generated_top", "" if rendered_generated_top is None else rendered_generated_top.as_posix()),
        ("dau_bundle_hdl_sources", ",".join(path.as_posix() for path in rendered_hdl_sources)),
    )


def _bundle_hdl_sources_tcl(dau_bundle_hdl_sources: tuple[Path, ...]) -> str:
    if not dau_bundle_hdl_sources:
        return ""
    source_list = " \\\n".join(f'    [file normalize "{source.as_posix()}"]' for source in dau_bundle_hdl_sources)
    return f"""set dau_bundle_hdl_sources [list \\
{source_list}
]
foreach dau_bundle_hdl_source $dau_bundle_hdl_sources {{
    if {{![file exists $dau_bundle_hdl_source]}} {{
        error "missing DAU bundle HDL source: $dau_bundle_hdl_source"
    }}
    if {{[llength [get_files -quiet $dau_bundle_hdl_source]] == 0}} {{
        add_files -norecurse -fileset sources_1 $dau_bundle_hdl_source
    }}
    set_property library xil_defaultlib [get_files $dau_bundle_hdl_source]
    set_property used_in {{synthesis implementation simulation}} [get_files $dau_bundle_hdl_source]
    set dau_bundle_hdl_source_ext [string tolower [file extension $dau_bundle_hdl_source]]
    if {{$dau_bundle_hdl_source_ext eq ".sv" || $dau_bundle_hdl_source_ext eq ".svh"}} {{
        set_property file_type SystemVerilog [get_files $dau_bundle_hdl_source]
    }} elseif {{$dau_bundle_hdl_source_ext eq ".v"}} {{
        set_property file_type Verilog [get_files $dau_bundle_hdl_source]
    }}
}}
"""


def _bundle_manifest_puts_tcl(
    *,
    dau_artifact_bundle_path: Path | None,
    dau_generated_top: Path | None,
    dau_bundle_hdl_sources: tuple[Path, ...],
) -> str:
    if dau_artifact_bundle_path is None and dau_generated_top is None and not dau_bundle_hdl_sources:
        return ""
    bundle_path = "" if dau_artifact_bundle_path is None else dau_artifact_bundle_path.as_posix()
    generated_top = "" if dau_generated_top is None else dau_generated_top.as_posix()
    hdl_sources = ",".join(path.as_posix() for path in dau_bundle_hdl_sources)
    return "\n".join(
        (
            f'puts $manifest_file "dau_artifact_bundle={bundle_path}"',
            f'puts $manifest_file "dau_generated_top={generated_top}"',
            f'puts $manifest_file "dau_bundle_hdl_sources={hdl_sources}"',
        )
    )


def _stream_job_contract_manifest() -> tuple[tuple[str, str], ...]:
    return DEFAULT_STREAM_JOB_REGISTER_CONTRACT.manifest_items()


def _stream_job_contract_puts_tcl() -> str:
    return "\n".join(f'puts $manifest_file "{key}={value}"' for key, value in _stream_job_contract_manifest())


def _selected_module_puts_tcl(selected_module: str | None) -> str:
    if selected_module is None:
        return ""
    return f'puts $manifest_file "selected_module={selected_module}"'


def _split_manifest_list(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item for item in value.split(",") if item)


def dau_overlay_manifest(
    dau_core_hdl_root: Path,
    *,
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit"),
    resource_summary_path: Path = Path("reports/dau_utilization.rpt"),
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt"),
    vivado_log_path: Path = Path("vivado.log"),
    dau_artifact_bundle_path: Path | None = None,
    artifact_bundle: ArtifactBundle | None = None,
    vivado_path_base: Path | None = None,
) -> tuple[tuple[str, str], ...]:
    identity_source = dau_core_hdl_root / "dau_identity_registers.sv"
    identity_axil_source = dau_core_hdl_root / "dau_identity_axil.v"
    legacy_identity_axil_source = dau_core_hdl_root / "dau_identity_axil.sv"
    identity_source = _render_vivado_path(identity_source, vivado_path_base=vivado_path_base)
    identity_axil_source = _render_vivado_path(identity_axil_source, vivado_path_base=vivado_path_base)
    legacy_identity_axil_source = _render_vivado_path(legacy_identity_axil_source, vivado_path_base=vivado_path_base)
    return (
        ("backend", "dau_build.vivado_backend.vivado_overlay"),
        ("dau_identity_registers_sv", identity_source.as_posix()),
        ("dau_identity_axil_v", identity_axil_source.as_posix()),
        ("dau_identity_axil_sv_legacy", legacy_identity_axil_source.as_posix()),
        ("dau_identity_axil_cell", "dau_identity_axil_0"),
        ("spi_ss_i_tieoff", "dau_spi_ss_i_tieoff"),
        ("overlay", overlay_tcl.as_posix()),
        ("bitstream", bitstream_path.as_posix()),
        ("resource_summary", resource_summary_path.as_posix()),
        ("timing_summary", timing_summary_path.as_posix()),
        ("vivado_log", vivado_log_path.as_posix()),
        ("build_status", "planned"),
        *_stream_job_contract_manifest(),
        *_dau_artifact_bundle_manifest(
            dau_artifact_bundle_path=dau_artifact_bundle_path,
            artifact_bundle=artifact_bundle,
            vivado_path_base=vivado_path_base,
        ),
    )


def dau_overlay_manifest_text(
    dau_core_hdl_root: Path,
    *,
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit"),
) -> str:
    lines = (f"{key}={value}" for key, value in dau_overlay_manifest(dau_core_hdl_root, overlay_tcl=overlay_tcl, bitstream_path=bitstream_path))
    return "\n".join(lines) + "\n"


def dau_overlay_tcl(
    dau_core_hdl_root: Path,
    *,
    manifest_path: Path = Path("dau-vivado.manifest"),
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit"),
    resource_summary_path: Path = Path("reports/dau_utilization.rpt"),
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt"),
    vivado_log_path: Path = Path("vivado.log"),
    dau_artifact_bundle_path: Path | None = None,
    dau_generated_top: Path | None = None,
    dau_bundle_hdl_sources: tuple[Path, ...] = (),
    selected_module: str | None = None,
    vivado_path_base: Path | None = None,
) -> str:
    identity_source = _render_vivado_path(dau_core_hdl_root / "dau_identity_registers.sv", vivado_path_base=vivado_path_base)
    identity_axil_source = _render_vivado_path(dau_core_hdl_root / "dau_identity_axil.v", vivado_path_base=vivado_path_base)
    legacy_identity_axil_source = _render_vivado_path(dau_core_hdl_root / "dau_identity_axil.sv", vivado_path_base=vivado_path_base)
    rendered_bundle_path = (
        None if dau_artifact_bundle_path is None else _render_vivado_path(dau_artifact_bundle_path, vivado_path_base=vivado_path_base)
    )
    rendered_generated_top = None if dau_generated_top is None else _render_vivado_path(dau_generated_top, vivado_path_base=vivado_path_base)
    rendered_bundle_sources = tuple(_render_vivado_path(path, vivado_path_base=vivado_path_base) for path in dau_bundle_hdl_sources)
    return f"""# Generated by dau-build; source before scripts/build.tcl.
set dau_identity_registers_sv [file normalize \"{identity_source.as_posix()}\"]
set dau_identity_axil_v [file normalize "{identity_axil_source.as_posix()}"]
set dau_identity_axil_sv_legacy [file normalize "{legacy_identity_axil_source.as_posix()}"]
if {{[llength [get_projects -quiet]] == 0}} {{
    if {{![file exists \"project.xpr\"]}} {{
        error \"project.xpr is missing; restore the Vivado project before this overlay\"
    }}
    open_project project.xpr
}}
set stale_dau_axil_source [get_files -quiet $dau_identity_axil_sv_legacy]
if {{[llength $stale_dau_axil_source] != 0}} {{
    remove_files $stale_dau_axil_source
}}
set locked_dau_ips [get_ips -quiet -filter {{IS_LOCKED == 1}}]
if {{[llength $locked_dau_ips] != 0}} {{
    upgrade_ip $locked_dau_ips
}}
foreach dau_hdl_source [list $dau_identity_registers_sv $dau_identity_axil_v] {{
    if {{![file exists $dau_hdl_source]}} {{
        error "missing DAU HDL source: $dau_hdl_source"
    }}
    if {{[llength [get_files -quiet $dau_hdl_source]] == 0}} {{
        add_files -norecurse -fileset sources_1 $dau_hdl_source
    }}
    set_property library xil_defaultlib [get_files $dau_hdl_source]
    set_property used_in {{synthesis implementation simulation}} [get_files $dau_hdl_source]
}}
set_property file_type SystemVerilog [get_files $dau_identity_registers_sv]
set_property file_type Verilog [get_files $dau_identity_axil_v]
{_bundle_hdl_sources_tcl(rendered_bundle_sources)}
update_compile_order -fileset sources_1
set top_bd [get_files -quiet project.srcs/sources_1/bd/Top/Top.bd]
if {{[llength $top_bd] == 0}} {{
    error "missing Top block design at project.srcs/sources_1/bd/Top/Top.bd"
}}
open_bd_design [get_files project.srcs/sources_1/bd/Top/Top.bd]
foreach net_name {{axi_interconnect_0_M00_AXI Model_dout Version_dout}} {{
    set old_net [get_bd_intf_nets -quiet $net_name]
    if {{[llength $old_net] != 0}} {{
        delete_bd_objs $old_net
    }}
    set old_net [get_bd_nets -quiet $net_name]
    if {{[llength $old_net] != 0}} {{
        delete_bd_objs $old_net
    }}
}}
foreach cell_name {{dau_identity_axil_0 axi_gpio_0 Model Version}} {{
    set old_cell [get_bd_cells -quiet $cell_name]
    if {{[llength $old_cell] != 0}} {{
        delete_bd_objs $old_cell
    }}
}}
create_bd_cell -type module -reference dau_identity_axil dau_identity_axil_0
connect_bd_intf_net -intf_net axi_interconnect_0_M00_AXI [get_bd_intf_pins axi_interconnect_0/M00_AXI] [get_bd_intf_pins dau_identity_axil_0/S_AXI]
connect_bd_net -net S00_ACLK_1 [get_bd_pins xdma_0/axi_aclk] [get_bd_pins dau_identity_axil_0/s_axi_aclk]
connect_bd_net -net S00_ARESETN_1 [get_bd_pins xdma_0/axi_aresetn] [get_bd_pins dau_identity_axil_0/s_axi_aresetn]
set dau_identity_addr_seg [lindex [get_bd_addr_segs -quiet dau_identity_axil_0/S_AXI/*] 0]
if {{$dau_identity_addr_seg eq ""}} {{
    error "missing DAU AXI-Lite address segment for dau_identity_axil_0/S_AXI"
}}
assign_bd_address -offset 0x00001000 -range 0x00001000 -target_address_space [get_bd_addr_spaces xdma_0/M_AXI_LITE] $dau_identity_addr_seg -force
set spi_ss_i_pin [get_bd_pins -quiet axi_quad_spi_0/ss_i]
if {{[llength $spi_ss_i_pin] != 0}} {{
    if {{[llength [get_bd_cells -quiet dau_spi_ss_i_tieoff]] == 0}} {{
        create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 dau_spi_ss_i_tieoff
    }}
    set_property -dict [list CONFIG.CONST_WIDTH {{1}} CONFIG.CONST_VAL {{0}}] [get_bd_cells dau_spi_ss_i_tieoff]
    set spi_ss_i_nets [get_bd_nets -quiet -of_objects $spi_ss_i_pin]
    if {{[llength $spi_ss_i_nets] == 0}} {{
        connect_bd_net -net dau_spi_ss_i_tieoff_dout [get_bd_pins dau_spi_ss_i_tieoff/dout] $spi_ss_i_pin
    }} elseif {{[lsearch -exact $spi_ss_i_nets /dau_spi_ss_i_tieoff_dout] < 0}} {{
        delete_bd_objs $spi_ss_i_nets
        connect_bd_net -net dau_spi_ss_i_tieoff_dout [get_bd_pins dau_spi_ss_i_tieoff/dout] $spi_ss_i_pin
    }}
}}
validate_bd_design
save_bd_design
set wrapper_path [make_wrapper -files [get_files project.srcs/sources_1/bd/Top/Top.bd] -top]
if {{[llength [get_files -quiet $wrapper_path]] == 0}} {{
    add_files -norecurse -fileset sources_1 $wrapper_path
}}
set_property -name "top" -value "Top_wrapper" -objects [get_filesets sources_1]
update_compile_order -fileset sources_1
set dau_identity_ooc_runs [get_runs -quiet *dau_identity_axil*]
if {{[llength $dau_identity_ooc_runs] != 0}} {{
    reset_run $dau_identity_ooc_runs
}}
# The Python staging step writes the full structured manifest before Vivado
# runs. Append runtime-discovered fields here so executing the command plan
# does not clobber the typed backend contract.
set manifest_file [open \"{manifest_path.as_posix()}\" a]
puts $manifest_file \"dau_identity_registers_sv=$dau_identity_registers_sv\"
puts $manifest_file "dau_identity_axil_v=$dau_identity_axil_v"
puts $manifest_file "dau_identity_axil_cell=dau_identity_axil_0"
puts $manifest_file "dau_identity_ooc_runs=$dau_identity_ooc_runs"
puts $manifest_file "spi_ss_i_tieoff=dau_spi_ss_i_tieoff"
puts $manifest_file "overlay={overlay_tcl.as_posix()}"
puts $manifest_file "bitstream={bitstream_path.as_posix()}"
puts $manifest_file "resource_summary={resource_summary_path.as_posix()}"
puts $manifest_file "timing_summary={timing_summary_path.as_posix()}"
puts $manifest_file "vivado_log={vivado_log_path.as_posix()}"
puts $manifest_file "build_status=planned"
{_stream_job_contract_puts_tcl()}
{_selected_module_puts_tcl(selected_module)}
{_bundle_manifest_puts_tcl(dau_artifact_bundle_path=rendered_bundle_path, dau_generated_top=rendered_generated_top, dau_bundle_hdl_sources=rendered_bundle_sources)}
close $manifest_file
"""


def write_dau_overlay_tcl(path: Path, *, dau_core_hdl_root: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dau_overlay_tcl(dau_core_hdl_root), encoding="utf-8")
    return path


def write_dau_overlay_manifest(path: Path, *, dau_core_hdl_root: Path, overlay_tcl: Path = Path("scripts/dau_overlay.tcl")) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dau_overlay_manifest_text(dau_core_hdl_root, overlay_tcl=overlay_tcl), encoding="utf-8")
    return path


def project_build_script(
    *,
    work_root: Path,
    project_tcl: Path,
    vivado_settings: Path,
    vivado_executable: str,
    vivado_invocation: str = "standard",
    vivado_mount_root: Path | None = None,
) -> str:
    command_root = vivado_mount_root or work_root
    command_tcl = _command_plan_tcl_path(build_root=work_root, tcl_path=project_tcl, vivado_mount_root=vivado_mount_root)
    vivado_command = _vivado_source_command(vivado_executable=vivado_executable, tcl_path=command_tcl, vivado_invocation=vivado_invocation)
    if vivado_invocation == "source-only":
        return " && ".join((f"cd {shlex.quote(str(command_root))}", vivado_command))
    return " && ".join(
        (
            f"cd {shlex.quote(str(work_root))}",
            f". {shlex.quote(str(vivado_settings))}",
            vivado_command,
        )
    )


def vivado_build_tcl(
    *,
    manifest_path: Path = Path("dau-vivado.manifest"),
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit"),
    resource_summary_path: Path = Path("reports/dau_utilization.rpt"),
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt"),
    vivado_log_path: Path = Path("vivado.log"),
) -> str:
    script = """# Generated by dau-build; source after scripts/dau_overlay.tcl.
open_project project.xpr
reset_run synth_1
launch_runs synth_1 -jobs 2
wait_on_run synth_1
open_run synth_1

foreach lane [list \
    [list {Top_i/xdma_0/inst/Top_xdma_0_0_pcie2_to_pcie3_wrapper_i/pcie2_ip_i/inst/inst/gt_top_i/pipe_wrapper_i/pipe_lane[3].gt_wrapper_i/gtp_channel.gtpe2_channel_i} GTPE2_CHANNEL_X0Y7] \
    [list {Top_i/xdma_0/inst/Top_xdma_0_0_pcie2_to_pcie3_wrapper_i/pcie2_ip_i/inst/inst/gt_top_i/pipe_wrapper_i/pipe_lane[0].gt_wrapper_i/gtp_channel.gtpe2_channel_i} GTPE2_CHANNEL_X0Y6] \
    [list {Top_i/xdma_0/inst/Top_xdma_0_0_pcie2_to_pcie3_wrapper_i/pcie2_ip_i/inst/inst/gt_top_i/pipe_wrapper_i/pipe_lane[2].gt_wrapper_i/gtp_channel.gtpe2_channel_i} GTPE2_CHANNEL_X0Y5] \
    [list {Top_i/xdma_0/inst/Top_xdma_0_0_pcie2_to_pcie3_wrapper_i/pcie2_ip_i/inst/inst/gt_top_i/pipe_wrapper_i/pipe_lane[1].gt_wrapper_i/gtp_channel.gtpe2_channel_i} GTPE2_CHANNEL_X0Y4] \
] {
    set cell_path [lindex $lane 0]
    set lane_loc [lindex $lane 1]
    set lane_cells [get_cells -quiet $cell_path]
    if {[llength $lane_cells] == 0} {
        puts "dau-build: skipping missing PCIe lane cell $cell_path"
        continue
    }
    reset_property LOC $lane_cells
    set_property LOC $lane_loc $lane_cells
}

launch_runs impl_1 -jobs 6 -to_step write_bitstream
wait_on_run impl_1
open_run impl_1
set default_bitstream_path [file normalize "project.runs/impl_1/Top_wrapper.bit"]
set expected_bitstream_path [file normalize "__BITSTREAM_PATH__"]
if {![file exists $default_bitstream_path]} {
    error "Vivado implementation did not produce default bitstream: $default_bitstream_path"
}
file mkdir [file dirname $expected_bitstream_path]
if {$expected_bitstream_path ne $default_bitstream_path} {
    file copy -force $default_bitstream_path $expected_bitstream_path
}
file mkdir [file dirname "__RESOURCE_SUMMARY_PATH__"]
file mkdir [file dirname "__TIMING_SUMMARY_PATH__"]
report_utilization -file "__RESOURCE_SUMMARY_PATH__"
report_timing_summary -file "__TIMING_SUMMARY_PATH__"
if {![file exists $expected_bitstream_path]} {
    error "expected Vivado bitstream was not produced: $expected_bitstream_path"
}
file mkdir mcs
write_cfgmem -format mcs -size 16 -interface SPIx4 -force -loadbit "up 0 $expected_bitstream_path" -file "./mcs/top.mcs"
write_cfgmem -format bin -size 16 -interface SPIx4 -force -loadbit "up 0 $expected_bitstream_path" -file "./mcs/top.bin"
write_verilog -mode funcsim -force Top.v
set manifest_file [open "__MANIFEST_PATH__" a]
puts $manifest_file "bitstream=__BITSTREAM_PATH__"
puts $manifest_file "resource_summary=__RESOURCE_SUMMARY_PATH__"
puts $manifest_file "timing_summary=__TIMING_SUMMARY_PATH__"
puts $manifest_file "vivado_log=__VIVADO_LOG_PATH__"
puts $manifest_file "build_status=built"
close $manifest_file
close_design
puts "Implementation done!"
"""
    return (
        script.replace("__MANIFEST_PATH__", manifest_path.as_posix())
        .replace("__BITSTREAM_PATH__", bitstream_path.as_posix())
        .replace("__RESOURCE_SUMMARY_PATH__", resource_summary_path.as_posix())
        .replace("__TIMING_SUMMARY_PATH__", timing_summary_path.as_posix())
        .replace("__VIVADO_LOG_PATH__", vivado_log_path.as_posix())
    )


def overlay_build_script(
    *,
    work_root: Path,
    overlay_tcl: Path,
    build_tcl: Path = Path("scripts/dau_build.tcl"),
    vivado_settings: Path,
    vivado_executable: str,
    vivado_invocation: str = "standard",
    vivado_mount_root: Path | None = None,
) -> str:
    command_root = vivado_mount_root or work_root
    overlay_command_tcl = _command_plan_tcl_path(build_root=work_root, tcl_path=overlay_tcl, vivado_mount_root=vivado_mount_root)
    build_command_tcl = _command_plan_tcl_path(build_root=work_root, tcl_path=build_tcl, vivado_mount_root=vivado_mount_root)
    overlay_command = _vivado_source_command(vivado_executable=vivado_executable, tcl_path=overlay_command_tcl, vivado_invocation=vivado_invocation)
    build_command = _vivado_source_command(vivado_executable=vivado_executable, tcl_path=build_command_tcl, vivado_invocation=vivado_invocation)
    top_v_path = _path_relative_to(work_root / "Top.v", vivado_mount_root) if vivado_mount_root is not None else Path("Top.v")
    if vivado_invocation == "source-only":
        return " && ".join((f"cd {shlex.quote(str(command_root))}", overlay_command, shlex.join(("rm", "-f", str(top_v_path))), build_command))
    return " && ".join(
        (
            f"cd {shlex.quote(str(work_root))}",
            f". {shlex.quote(str(vivado_settings))}",
            overlay_command,
            shlex.join(("rm", "-f", "Top.v")),
            build_command,
        )
    )


def overlay_command_plan_text(
    *,
    work_root: Path,
    overlay_tcl: Path,
    build_tcl: Path = Path("scripts/dau_build.tcl"),
    vivado_settings: Path,
    vivado_executable: str,
    vivado_invocation: str = "standard",
    vivado_mount_root: Path | None = None,
) -> str:
    return "\n".join(
        (
            "# Generated by dau-build; review before invoking Vivado.",
            f"vivado-overlay-build\t{overlay_build_script(work_root=work_root, overlay_tcl=overlay_tcl, build_tcl=build_tcl, vivado_settings=vivado_settings, vivado_executable=vivado_executable, vivado_invocation=vivado_invocation, vivado_mount_root=vivado_mount_root)}",
            "",
        )
    )


def flash_script(*, work_root: Path, vivado_settings: Path, vivado_executable: str, vivado_invocation: str = "standard") -> str:
    flash_command = _vivado_source_command(
        vivado_executable=vivado_executable, tcl_path=Path("scripts/flash.tcl"), vivado_invocation=vivado_invocation
    )
    if vivado_invocation == "source-only":
        return " && ".join((f"cd {shlex.quote(str(work_root))}", flash_command))
    return " && ".join(
        (
            f"cd {shlex.quote(str(work_root))}",
            f". {shlex.quote(str(vivado_settings))}",
            flash_command,
        )
    )


def _vivado_source_command(*, vivado_executable: str, tcl_path: Path, vivado_invocation: str) -> str:
    if vivado_invocation == "source-only":
        return shlex.join((vivado_executable, str(tcl_path)))
    return shlex.join((vivado_executable, "-mode", "batch", "-source", str(tcl_path)))
