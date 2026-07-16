from __future__ import annotations

import base64
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from ccflow import BaseModel

from dau_build.vivado_backend import (
    VivadoBackendArtifactValidation,
    VivadoBackendRequest,
    VivadoOverlayDefinition,
    VivadoProjectArtifactValidation,
    VivadoProjectGenerationRequest,
    dau_overlay_tcl,
    flash_script as vivado_flash_script,
    generate_vivado_backend_artifacts,
    generate_vivado_project_generation_artifacts,
    overlay_build_script as vivado_overlay_build_script,
    source_only_vivado_driver_path,
    source_only_vivado_driver_tcl,
    validate_vivado_backend_artifact_bundle,
    validate_vivado_project_artifact_bundle,
    vivado_build_tcl,
)

SHELL_STAGE_EXCLUDES = (
    ".Xil",
    "project.cache",
    "project.gen",
    "project.hw",
    "project.runs",
    "*.jou",
    "*.log",
    "hs_err_pid*.log",
)


class ToolStep(BaseModel):
    name: str
    argv: tuple[str, ...]

    def __init__(self, name: str, argv: tuple[str, ...] = ()) -> None:
        super().__init__(name=name, argv=argv)

    @property
    def command_line(self) -> str:
        return shlex.join(self.argv)


class HardwareToolchainConfig(BaseModel):
    work_root: Path
    bitstream_path: Path | None = None
    vivado_executable: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    openfpgaloader_executable: str = "openFPGALoader"
    # the console script dau-utils actually installs (dau_utils.pci_runtime_pm)
    runtime_pm_executable: str = "dau-utils-pci-runtime-pm"
    # host access is board/host configuration, never code defaults: compose
    # it from a platform's host_access (for_platform) or set the fields
    # explicitly. Steps that need an unset fact fail with guidance; the
    # empty tuples are meaningful (global rescan only / no runtime-PM holds).
    runtime_pm_patterns: tuple[str, ...] = ()
    jtag_cable: str | None = None
    endpoint_bdf: str | None = None
    expected_endpoint_id: str | None = None
    rescan_bdfs: tuple[str, ...] = ()

    @classmethod
    def for_platform(cls, platform, *, work_root: Path, **overrides) -> "HardwareToolchainConfig":
        """Compose the toolchain config from a registered platform's
        ``host_access`` (board/host config, not code defaults). Explicit
        keyword overrides win; with neither, the host-access facts stay
        unset and any step that needs them fails with guidance."""
        access = getattr(platform, "host_access", None)
        values: dict = {}
        if access is not None:
            values = {
                "expected_endpoint_id": access.pci_id,
                "endpoint_bdf": access.endpoint_bdf,
                "jtag_cable": access.jtag_cable,
                "runtime_pm_executable": access.runtime_pm_executable,
                # authoritative including empty (global rescan only / no
                # runtime-PM holds) — never silently the dpv1 defaults
                "rescan_bdfs": access.rescan_bdfs,
                "runtime_pm_patterns": access.runtime_pm_patterns,
            }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(work_root=work_root, **values)

    @property
    def project_tcl(self) -> Path:
        return self.work_root / "project.tcl"

    @property
    def bitstream(self) -> Path:
        if self.bitstream_path is not None:
            if self.bitstream_path.is_absolute():
                return self.bitstream_path
            return self.work_root / self.bitstream_path
        return self.work_root / "project.runs" / "impl_1" / "Top_wrapper.bit"

    @property
    def lspci_slot(self) -> str:
        return self.required_host_access("endpoint_bdf").removeprefix("0000:")

    def required_host_access(self, field_name: str) -> str:
        """A host-access fact a plan step needs; unset means the caller
        composed no board/host configuration (dau-build carries none)."""
        value = getattr(self, field_name)
        if value is None:
            raise ValueError(
                f"{field_name} is unset: hardware access is board/host configuration — "
                f"compose platform=platforms/<vendor>/<board> with host_access (or set {field_name}=...)"
            )
        return value


# resolve forward-ref annotations (this module uses `from __future__ import
# annotations`, so pydantic needs the models rebuilt against module globals)
ToolStep.model_rebuild()
HardwareToolchainConfig.model_rebuild()


