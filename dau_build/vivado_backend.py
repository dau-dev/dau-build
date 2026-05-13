from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NiteFuryBackendRequest:
    dau_core_hdl_root: Path
    build_root: Path
    artifact_stem: str = "dau-nitefury"
    platform: str = "nitefury"
    shell: str = "nitefury-xdma"
    operator_set: tuple[str, ...] = ("identity",)
    register_map_version: str = "0.1"
    stream_protocol_version: str = "0.1"
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    build_tcl: Path = Path("scripts/dau_build.tcl")
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit")
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    vivado_executable: str = "vivado"

    @property
    def resolved_manifest_path(self) -> Path:
        return self.manifest_path if self.manifest_path is not None else Path(f"{self.artifact_stem}.manifest")

    @property
    def resolved_command_plan_path(self) -> Path:
        return self.command_plan_path if self.command_plan_path is not None else Path(f"{self.artifact_stem}.plan")


@dataclass(frozen=True)
class NiteFuryProjectGenerationRequest:
    source_nite_root: Path
    work_nite_root: Path
    dau_core_root: Path
    dau_driver_root: Path
    dau_utils_root: Path | None = None
    dau_build_manifest_path: Path | None = None
    dau_top_sv_path: Path | None = None
    artifact_stem: str = "dau-nitefury"
    platform: str = "nitefury"
    shell: str = "nitefury-xdma"
    operator_set: tuple[str, ...] = ("identity",)
    register_map_version: str = "0.1"
    stream_protocol_version: str = "0.1"
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    build_tcl: Path = Path("scripts/dau_build.tcl")
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    project_manifest_path: Path | None = None
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit")
    xdma_module_path: Path = Path("sw/xdma/xdma.ko")
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    vivado_executable: str = "vivado"
    plan_executable: str = "dau-nitefury-plan"

    @property
    def dau_core_hdl_root(self) -> Path:
        return self.dau_core_root / "dau_core" / "hdl"

    @property
    def resolved_project_manifest_path(self) -> Path:
        return self.project_manifest_path if self.project_manifest_path is not None else Path(f"{self.artifact_stem}.project")

    @property
    def backend_request(self) -> NiteFuryBackendRequest:
        return NiteFuryBackendRequest(
            dau_core_hdl_root=self.dau_core_hdl_root,
            build_root=self.work_nite_root,
            artifact_stem=self.artifact_stem,
            platform=self.platform,
            shell=self.shell,
            operator_set=self.operator_set,
            register_map_version=self.register_map_version,
            stream_protocol_version=self.stream_protocol_version,
            overlay_tcl=self.overlay_tcl,
            build_tcl=self.build_tcl,
            manifest_path=self.manifest_path,
            command_plan_path=self.command_plan_path,
            bitstream_path=self.bitstream_path,
            vivado_settings=self.vivado_settings,
            vivado_executable=self.vivado_executable,
        )


@dataclass(frozen=True)
class NiteFuryBackendArtifacts:
    overlay_tcl_path: Path
    manifest_path: Path
    command_plan_path: Path
    build_tcl_path: Path
    bitstream_path: Path
    overlay_tcl_text: str
    build_tcl_text: str
    manifest_text: str
    command_plan_text: str


@dataclass(frozen=True)
class NiteFuryProjectGenerationArtifacts:
    project_manifest_path: Path
    project_manifest_text: str
    backend_artifacts: NiteFuryBackendArtifacts


@dataclass(frozen=True)
class NiteFuryBackendArtifactValidation:
    manifest_path: Path
    command_plan_path: Path
    overlay_tcl_path: Path | None
    bitstream_path: Path | None
    manifest_items: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class NiteFuryProjectArtifactValidation:
    project_manifest_path: Path
    project_manifest_items: tuple[tuple[str, str], ...]
    backend_validation: NiteFuryBackendArtifactValidation
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


def generate_nitefury_backend_artifacts(request: NiteFuryBackendRequest) -> NiteFuryBackendArtifacts:
    manifest_path = request.resolved_manifest_path
    command_plan_path = request.resolved_command_plan_path
    return NiteFuryBackendArtifacts(
        overlay_tcl_path=_build_artifact_path(request.build_root, request.overlay_tcl),
        manifest_path=_build_artifact_path(request.build_root, manifest_path),
        command_plan_path=_build_artifact_path(request.build_root, command_plan_path),
        build_tcl_path=_build_artifact_path(request.build_root, request.build_tcl),
        bitstream_path=_build_artifact_path(request.build_root, request.bitstream_path),
        overlay_tcl_text=dau_overlay_tcl(
            request.dau_core_hdl_root,
            manifest_path=manifest_path,
            overlay_tcl=request.overlay_tcl,
            bitstream_path=request.bitstream_path,
        ),
        build_tcl_text=nitefury_build_tcl(),
        manifest_text=nitefury_backend_manifest_text(request),
        command_plan_text=overlay_command_plan_text(
            nite_root=request.build_root,
            overlay_tcl=request.overlay_tcl,
            build_tcl=request.build_tcl,
            vivado_settings=request.vivado_settings,
            vivado_executable=request.vivado_executable,
        ),
    )


def generate_nitefury_project_generation_artifacts(request: NiteFuryProjectGenerationRequest) -> NiteFuryProjectGenerationArtifacts:
    return NiteFuryProjectGenerationArtifacts(
        project_manifest_path=_build_artifact_path(request.work_nite_root, request.resolved_project_manifest_path),
        project_manifest_text=nitefury_project_generation_manifest_text(request),
        backend_artifacts=generate_nitefury_backend_artifacts(request.backend_request),
    )


def validate_nitefury_backend_artifact_bundle(
    build_root: Path,
    *,
    manifest_path: Path = Path("dau-nitefury.manifest"),
    command_plan_path: Path = Path("dau-nitefury.plan"),
) -> NiteFuryBackendArtifactValidation:
    resolved_manifest_path = _build_artifact_path(build_root, manifest_path)
    resolved_command_plan_path = _build_artifact_path(build_root, command_plan_path)
    errors: list[str] = []
    manifest_items: tuple[tuple[str, str], ...] = ()
    overlay_tcl_path: Path | None = None
    bitstream_path: Path | None = None

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

    return NiteFuryBackendArtifactValidation(
        manifest_path=resolved_manifest_path,
        command_plan_path=resolved_command_plan_path,
        overlay_tcl_path=overlay_tcl_path,
        bitstream_path=bitstream_path,
        manifest_items=manifest_items,
        errors=tuple(errors),
    )