def build_and_program_plan(config: HardwareToolchainConfig) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config),
        vivado_build_step(config),
        jtag_detect_step(config),
        program_volatile_step(config),
        pci_rescan_step(config.rescan_bdfs),
        lspci_endpoint_step(config),
    )


def recovery_plan(config: HardwareToolchainConfig) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(config.rescan_bdfs),
        lspci_endpoint_step(config),
    )


def local_build_and_program_plan(
    config: HardwareToolchainConfig,
    *,
    dau_core_root: Path,
    source_shell_root: Path | None = None,
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    dau_utils_root: Path | None = None,
    smoke_command: str | None = None,
    python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
    overlay_definition: VivadoOverlayDefinition | None = None,
) -> tuple[ToolStep, ...]:
    overlay_path = _work_path(config.work_root, overlay_tcl)
    build_tcl = Path("scripts/dau_build.tcl")
    build_tcl_path = _work_path(config.work_root, build_tcl)
    vivado_path_base = config.work_root.resolve(strict=False) if config.vivado_mount_root is not None else None
    overlay_source = dau_overlay_tcl(dau_core_root / "dau_core" / "hdl", vivado_path_base=vivado_path_base, overlay_definition=overlay_definition)
    build_tcl_source = vivado_build_tcl(lane_placements=None if overlay_definition is None else overlay_definition.lane_placements)
    stage_steps = () if source_shell_root is None else stage_shell_plan(config, source_shell_root=source_shell_root)
    return (
        *stage_steps,
        thunderbolt_hold_step(config, dau_utils_root=dau_utils_root, python=python),
        write_dau_overlay_step(overlay_path=overlay_path, source=overlay_source),
        write_vivado_build_script_step(build_tcl_path=build_tcl_path, source=build_tcl_source),
        *_local_vivado_driver_steps(config, overlay_tcl=overlay_tcl, build_tcl=build_tcl),
        vivado_overlay_build_step(config, overlay_tcl=overlay_tcl, build_tcl=build_tcl, vivado_settings=vivado_settings),
        jtag_detect_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(config.rescan_bdfs),
        lspci_endpoint_step(config),
        *_smoke_steps(smoke_command),
        thunderbolt_release_step(config, dau_utils_root=dau_utils_root, python=python),
    )


def stage_vivado_overlay_plan(
    config: HardwareToolchainConfig,
    *,
    dau_core_root: Path,
    source_shell_root: Path | None = None,
    dau_artifact_bundle: Path | None = None,
    artifact_stem: str = "dau-vivado",
    platform: str = "vivado-xdma",
    shell: str = "xdma-shell",
    operator_set: tuple[str, ...] = ("identity",),
    register_map_version: str = "0.1",
    stream_protocol_version: str = "0.1",
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    manifest_path: Path | None = None,
    command_plan_path: Path | None = None,
    resource_summary_path: Path = Path("reports/dau_utilization.rpt"),
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt"),
    vivado_log_path: Path = Path("vivado.log"),
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
    overlay_definition: VivadoOverlayDefinition | None = None,
) -> tuple[ToolStep, ...]:
    artifacts = generate_vivado_backend_artifacts(
        VivadoBackendRequest(
            dau_core_hdl_root=dau_core_root / "dau_core" / "hdl",
            build_root=config.work_root,
            dau_artifact_bundle_path=dau_artifact_bundle,
            artifact_stem=artifact_stem,
            platform=platform,
            shell=shell,
            operator_set=operator_set,
            register_map_version=register_map_version,
            stream_protocol_version=stream_protocol_version,
            overlay_tcl=overlay_tcl,
            manifest_path=manifest_path,
            command_plan_path=command_plan_path,
            bitstream_path=config.bitstream_path or Path("project.runs/impl_1/Top_wrapper.bit"),
            resource_summary_path=resource_summary_path,
            timing_summary_path=timing_summary_path,
            vivado_log_path=vivado_log_path,
            vivado_settings=vivado_settings,
            vivado_executable=config.vivado_executable,
            vivado_invocation=config.vivado_invocation,
            vivado_mount_root=config.vivado_mount_root,
            overlay_definition=overlay_definition,
        )
    )
    stage_steps = () if source_shell_root is None else stage_shell_plan(config, source_shell_root=source_shell_root)
    return (
        *stage_steps,
        write_dau_overlay_step(overlay_path=artifacts.overlay_tcl_path, source=artifacts.overlay_tcl_text),
        write_dau_manifest_step(manifest_path=artifacts.manifest_path, source=artifacts.manifest_text),
        write_vivado_build_script_step(build_tcl_path=artifacts.build_tcl_path, source=artifacts.build_tcl_text),
        *_backend_vivado_driver_steps(artifacts),
        write_vivado_command_plan_step(command_plan_path=artifacts.command_plan_path, source=artifacts.command_plan_text),
    )