def validate_nitefury_project_artifact_bundle(
    build_root: Path,
    *,
    project_manifest_path: Path = Path("dau-nitefury.project"),
    manifest_path: Path = Path("dau-nitefury.manifest"),
    command_plan_path: Path = Path("dau-nitefury.plan"),
) -> NiteFuryProjectArtifactValidation:
    backend_validation = validate_nitefury_backend_artifact_bundle(
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

    return NiteFuryProjectArtifactValidation(
        project_manifest_path=resolved_project_manifest_path,
        project_manifest_items=project_manifest_items,
        backend_validation=backend_validation,
        errors=tuple(errors),
    )


def nitefury_backend_manifest(request: NiteFuryBackendRequest) -> tuple[tuple[str, str], ...]:
    return (
        *dau_overlay_manifest(
            request.dau_core_hdl_root,
            overlay_tcl=request.overlay_tcl,
            bitstream_path=request.bitstream_path,
        ),
        ("platform", request.platform),
        ("shell", request.shell),
        ("artifact_stem", request.artifact_stem),
        ("build_root", request.build_root.as_posix()),
        ("manifest", request.resolved_manifest_path.as_posix()),
        ("command_plan", request.resolved_command_plan_path.as_posix()),
        ("build_tcl", request.build_tcl.as_posix()),
        ("register_map_version", request.register_map_version),
        ("stream_protocol_version", request.stream_protocol_version),
        ("operator_set", ",".join(request.operator_set)),
        ("vivado_settings", request.vivado_settings.as_posix()),
        ("vivado_executable", request.vivado_executable),
    )


def nitefury_backend_manifest_text(request: NiteFuryBackendRequest) -> str:
    lines = (f"{key}={value}" for key, value in nitefury_backend_manifest(request))
    return "\n".join(lines) + "\n"


def nitefury_project_generation_manifest(request: NiteFuryProjectGenerationRequest) -> tuple[tuple[str, str], ...]:
    backend_request = request.backend_request
    items = [
        ("project_generator", "dau_build.vivado_backend.nitefury_project"),
        ("platform", request.platform),
        ("shell", request.shell),
        ("artifact_stem", request.artifact_stem),
        ("source_nite_root", request.source_nite_root.as_posix()),
        ("work_nite_root", request.work_nite_root.as_posix()),
        ("dau_core_root", request.dau_core_root.as_posix()),
        ("dau_core_hdl_root", request.dau_core_hdl_root.as_posix()),
        ("dau_driver_root", request.dau_driver_root.as_posix()),
        ("dau_utils_root", "" if request.dau_utils_root is None else request.dau_utils_root.as_posix()),
        ("dau_build_manifest", "" if request.dau_build_manifest_path is None else request.dau_build_manifest_path.as_posix()),
        ("dau_top_sv", "" if request.dau_top_sv_path is None else request.dau_top_sv_path.as_posix()),
        ("project_manifest", request.resolved_project_manifest_path.as_posix()),
        ("backend_manifest", backend_request.resolved_manifest_path.as_posix()),
        ("backend_command_plan", backend_request.resolved_command_plan_path.as_posix()),
        ("overlay_tcl", request.overlay_tcl.as_posix()),
        ("build_tcl", request.build_tcl.as_posix()),
        ("bitstream", request.bitstream_path.as_posix()),
        ("xdma_module", request.xdma_module_path.as_posix()),
        ("register_map_version", request.register_map_version),
        ("stream_protocol_version", request.stream_protocol_version),
        ("operator_set", ",".join(request.operator_set)),
        ("vivado_settings", request.vivado_settings.as_posix()),
        ("vivado_executable", request.vivado_executable),
        ("stage_command", nitefury_project_stage_command(request)),
        ("build_command", nitefury_project_build_command(request)),
        ("validate_command", nitefury_project_validate_command(request)),
    ]
    return tuple(items)


def nitefury_project_generation_manifest_text(request: NiteFuryProjectGenerationRequest) -> str:
    lines = (f"{key}={value}" for key, value in nitefury_project_generation_manifest(request))
    return "\n".join(lines) + "\n"


def nitefury_project_stage_command(request: NiteFuryProjectGenerationRequest) -> str:
    argv = [
        request.plan_executable,
        "stage-vivado-overlay",
        "--source-nite-root",
        str(request.source_nite_root),
        "--nite-root",
        str(request.work_nite_root),
        "--dau-core-root",
        str(request.dau_core_root),
        "--artifact-stem",
        request.artifact_stem,
        "--backend-platform",
        request.platform,
        "--backend-shell",
        request.shell,
        "--register-map-version",
        request.register_map_version,
        "--stream-protocol-version",
        request.stream_protocol_version,
        "--overlay-tcl",
        str(request.overlay_tcl),
        "--manifest-path",
        str(request.backend_request.resolved_manifest_path),
        "--command-plan-path",
        str(request.backend_request.resolved_command_plan_path),
        "--vivado-settings",
        str(request.vivado_settings),
        "--vivado",
        request.vivado_executable,
    ]
    for operator in request.operator_set:
        argv.extend(("--operator", operator))
    return shlex.join(argv)


def nitefury_project_build_command(request: NiteFuryProjectGenerationRequest) -> str:
    argv = [
        request.plan_executable,
        "local-build-and-program",
        "--source-nite-root",
        str(request.source_nite_root),
        "--nite-root",
        str(request.work_nite_root),
        "--dau-core-root",
        str(request.dau_core_root),
        "--dau-driver-root",
        str(request.dau_driver_root),
        "--overlay-tcl",
        str(request.overlay_tcl),
        "--bitstream",
        str(request.bitstream_path),
        "--vivado-settings",
        str(request.vivado_settings),
        "--vivado",
        request.vivado_executable,
    ]
    if request.dau_utils_root is not None:
        argv.extend(("--dau-utils-root", str(request.dau_utils_root)))
    return shlex.join(argv)


def nitefury_project_validate_command(request: NiteFuryProjectGenerationRequest) -> str:
    argv = [
        request.plan_executable,
        "validate-bitstream",
        "--nite-root",
        str(request.work_nite_root),
        "--bitstream",
        str(request.bitstream_path),
        "--dau-core-root",
        str(request.dau_core_root),
        "--dau-driver-root",
        str(request.dau_driver_root),
    ]
    if request.dau_utils_root is not None:
        argv.extend(("--dau-utils-root", str(request.dau_utils_root)))
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
        "register_map_version",
        "stream_protocol_version",
        "operator_set",
        "vivado_settings",
        "vivado_executable",
    )
    for key in required_keys:
        if not manifest.get(key):
            errors.append(f"manifest missing required key: {key}")
    if manifest.get("backend") and manifest["backend"] != "dau_build.vivado_backend.nitefury_overlay":
        errors.append(f"unexpected backend: {manifest['backend']}")
    if manifest.get("build_root") and manifest["build_root"] != build_root.as_posix():
        errors.append(f"manifest build_root mismatch: {manifest['build_root']} != {build_root.as_posix()}")
    if manifest.get("manifest") and manifest["manifest"] != manifest_path.as_posix():
        errors.append(f"manifest path mismatch: {manifest['manifest']} != {manifest_path.as_posix()}")
    if manifest.get("command_plan") and manifest["command_plan"] != command_plan_path.as_posix():
        errors.append(f"command plan path mismatch: {manifest['command_plan']} != {command_plan_path.as_posix()}")
    return tuple(errors)


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
        "source_nite_root",
        "work_nite_root",
        "dau_core_root",
        "dau_core_hdl_root",
        "dau_driver_root",
        "dau_utils_root",
        "dau_build_manifest",
        "dau_top_sv",
        "project_manifest",
        "backend_manifest",
        "backend_command_plan",
        "overlay_tcl",
        "build_tcl",
        "bitstream",
        "xdma_module",
        "register_map_version",
        "stream_protocol_version",
        "operator_set",
        "vivado_settings",
        "vivado_executable",
        "stage_command",
        "build_command",
        "validate_command",
    )
    optional_empty_keys = {"dau_utils_root", "dau_build_manifest", "dau_top_sv"}
    for key in required_keys:
        if key not in project_manifest:
            errors.append(f"project manifest missing required key: {key}")
        elif key not in optional_empty_keys and not project_manifest[key]:
            errors.append(f"project manifest has empty required key: {key}")

    if project_manifest.get("project_generator") and project_manifest["project_generator"] != "dau_build.vivado_backend.nitefury_project":
        errors.append(f"unexpected project generator: {project_manifest['project_generator']}")
    if project_manifest.get("work_nite_root") and project_manifest["work_nite_root"] != build_root.as_posix():
        errors.append(f"project work root mismatch: {project_manifest['work_nite_root']} != {build_root.as_posix()}")
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

    errors.extend(_validate_project_backend_manifest_cross_refs(project_manifest=project_manifest, backend_manifest=backend_manifest))
    errors.extend(
        _validate_project_manifest_commands(project_manifest=project_manifest, manifest_path=manifest_path, command_plan_path=command_plan_path)
    )
    return tuple(errors)