def stage_vivado_project_plan(
    config: HardwareToolchainConfig,
    *,
    source_shell_root: Path,
    dau_core_root: Path,
    dau_driver_root: Path,
    dau_utils_root: Path | None = None,
    dau_artifact_bundle: Path | None = None,
    artifact_stem: str = "dau-vivado",
    platform: str = "vivado-xdma",
    shell: str = "xdma-shell",
    operator_set: tuple[str, ...] = ("identity",),
    register_map_version: str = "0.1",
    stream_protocol_version: str = "0.1",
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    manifest_path: Path | None = None,
    command_plan_path: Path | None = None,
    project_manifest_path: Path | None = None,
    resource_summary_path: Path = Path("reports/dau_utilization.rpt"),
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt"),
    vivado_log_path: Path = Path("vivado.log"),
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
    overlay_definition: VivadoOverlayDefinition | None = None,
    stage_task_name: str | None = None,
) -> tuple[ToolStep, ...]:
    artifacts = generate_vivado_project_generation_artifacts(
        VivadoProjectGenerationRequest(
            source_shell_root=source_shell_root,
            work_root=config.work_root,
            dau_core_root=dau_core_root,
            dau_driver_root=dau_driver_root,
            dau_utils_root=dau_utils_root,
            dau_artifact_bundle_path=dau_artifact_bundle,
            artifact_stem=artifact_stem,
            platform=platform,
            shell=shell,
            operator_set=operator_set,
            register_map_version=register_map_version,
            stream_protocol_version=stream_protocol_version,
            overlay_tcl=overlay_tcl,
            manifest_path=manifest_path,
            command_plan_path=command_plan_path,
            project_manifest_path=project_manifest_path,
            bitstream_path=config.bitstream_path or Path("project.runs/impl_1/Top_wrapper.bit"),
            resource_summary_path=resource_summary_path,
            timing_summary_path=timing_summary_path,
            vivado_log_path=vivado_log_path,
            vivado_settings=vivado_settings,
            vivado_executable=config.vivado_executable,
            vivado_invocation=config.vivado_invocation,
            vivado_mount_root=config.vivado_mount_root,
            overlay_definition=overlay_definition,
            **({} if stage_task_name is None else {"stage_task_name": stage_task_name}),
        )
    )
    backend_artifacts = artifacts.backend_artifacts
    return (
        *stage_shell_plan(config, source_shell_root=source_shell_root),
        write_vivado_project_manifest_step(manifest_path=artifacts.project_manifest_path, source=artifacts.project_manifest_text),
        write_dau_overlay_step(overlay_path=backend_artifacts.overlay_tcl_path, source=backend_artifacts.overlay_tcl_text),
        write_dau_manifest_step(manifest_path=backend_artifacts.manifest_path, source=backend_artifacts.manifest_text),
        write_vivado_build_script_step(build_tcl_path=backend_artifacts.build_tcl_path, source=backend_artifacts.build_tcl_text),
        *_backend_vivado_driver_steps(backend_artifacts),
        write_vivado_command_plan_step(command_plan_path=backend_artifacts.command_plan_path, source=backend_artifacts.command_plan_text),
    )