def _validate_project_backend_manifest_cross_refs(*, project_manifest: dict[str, str], backend_manifest: dict[str, str]) -> tuple[str, ...]:
    errors: list[str] = []
    key_pairs = (
        ("platform", "platform"),
        ("shell", "shell"),
        ("artifact_stem", "artifact_stem"),
        ("overlay_tcl", "overlay"),
        ("build_tcl", "build_tcl"),
        ("bitstream", "bitstream"),
        ("register_map_version", "register_map_version"),
        ("stream_protocol_version", "stream_protocol_version"),
        ("operator_set", "operator_set"),
        ("vivado_settings", "vivado_settings"),
        ("vivado_executable", "vivado_executable"),
    )
    for project_key, backend_key in key_pairs:
        project_value = project_manifest.get(project_key)
        backend_value = backend_manifest.get(backend_key)
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
    source_nite_root = project_manifest.get("source_nite_root", "")
    work_nite_root = project_manifest.get("work_nite_root", "")
    dau_core_root = project_manifest.get("dau_core_root", "")
    dau_driver_root = project_manifest.get("dau_driver_root", "")
    dau_utils_root = project_manifest.get("dau_utils_root", "")
    overlay_tcl = project_manifest.get("overlay_tcl", "")
    bitstream = project_manifest.get("bitstream", "")
    vivado_settings = project_manifest.get("vivado_settings", "")
    vivado_executable = project_manifest.get("vivado_executable", "")

    errors.extend(
        _validate_project_command(
            label="stage_command",
            command=project_manifest.get("stage_command", ""),
            expected_plan="stage-vivado-overlay",
            required_options=(
                ("--source-nite-root", source_nite_root),
                ("--nite-root", work_nite_root),
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
            ),
        )
    )
    build_required_options = [
        ("--source-nite-root", source_nite_root),
        ("--nite-root", work_nite_root),
        ("--dau-core-root", dau_core_root),
        ("--dau-driver-root", dau_driver_root),
        ("--overlay-tcl", overlay_tcl),
        ("--bitstream", bitstream),
        ("--vivado-settings", vivado_settings),
        ("--vivado", vivado_executable),
    ]
    validate_required_options = [
        ("--nite-root", work_nite_root),
        ("--bitstream", bitstream),
        ("--dau-core-root", dau_core_root),
        ("--dau-driver-root", dau_driver_root),
    ]
    if dau_utils_root:
        build_required_options.append(("--dau-utils-root", dau_utils_root))
        validate_required_options.append(("--dau-utils-root", dau_utils_root))
    errors.extend(
        _validate_project_command(
            label="build_command",
            command=project_manifest.get("build_command", ""),
            expected_plan="local-build-and-program",
            required_options=tuple(build_required_options),
        )
    )
    errors.extend(
        _validate_project_command(
            label="validate_command",
            command=project_manifest.get("validate_command", ""),
            expected_plan="validate-bitstream",
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
    if len(tokens) < 2 or tokens[1] != expected_plan:
        actual_plan = tokens[1] if len(tokens) > 1 else ""
        errors.append(f"project {label} plan mismatch: {actual_plan} != {expected_plan}")
    for option, expected_value in required_options:
        actual_value = _command_option_value(tokens, option)
        if actual_value is None:
            errors.append(f"project {label} missing option: {option}")
        elif expected_value and actual_value != expected_value:
            errors.append(f"project {label} option {option} mismatch: {actual_value} != {expected_value}")
    return tuple(errors)


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
    if f"cd {shlex.quote(str(build_root))}" not in command_plan_text:
        errors.append(f"command plan does not cd into build root: {build_root}")
    if overlay_tcl and f"-source {shlex.quote(overlay_tcl)}" not in command_plan_text:
        errors.append(f"command plan does not source overlay Tcl: {overlay_tcl}")
    if build_tcl and f"-source {shlex.quote(build_tcl)}" not in command_plan_text:
        errors.append(f"command plan does not source build Tcl: {build_tcl}")
    return tuple(errors)


def _validate_overlay_tcl_contract(*, manifest: dict[str, str], overlay_tcl_text: str) -> tuple[str, ...]:
    errors: list[str] = []
    for key in ("overlay", "bitstream"):
        value = manifest.get(key)
        if value is None:
            continue
        expected = f'"{key}={value}"'
        if expected not in overlay_tcl_text:
            errors.append(f"overlay Tcl does not write manifest field: {key}={value}")
    return tuple(errors)


def _build_artifact_path(build_root: Path, artifact_path: Path) -> Path:
    if artifact_path.is_absolute():
        return artifact_path
    return build_root / artifact_path


def dau_overlay_manifest(
    dau_core_hdl_root: Path,
    *,
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit"),
) -> tuple[tuple[str, str], ...]:
    identity_source = dau_core_hdl_root / "dau_identity_registers.sv"
    identity_axil_source = dau_core_hdl_root / "dau_identity_axil.v"
    legacy_identity_axil_source = dau_core_hdl_root / "dau_identity_axil.sv"
    return (
        ("backend", "dau_build.vivado_backend.nitefury_overlay"),
        ("dau_identity_registers_sv", identity_source.as_posix()),
        ("dau_identity_axil_v", identity_axil_source.as_posix()),
        ("dau_identity_axil_sv_legacy", legacy_identity_axil_source.as_posix()),
        ("dau_identity_axil_cell", "dau_identity_axil_0"),
        ("spi_ss_i_tieoff", "dau_spi_ss_i_tieoff"),
        ("register_window_offset", "0x00001000"),
        ("overlay", overlay_tcl.as_posix()),
        ("bitstream", bitstream_path.as_posix()),
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
    manifest_path: Path = Path("dau-nitefury.manifest"),
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    bitstream_path: Path = Path("project.runs/impl_1/Top_wrapper.bit"),
) -> str:
    identity_source = dau_core_hdl_root / "dau_identity_registers.sv"
    identity_axil_source = dau_core_hdl_root / "dau_identity_axil.v"
    legacy_identity_axil_source = dau_core_hdl_root / "dau_identity_axil.sv"
    return f"""# Generated by dau-build; source before scripts/build.tcl.
set dau_identity_registers_sv [file normalize \"{identity_source.as_posix()}\"]
set dau_identity_axil_v [file normalize "{identity_axil_source.as_posix()}"]
set dau_identity_axil_sv_legacy [file normalize "{legacy_identity_axil_source.as_posix()}"]
if {{[llength [get_projects -quiet]] == 0}} {{
    if {{![file exists \"project.xpr\"]}} {{
        error \"project.xpr is missing; restore the NiteFury project before this overlay\"
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
set manifest_file [open \"{manifest_path.as_posix()}\" w]
puts $manifest_file \"dau_identity_registers_sv=$dau_identity_registers_sv\"
puts $manifest_file "dau_identity_axil_v=$dau_identity_axil_v"
puts $manifest_file "dau_identity_axil_cell=dau_identity_axil_0"
puts $manifest_file "dau_identity_ooc_runs=$dau_identity_ooc_runs"
puts $manifest_file "spi_ss_i_tieoff=dau_spi_ss_i_tieoff"
puts $manifest_file "register_window_offset=0x00001000"
puts $manifest_file "overlay={overlay_tcl.as_posix()}"
puts $manifest_file "bitstream={bitstream_path.as_posix()}"
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


def project_build_script(*, nite_root: Path, project_tcl: Path, vivado_settings: Path, vivado_executable: str) -> str:
    return " && ".join(
        (
            f"cd {shlex.quote(str(nite_root))}",
            f". {shlex.quote(str(vivado_settings))}",
            shlex.join((vivado_executable, "-mode", "batch", "-source", str(project_tcl))),
        )
    )


def nitefury_build_tcl() -> str:
    return """# Generated by dau-build; source after scripts/dau_overlay.tcl.
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
write_cfgmem -format mcs -size 16 -interface SPIx4 -force -loadbit "up 0 ./project.runs/impl_1/Top_wrapper.bit" -file "./mcs/top.mcs"
write_cfgmem -format bin -size 16 -interface SPIx4 -force -loadbit "up 0 ./project.runs/impl_1/Top_wrapper.bit" -file "./mcs/top.bin"
write_verilog -mode funcsim Top.v
close_design
puts "Implementation done!"
"""


def overlay_build_script(
    *, nite_root: Path, overlay_tcl: Path, build_tcl: Path = Path("scripts/dau_build.tcl"), vivado_settings: Path, vivado_executable: str
) -> str:
    return " && ".join(
        (
            f"cd {shlex.quote(str(nite_root))}",
            f". {shlex.quote(str(vivado_settings))}",
            shlex.join((vivado_executable, "-mode", "batch", "-source", str(overlay_tcl))),
            shlex.join(("rm", "-f", "Top.v")),
            shlex.join((vivado_executable, "-mode", "batch", "-source", str(build_tcl))),
        )
    )


def overlay_command_plan_text(
    *, nite_root: Path, overlay_tcl: Path, build_tcl: Path = Path("scripts/dau_build.tcl"), vivado_settings: Path, vivado_executable: str
) -> str:
    return "\n".join(
        (
            "# Generated by dau-build; review before invoking Vivado.",
            f"vivado-overlay-build\t{overlay_build_script(nite_root=nite_root, overlay_tcl=overlay_tcl, build_tcl=build_tcl, vivado_settings=vivado_settings, vivado_executable=vivado_executable)}",
            "",
        )
    )


def flash_script(*, nite_root: Path, vivado_settings: Path, vivado_executable: str) -> str:
    return " && ".join(
        (
            f"cd {shlex.quote(str(nite_root))}",
            f". {shlex.quote(str(vivado_settings))}",
            shlex.join((vivado_executable, "-mode", "batch", "-source", "scripts/flash.tcl")),
        )
    )