def stage_shell_plan(
    config: HardwareToolchainConfig,
    *,
    source_shell_root: Path,
) -> tuple[ToolStep, ...]:
    return (stage_shell_step(source_shell_root=source_shell_root, work_root=config.work_root),)


def validate_bitstream_plan(
    config: HardwareToolchainConfig,
    *,
    smoke_command: str | None = None,
    dau_utils_root: Path | None = None,
    python: str = "python3",
) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config, dau_utils_root=dau_utils_root, python=python),
        jtag_detect_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(config.rescan_bdfs),
        lspci_endpoint_step(config),
        *_smoke_steps(smoke_command),
        thunderbolt_release_step(config, dau_utils_root=dau_utils_root, python=python),
    )


def validate_vivado_artifacts(
    config: HardwareToolchainConfig,
    *,
    manifest_path: Path = Path("dau-vivado.manifest"),
    command_plan_path: Path = Path("dau-vivado.plan"),
    project_manifest_path: Path | None = None,
) -> VivadoBackendArtifactValidation | VivadoProjectArtifactValidation:
    if project_manifest_path is not None:
        return validate_vivado_project_artifact_bundle(
            config.work_root,
            project_manifest_path=project_manifest_path,
            manifest_path=manifest_path,
            command_plan_path=command_plan_path,
        )
    return validate_vivado_backend_artifact_bundle(
        config.work_root,
        manifest_path=manifest_path,
        command_plan_path=command_plan_path,
    )


def flash_plan(
    config: HardwareToolchainConfig,
    *,
    dau_utils_root: Path | None = None,
    python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config, dau_utils_root=dau_utils_root, python=python),
        flash_step(config, vivado_settings=vivado_settings),
        thunderbolt_release_step(config, dau_utils_root=dau_utils_root, python=python),
    )


def thunderbolt_hold_plan(config: HardwareToolchainConfig) -> tuple[ToolStep, ...]:
    return (thunderbolt_hold_step(config),)


def thunderbolt_release_plan(config: HardwareToolchainConfig) -> tuple[ToolStep, ...]:
    return (thunderbolt_release_step(config),)


def vivado_build_step(config: HardwareToolchainConfig) -> ToolStep:
    return ToolStep("vivado-build", (config.vivado_executable, "-mode", "batch", "-source", str(config.project_tcl)))


def stage_shell_step(*, source_shell_root: Path, work_root: Path) -> ToolStep:
    return ToolStep("stage-shell", ("sh", "-c", _stage_shell_script(source_shell_root=source_shell_root, work_root=work_root)))


def write_dau_overlay_step(*, overlay_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-dau-overlay", overlay_path, source)


def write_dau_manifest_step(*, manifest_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-dau-manifest", manifest_path, source)


def write_vivado_command_plan_step(*, command_plan_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-vivado-command-plan", command_plan_path, source)


def write_vivado_project_manifest_step(*, manifest_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-vivado-project-manifest", manifest_path, source)


def write_vivado_build_script_step(*, build_tcl_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-vivado-build-script", build_tcl_path, source)


def write_vivado_driver_script_step(*, driver_tcl_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-vivado-driver-script", driver_tcl_path, source)


def _backend_vivado_driver_steps(artifacts) -> tuple[ToolStep, ...]:
    steps: list[ToolStep] = []
    if artifacts.overlay_driver_tcl_path is not None and artifacts.overlay_driver_tcl_text is not None:
        steps.append(write_vivado_driver_script_step(driver_tcl_path=artifacts.overlay_driver_tcl_path, source=artifacts.overlay_driver_tcl_text))
    if artifacts.build_driver_tcl_path is not None and artifacts.build_driver_tcl_text is not None:
        steps.append(write_vivado_driver_script_step(driver_tcl_path=artifacts.build_driver_tcl_path, source=artifacts.build_driver_tcl_text))
    return tuple(steps)


def _local_vivado_driver_steps(config: HardwareToolchainConfig, *, overlay_tcl: Path, build_tcl: Path) -> tuple[ToolStep, ...]:
    if config.vivado_mount_root is None:
        return ()
    overlay_driver_tcl = source_only_vivado_driver_path(overlay_tcl)
    build_driver_tcl = source_only_vivado_driver_path(build_tcl)
    return (
        write_vivado_driver_script_step(
            driver_tcl_path=_work_path(config.work_root, overlay_driver_tcl),
            source=source_only_vivado_driver_tcl(work_root=config.work_root, vivado_mount_root=config.vivado_mount_root, tcl_path=overlay_tcl),
        ),
        write_vivado_driver_script_step(
            driver_tcl_path=_work_path(config.work_root, build_driver_tcl),
            source=source_only_vivado_driver_tcl(work_root=config.work_root, vivado_mount_root=config.vivado_mount_root, tcl_path=build_tcl),
        ),
    )


def _write_text_step(name: str, path: Path, source: str) -> ToolStep:
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = f"mkdir -p {shlex.quote(str(path.parent))} && printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(str(path))}"
    return ToolStep(name, ("sh", "-c", script))


def vivado_overlay_build_step(
    config: HardwareToolchainConfig,
    *,
    overlay_tcl: Path,
    build_tcl: Path = Path("scripts/dau_build.tcl"),
    vivado_settings: Path,
) -> ToolStep:
    script = vivado_overlay_build_script(
        work_root=config.work_root,
        overlay_tcl=overlay_tcl,
        build_tcl=build_tcl,
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
        vivado_invocation=config.vivado_invocation,
        vivado_mount_root=config.vivado_mount_root,
    )
    return ToolStep("vivado-overlay-build", ("bash", "-lc", script))


def validate_vivado_artifacts_step(
    config: HardwareToolchainConfig,
    *,
    manifest_path: Path,
    command_plan_path: Path,
    project_manifest_path: Path | None = None,
    executable: str | Sequence[str] = "dau-build",
) -> ToolStep:
    command_prefix = [executable] if isinstance(executable, str) else list(executable)
    argv = [
        *command_prefix,
        "task=tasks/validate/validate-vivado-artifacts",
        f"work_root={config.work_root}",
        f"manifest_path={manifest_path}",
        f"command_plan_path={command_plan_path}",
    ]
    if project_manifest_path is not None:
        argv.append(f"project_manifest_path={project_manifest_path}")
    return ToolStep("validate-vivado-artifacts", tuple(argv))


def hardware_smoke_step(smoke_command: str) -> ToolStep:
    """A post-programming smoke check supplied by the caller. dau-build is
    public and never imports the private DAU packages, so it carries no smoke
    payload of its own — inject one (e.g. from a private config overlay) via
    the plan's ``smoke_command`` field."""
    return ToolStep("driver-hardware-smoke", ("sh", "-c", smoke_command))


def _smoke_steps(smoke_command: str | None) -> tuple[ToolStep, ...]:
    return () if smoke_command is None else (hardware_smoke_step(smoke_command),)


def flash_step(config: HardwareToolchainConfig, *, vivado_settings: Path) -> ToolStep:
    script = vivado_flash_script(
        work_root=config.work_root,
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
        vivado_invocation=config.vivado_invocation,
    )
    return ToolStep("flash", ("bash", "-lc", script))


def thunderbolt_hold_step(config: HardwareToolchainConfig, *, dau_utils_root: Path | None = None, python: str = "python3") -> ToolStep:
    return ToolStep(
        "thunderbolt-hold",
        _runtime_pm_argv(config, "hold")
        if dau_utils_root is None
        else ("sh", "-c", _local_runtime_pm_script(config, "hold", dau_utils_root, python)),
    )


def thunderbolt_release_step(config: HardwareToolchainConfig, *, dau_utils_root: Path | None = None, python: str = "python3") -> ToolStep:
    return ToolStep(
        "thunderbolt-release",
        _runtime_pm_argv(config, "release")
        if dau_utils_root is None
        else ("sh", "-c", _local_runtime_pm_script(config, "release", dau_utils_root, python)),
    )


def jtag_detect_step(config: HardwareToolchainConfig) -> ToolStep:
    return ToolStep("jtag-detect", (config.openfpgaloader_executable, "-c", config.required_host_access("jtag_cable"), "--detect"))


def program_volatile_step(config: HardwareToolchainConfig) -> ToolStep:
    return ToolStep("program-volatile", (config.openfpgaloader_executable, "-c", config.required_host_access("jtag_cable"), str(config.bitstream)))


def remove_endpoint_step(config: HardwareToolchainConfig) -> ToolStep:
    remove_path = f"/sys/bus/pci/devices/{config.required_host_access('endpoint_bdf')}/remove"
    return ToolStep("remove-endpoint", ("sh", "-c", f"test ! -e {remove_path} || echo 1 > {remove_path}"))


def pci_global_rescan_step() -> ToolStep:
    return ToolStep("pci-global-rescan", ("sh", "-c", "echo 1 > /sys/bus/pci/rescan"))


def pci_rescan_step(bridge_bdfs: Sequence[str] = ()) -> ToolStep:
    return ToolStep("pci-rescan", ("sh", "-c", _pci_rescan_script(bridge_bdfs)))


def lspci_endpoint_step(config: HardwareToolchainConfig) -> ToolStep:
    return ToolStep("lspci-endpoint", ("sh", "-c", _lspci_endpoint_script(config)))


def _runtime_pm_argv(config: HardwareToolchainConfig, mode: str) -> tuple[str, ...]:
    argv = [config.runtime_pm_executable, mode]
    for pattern in config.runtime_pm_patterns:
        argv.extend(("--pattern", pattern))
    return tuple(argv)


def _lspci_endpoint_script(config: HardwareToolchainConfig, bridge_bdfs: Sequence[str] | None = None) -> str:
    if bridge_bdfs is None:
        bridge_bdfs = config.rescan_bdfs
    expected_id = shlex.quote(config.required_host_access("expected_endpoint_id").lower())
    expected_slot = shlex.quote(config.lspci_slot)
    retry_rescan = _pci_rescan_script(bridge_bdfs)
    return " ".join(
        (
            f"endpoint_output=$(lspci -Dnn -d {expected_id} || true);",
            'if test -n "$endpoint_output";',
            "then printf '%s\\n' \"$endpoint_output\"; exit 0; fi;",
            "for attempt in 1 2 3; do",
            retry_rescan + ";",
            f"endpoint_output=$(lspci -Dnn -d {expected_id} || true);",
            'if test -n "$endpoint_output";',
            "then printf '%s\\n' \"$endpoint_output\"; exit 0; fi;",
            "done;",
            f"echo expected PCI endpoint {expected_id} after rescan, but none was found >&2;",
            f"echo expected slot: {expected_slot} >&2;",
            "lspci -tv >&2 || true;",
            "dmesg -T 2>/dev/null | tail -80 | grep -Ei 'pci|pcie|xdma|10ee|xilinx|04:00|03:01' >&2 || true;",
            "exit 1",
        )
    )


def _pci_rescan_script(bridge_bdfs: Sequence[str]) -> str:
    bridge_list = " ".join(shlex.quote(bdf) for bdf in bridge_bdfs)
    if not bridge_list:
        return "echo 1 > /sys/bus/pci/rescan"
    return (
        f"for bdf in {bridge_list}; "
        "do test ! -w /sys/bus/pci/devices/$bdf/rescan || "
        "echo 1 > /sys/bus/pci/devices/$bdf/rescan; "
        "done && echo 1 > /sys/bus/pci/rescan"
    )


def _local_runtime_pm_script(config: HardwareToolchainConfig, mode: str, dau_utils_root: Path, python: str) -> str:
    argv = [python, "-c", "from dau_utils.pci_runtime_pm import main; raise SystemExit(main())", mode]
    for pattern in config.runtime_pm_patterns:
        argv.extend(("--pattern", pattern))
    return f"PYTHONPATH={shlex.quote(str(dau_utils_root))} {shlex.join(argv)}"


def _work_path(work_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return work_root / path


def _directory_argument(path: Path) -> str:
    return str(path).rstrip("/") + "/"


def _stage_shell_script(*, source_shell_root: Path, work_root: Path) -> str:
    argv = ["rsync", "-a", "--delete", "--delete-excluded"]
    for pattern in SHELL_STAGE_EXCLUDES:
        argv.extend(("--exclude", pattern))
    argv.extend((_directory_argument(source_shell_root), _directory_argument(work_root)))
    return f"mkdir -p {shlex.quote(str(work_root.parent))} && {shlex.join(argv)}"


def _execute_steps(steps: Sequence[ToolStep]) -> int:
    return execute_plan_steps(steps)


def format_plan_steps(steps: Sequence[ToolStep]) -> str:
    return "\n".join(f"{step.name}\t{step.command_line}" for step in steps)


def execute_plan_steps(steps: Sequence[ToolStep]) -> int:
    for index, step in enumerate(steps):
        print(f"+ {step.command_line}", flush=True)
        result = subprocess.run(step.argv)
        if result.returncode != 0:
            for cleanup_step in steps[index + 1 :]:
                if cleanup_step.name.endswith("release"):
                    print(f"+ {cleanup_step.command_line}", flush=True)
                    subprocess.run(cleanup_step.argv)
            return result.returncode
    return 0


class HardwarePlan(BaseModel):
    """A hardware-session plan selected from the ``plan`` config group. Each is
    a polymorphic model owning its required fields; ``HardwarePlanTask``
    delegates to ``compose`` — there is no plan ``Literal`` or dict-of-lambdas
    dispatch."""

    name: str

    def compose(self, config: "HardwareToolchainConfig") -> tuple[ToolStep, ...]:
        raise NotImplementedError


class BuildAndProgramPlan(HardwarePlan):
    name: str = "build-and-program"

    def compose(self, config):
        return build_and_program_plan(config)


class RecoveryPlan(HardwarePlan):
    name: str = "recovery"

    def compose(self, config):
        return recovery_plan(config)


class ThunderboltHoldPlan(HardwarePlan):
    name: str = "thunderbolt-hold"

    def compose(self, config):
        return thunderbolt_hold_plan(config)


class ThunderboltReleasePlan(HardwarePlan):
    name: str = "thunderbolt-release"

    def compose(self, config):
        return thunderbolt_release_plan(config)


class FlashPlan(HardwarePlan):
    name: str = "flash"
    dau_utils_root: Path | None = None
    python: str = "python3"
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")

    def compose(self, config):
        return flash_plan(config, dau_utils_root=self.dau_utils_root, python=self.python, vivado_settings=self.vivado_settings)


class ValidateBitstreamPlan(HardwarePlan):
    name: str = "validate-bitstream"
    smoke_command: str | None = None
    dau_utils_root: Path | None = None
    python: str = "python3"

    def compose(self, config):
        return validate_bitstream_plan(
            config,
            smoke_command=self.smoke_command,
            dau_utils_root=self.dau_utils_root,
            python=self.python,
        )


class LocalBuildAndProgramPlan(HardwarePlan):
    name: str = "local-build-and-program"
    # sourced from the composed host group (host=hosts/<name>) by the plan
    # config; a direct plan.dau_core_root=... override wins
    dau_core_root: Path | None = None
    source_shell_root: Path | None = None
    dau_utils_root: Path | None = None
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    smoke_command: str | None = None
    python: str = "python3"
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    overlay_definition: VivadoOverlayDefinition | None = None

    def compose(self, config):
        if self.dau_core_root is None:
            raise ValueError("dau_core_root is required: select host=hosts/<name> (a host config group entry) or set plan.dau_core_root=...")
        return local_build_and_program_plan(
            config,
            dau_core_root=self.dau_core_root,
            source_shell_root=self.source_shell_root,
            dau_utils_root=self.dau_utils_root,
            overlay_tcl=self.overlay_tcl,
            smoke_command=self.smoke_command,
            python=self.python,
            vivado_settings=self.vivado_settings,
            overlay_definition=self.overlay_definition,
        )


for _plan_cls in (
    HardwarePlan,
    BuildAndProgramPlan,
    RecoveryPlan,
    ThunderboltHoldPlan,
    ThunderboltReleasePlan,
    FlashPlan,
    ValidateBitstreamPlan,
    LocalBuildAndProgramPlan,
):
    _plan_cls.model_rebuild()
