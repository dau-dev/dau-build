from __future__ import annotations

import argparse
import base64
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dau_build.vivado_backend import (
    NiteFuryBackendArtifactValidation,
    NiteFuryBackendRequest,
    NiteFuryProjectArtifactValidation,
    NiteFuryProjectGenerationRequest,
    dau_overlay_tcl,
    flash_script as vivado_flash_script,
    generate_nitefury_backend_artifacts,
    generate_nitefury_project_generation_artifacts,
    nitefury_build_tcl,
    overlay_build_script as vivado_overlay_build_script,
    project_build_script as vivado_project_build_script,
    validate_nitefury_backend_artifact_bundle,
    validate_nitefury_project_artifact_bundle,
)

PCI_RESCAN_BDFS = (
    "0000:03:01.0",
    "0000:02:00.0",
    "0000:00:0d.3",
    "0000:00:0d.2",
    "0000:00:0d.0",
    "0000:00:07.2",
    "0000:00:07.0",
)

NITEFURY_SHELL_STAGE_EXCLUDES = (
    ".Xil",
    "project.cache",
    "project.gen",
    "project.hw",
    "project.runs",
    "*.jou",
    "*.log",
    "hs_err_pid*.log",
)


@dataclass(frozen=True)
class ToolStep:
    name: str
    argv: tuple[str, ...]

    @property
    def command_line(self) -> str:
        return shlex.join(self.argv)


@dataclass(frozen=True)
class NiteFuryToolchainConfig:
    nite_root: Path
    bitstream_path: Path | None = None
    vivado_executable: str = "vivado"
    openfpgaloader_executable: str = "openFPGALoader"
    runtime_pm_executable: str = "dau-pci-runtime-pm"
    runtime_pm_patterns: tuple[str, ...] = ("Thunderbolt", "JHL", "10ee:7011", "Xilinx")
    jtag_cable: str = "digilent_hs2"
    endpoint_bdf: str = "0000:04:00.0"
    expected_endpoint_id: str = "10ee:7011"

    @property
    def project_tcl(self) -> Path:
        return self.nite_root / "project.tcl"

    @property
    def bitstream(self) -> Path:
        if self.bitstream_path is not None:
            if self.bitstream_path.is_absolute():
                return self.bitstream_path
            return self.nite_root / self.bitstream_path
        return self.nite_root / "project.runs" / "impl_1" / "Top_wrapper.bit"

    @property
    def lspci_slot(self) -> str:
        return self.endpoint_bdf.removeprefix("0000:")


def build_and_program_plan(config: NiteFuryToolchainConfig) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config),
        vivado_build_step(config),
        jtag_detect_step(config),
        program_volatile_step(config),
        pci_rescan_step(),
        lspci_endpoint_step(config),
    )


def recovery_plan(config: NiteFuryToolchainConfig) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(),
        lspci_endpoint_step(config),
    )


def remote_build_plan(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_nite_root: Path,
    remote_source_nite_root: Path | None = None,
    remote_dau_utils_root: Path | None = None,
    remote_python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    stage_steps = (
        ()
        if remote_source_nite_root is None
        else (
            remote_stage_nitefury_shell_step(
                remote_host=remote_host, remote_source_nite_root=remote_source_nite_root, remote_nite_root=remote_nite_root
            ),
        )
    )
    return (
        *stage_steps,
        remote_thunderbolt_hold_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
        remote_vivado_build_step(config, remote_host=remote_host, remote_nite_root=remote_nite_root, vivado_settings=vivado_settings),
        remote_thunderbolt_release_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
    )


def remote_build_and_program_plan(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_nite_root: Path,
    remote_dau_core_root: Path,
    remote_dau_driver_root: Path,
    remote_source_nite_root: Path | None = None,
    remote_overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    remote_dau_utils_root: Path | None = None,
    remote_python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    remote_overlay_path = _remote_nite_path(remote_nite_root, remote_overlay_tcl)
    remote_build_tcl = Path("scripts/dau_build.tcl")
    remote_build_tcl_path = _remote_nite_path(remote_nite_root, remote_build_tcl)
    overlay_source = dau_overlay_tcl(remote_dau_core_root / "dau_core" / "hdl")
    stage_steps = (
        ()
        if remote_source_nite_root is None
        else (
            remote_stage_nitefury_shell_step(
                remote_host=remote_host, remote_source_nite_root=remote_source_nite_root, remote_nite_root=remote_nite_root
            ),
        )
    )
    return (
        *stage_steps,
        remote_thunderbolt_hold_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
        remote_write_dau_overlay_step(remote_host=remote_host, remote_overlay_path=remote_overlay_path, source=overlay_source),
        remote_write_vivado_build_script_step(remote_host=remote_host, remote_build_tcl_path=remote_build_tcl_path, source=nitefury_build_tcl()),
        remote_vivado_overlay_build_step(
            config,
            remote_host=remote_host,
            remote_nite_root=remote_nite_root,
            remote_overlay_tcl=remote_overlay_tcl,
            remote_build_tcl=remote_build_tcl,
            vivado_settings=vivado_settings,
        ),
        remote_jtag_detect_step(config, remote_host=remote_host),
        remote_remove_endpoint_step(config, remote_host=remote_host),
        remote_program_volatile_step(config, remote_host=remote_host, remote_nite_root=remote_nite_root),
        remote_pci_rescan_step(remote_host=remote_host),
        remote_lspci_endpoint_step(config, remote_host=remote_host),
        remote_driver_hardware_smoke_step(
            remote_host=remote_host,
            remote_dau_core_root=remote_dau_core_root,
            remote_dau_driver_root=remote_dau_driver_root,
            remote_python=remote_python,
        ),
        remote_thunderbolt_release_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
    )


def local_build_and_program_plan(
    config: NiteFuryToolchainConfig,
    *,
    dau_core_root: Path,
    dau_driver_root: Path,
    source_nite_root: Path | None = None,
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    dau_utils_root: Path | None = None,
    python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    overlay_path = _nite_path(config.nite_root, overlay_tcl)
    build_tcl = Path("scripts/dau_build.tcl")
    build_tcl_path = _nite_path(config.nite_root, build_tcl)
    overlay_source = dau_overlay_tcl(dau_core_root / "dau_core" / "hdl")
    stage_steps = () if source_nite_root is None else stage_nitefury_shell_plan(config, source_nite_root=source_nite_root)
    return (
        *stage_steps,
        thunderbolt_hold_step(config, dau_utils_root=dau_utils_root, python=python),
        write_dau_overlay_step(overlay_path=overlay_path, source=overlay_source),
        write_vivado_build_script_step(build_tcl_path=build_tcl_path, source=nitefury_build_tcl()),
        vivado_overlay_build_step(config, overlay_tcl=overlay_tcl, build_tcl=build_tcl, vivado_settings=vivado_settings),
        jtag_detect_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(),
        lspci_endpoint_step(config),
        driver_hardware_smoke_step(dau_core_root=dau_core_root, dau_driver_root=dau_driver_root, python=python),
        thunderbolt_release_step(config, dau_utils_root=dau_utils_root, python=python),
    )


def stage_vivado_overlay_plan(
    config: NiteFuryToolchainConfig,
    *,
    dau_core_root: Path,
    source_nite_root: Path | None = None,
    artifact_stem: str = "dau-nitefury",
    platform: str = "nitefury",
    shell: str = "nitefury-xdma",
    operator_set: tuple[str, ...] = ("identity",),
    register_map_version: str = "0.1",
    stream_protocol_version: str = "0.1",
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    manifest_path: Path | None = None,
    command_plan_path: Path | None = None,
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    artifacts = generate_nitefury_backend_artifacts(
        NiteFuryBackendRequest(
            dau_core_hdl_root=dau_core_root / "dau_core" / "hdl",
            build_root=config.nite_root,
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
            vivado_settings=vivado_settings,
            vivado_executable=config.vivado_executable,
        )
    )
    stage_steps = () if source_nite_root is None else stage_nitefury_shell_plan(config, source_nite_root=source_nite_root)
    return (
        *stage_steps,
        write_dau_overlay_step(overlay_path=artifacts.overlay_tcl_path, source=artifacts.overlay_tcl_text),
        write_dau_manifest_step(manifest_path=artifacts.manifest_path, source=artifacts.manifest_text),
        write_vivado_build_script_step(build_tcl_path=artifacts.build_tcl_path, source=artifacts.build_tcl_text),
        write_vivado_command_plan_step(command_plan_path=artifacts.command_plan_path, source=artifacts.command_plan_text),
    )


def stage_nitefury_project_plan(
    config: NiteFuryToolchainConfig,
    *,
    source_nite_root: Path,
    dau_core_root: Path,
    dau_driver_root: Path,
    dau_utils_root: Path | None = None,
    artifact_stem: str = "dau-nitefury",
    platform: str = "nitefury",
    shell: str = "nitefury-xdma",
    operator_set: tuple[str, ...] = ("identity",),
    register_map_version: str = "0.1",
    stream_protocol_version: str = "0.1",
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    manifest_path: Path | None = None,
    command_plan_path: Path | None = None,
    project_manifest_path: Path | None = None,
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    artifacts = generate_nitefury_project_generation_artifacts(
        NiteFuryProjectGenerationRequest(
            source_nite_root=source_nite_root,
            work_nite_root=config.nite_root,
            dau_core_root=dau_core_root,
            dau_driver_root=dau_driver_root,
            dau_utils_root=dau_utils_root,
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
            vivado_settings=vivado_settings,
            vivado_executable=config.vivado_executable,
        )
    )
    backend_artifacts = artifacts.backend_artifacts
    return (
        *stage_nitefury_shell_plan(config, source_nite_root=source_nite_root),
        write_nitefury_project_manifest_step(manifest_path=artifacts.project_manifest_path, source=artifacts.project_manifest_text),
        write_dau_overlay_step(overlay_path=backend_artifacts.overlay_tcl_path, source=backend_artifacts.overlay_tcl_text),
        write_dau_manifest_step(manifest_path=backend_artifacts.manifest_path, source=backend_artifacts.manifest_text),
        write_vivado_build_script_step(build_tcl_path=backend_artifacts.build_tcl_path, source=backend_artifacts.build_tcl_text),
        write_vivado_command_plan_step(command_plan_path=backend_artifacts.command_plan_path, source=backend_artifacts.command_plan_text),
    )


def stage_nitefury_shell_plan(
    config: NiteFuryToolchainConfig,
    *,
    source_nite_root: Path,
) -> tuple[ToolStep, ...]:
    return (stage_nitefury_shell_step(source_nite_root=source_nite_root, work_nite_root=config.nite_root),)


def validate_bitstream_plan(
    config: NiteFuryToolchainConfig,
    *,
    dau_core_root: Path,
    dau_driver_root: Path,
    dau_utils_root: Path | None = None,
    python: str = "python3",
) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config, dau_utils_root=dau_utils_root, python=python),
        jtag_detect_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(),
        lspci_endpoint_step(config),
        driver_hardware_smoke_step(dau_core_root=dau_core_root, dau_driver_root=dau_driver_root, python=python),
        thunderbolt_release_step(config, dau_utils_root=dau_utils_root, python=python),
    )


def validate_vivado_artifacts(
    config: NiteFuryToolchainConfig,
    *,
    manifest_path: Path = Path("dau-nitefury.manifest"),
    command_plan_path: Path = Path("dau-nitefury.plan"),
    project_manifest_path: Path | None = None,
) -> NiteFuryBackendArtifactValidation | NiteFuryProjectArtifactValidation:
    if project_manifest_path is not None:
        return validate_nitefury_project_artifact_bundle(
            config.nite_root,
            project_manifest_path=project_manifest_path,
            manifest_path=manifest_path,
            command_plan_path=command_plan_path,
        )
    return validate_nitefury_backend_artifact_bundle(
        config.nite_root,
        manifest_path=manifest_path,
        command_plan_path=command_plan_path,
    )


def remote_xdma_rebuild_plan(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_xdma_root: Path,
) -> tuple[ToolStep, ...]:
    return (remote_xdma_rebuild_step(config, remote_host=remote_host, remote_xdma_root=remote_xdma_root),)


def remote_xdma_load_plan(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_xdma_root: Path,
    remote_dau_utils_root: Path | None = None,
    remote_python: str = "python3",
) -> tuple[ToolStep, ...]:
    return (
        remote_thunderbolt_hold_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
        remote_xdma_unload_step(remote_host=remote_host),
        remote_xdma_load_step(config, remote_host=remote_host, remote_xdma_root=remote_xdma_root),
        remote_thunderbolt_release_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
    )


def remote_flash_plan(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_nite_root: Path,
    remote_dau_utils_root: Path | None = None,
    remote_python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    return (
        remote_thunderbolt_hold_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
        remote_flash_step(config, remote_host=remote_host, remote_nite_root=remote_nite_root, vivado_settings=vivado_settings),
        remote_thunderbolt_release_step(config, remote_host=remote_host, remote_dau_utils_root=remote_dau_utils_root, remote_python=remote_python),
    )


def flash_plan(
    config: NiteFuryToolchainConfig,
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


def thunderbolt_hold_plan(config: NiteFuryToolchainConfig) -> tuple[ToolStep, ...]:
    return (thunderbolt_hold_step(config),)


def thunderbolt_release_plan(config: NiteFuryToolchainConfig) -> tuple[ToolStep, ...]:
    return (thunderbolt_release_step(config),)


def vivado_build_step(config: NiteFuryToolchainConfig) -> ToolStep:
    return ToolStep("vivado-build", (config.vivado_executable, "-mode", "batch", "-source", str(config.project_tcl)))


def stage_nitefury_shell_step(*, source_nite_root: Path, work_nite_root: Path) -> ToolStep:
    return ToolStep(
        "stage-nitefury-shell", ("sh", "-c", _stage_nitefury_shell_script(source_nite_root=source_nite_root, work_nite_root=work_nite_root))
    )


def remote_stage_nitefury_shell_step(*, remote_host: str, remote_source_nite_root: Path, remote_nite_root: Path) -> ToolStep:
    script = _stage_nitefury_shell_script(source_nite_root=remote_source_nite_root, work_nite_root=remote_nite_root)
    return ToolStep("remote-stage-nitefury-shell", _ssh_argv(remote_host, script))


def write_dau_overlay_step(*, overlay_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-dau-overlay", overlay_path, source)


def write_dau_manifest_step(*, manifest_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-dau-manifest", manifest_path, source)


def write_vivado_command_plan_step(*, command_plan_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-vivado-command-plan", command_plan_path, source)


def write_nitefury_project_manifest_step(*, manifest_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-nitefury-project-manifest", manifest_path, source)


def write_vivado_build_script_step(*, build_tcl_path: Path, source: str) -> ToolStep:
    return _write_text_step("write-vivado-build-script", build_tcl_path, source)


def _write_text_step(name: str, path: Path, source: str) -> ToolStep:
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = f"mkdir -p {shlex.quote(str(path.parent))} && printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(str(path))}"
    return ToolStep(name, ("sh", "-c", script))


def vivado_overlay_build_step(
    config: NiteFuryToolchainConfig,
    *,
    overlay_tcl: Path,
    build_tcl: Path = Path("scripts/dau_build.tcl"),
    vivado_settings: Path,
) -> ToolStep:
    script = vivado_overlay_build_script(
        nite_root=config.nite_root,
        overlay_tcl=overlay_tcl,
        build_tcl=build_tcl,
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
    )
    return ToolStep("vivado-overlay-build", ("bash", "-lc", script))


def remote_thunderbolt_hold_step(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_dau_utils_root: Path | None = None,
    remote_python: str = "python3",
) -> ToolStep:
    return ToolStep(
        "remote-thunderbolt-hold", _ssh_argv(remote_host, _remote_runtime_pm_script(config, "hold", remote_dau_utils_root, remote_python))
    )


def remote_thunderbolt_release_step(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_dau_utils_root: Path | None = None,
    remote_python: str = "python3",
) -> ToolStep:
    return ToolStep(
        "remote-thunderbolt-release", _ssh_argv(remote_host, _remote_runtime_pm_script(config, "release", remote_dau_utils_root, remote_python))
    )


def remote_vivado_build_step(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_nite_root: Path,
    vivado_settings: Path,
) -> ToolStep:
    script = vivado_project_build_script(
        nite_root=remote_nite_root,
        project_tcl=Path("project.tcl"),
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
    )
    return ToolStep("remote-vivado-build", _ssh_argv(remote_host, script))


def remote_write_dau_overlay_step(*, remote_host: str, remote_overlay_path: Path, source: str) -> ToolStep:
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = f"mkdir -p {shlex.quote(str(remote_overlay_path.parent))} && printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(str(remote_overlay_path))}"
    return ToolStep("remote-write-dau-overlay", _ssh_argv(remote_host, script))


def remote_write_vivado_build_script_step(*, remote_host: str, remote_build_tcl_path: Path, source: str) -> ToolStep:
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = f"mkdir -p {shlex.quote(str(remote_build_tcl_path.parent))} && printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(str(remote_build_tcl_path))}"
    return ToolStep("remote-write-vivado-build-script", _ssh_argv(remote_host, script))


def remote_vivado_overlay_build_step(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_nite_root: Path,
    remote_overlay_tcl: Path,
    remote_build_tcl: Path = Path("scripts/dau_build.tcl"),
    vivado_settings: Path,
) -> ToolStep:
    script = vivado_overlay_build_script(
        nite_root=remote_nite_root,
        overlay_tcl=remote_overlay_tcl,
        build_tcl=remote_build_tcl,
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
    )
    return ToolStep("remote-vivado-build", _ssh_argv(remote_host, script))


def remote_jtag_detect_step(config: NiteFuryToolchainConfig, *, remote_host: str) -> ToolStep:
    return ToolStep("remote-jtag-detect", _ssh_argv(remote_host, shlex.join((config.openfpgaloader_executable, "-c", config.jtag_cable, "--detect"))))


def remote_remove_endpoint_step(config: NiteFuryToolchainConfig, *, remote_host: str) -> ToolStep:
    remove_path = f"/sys/bus/pci/devices/{config.endpoint_bdf}/remove"
    return ToolStep("remote-remove-endpoint", _ssh_argv(remote_host, f"test ! -e {remove_path} || echo 1 > {remove_path}"))


def remote_program_volatile_step(config: NiteFuryToolchainConfig, *, remote_host: str, remote_nite_root: Path) -> ToolStep:
    remote_bitstream = remote_nite_root / "project.runs" / "impl_1" / "Top_wrapper.bit"
    return ToolStep(
        "remote-program-volatile",
        _ssh_argv(remote_host, shlex.join((config.openfpgaloader_executable, "-c", config.jtag_cable, str(remote_bitstream)))),
    )


def remote_pci_global_rescan_step(*, remote_host: str) -> ToolStep:
    return ToolStep("remote-pci-global-rescan", _ssh_argv(remote_host, "echo 1 > /sys/bus/pci/rescan"))


def remote_pci_rescan_step(*, remote_host: str, bridge_bdfs: Sequence[str] = PCI_RESCAN_BDFS) -> ToolStep:
    return ToolStep("remote-pci-rescan", _ssh_argv(remote_host, _pci_rescan_script(bridge_bdfs)))


def remote_lspci_endpoint_step(config: NiteFuryToolchainConfig, *, remote_host: str) -> ToolStep:
    return ToolStep("remote-lspci-endpoint", _ssh_argv(remote_host, _lspci_endpoint_script(config)))


def remote_driver_hardware_smoke_step(
    *,
    remote_host: str,
    remote_dau_core_root: Path,
    remote_dau_driver_root: Path,
    remote_python: str,
) -> ToolStep:
    pythonpath = f"{remote_dau_core_root}:{remote_dau_driver_root}"
    smoke_code = _driver_hardware_smoke_code()
    script = f"PYTHONPATH={shlex.quote(pythonpath)} {shlex.join((remote_python, '-c', smoke_code))}"
    return ToolStep("remote-driver-hardware-smoke", _ssh_argv(remote_host, script))


def driver_hardware_smoke_step(*, dau_core_root: Path, dau_driver_root: Path, python: str) -> ToolStep:
    pythonpath = f"{dau_core_root}:{dau_driver_root}"
    script = f"PYTHONPATH={shlex.quote(pythonpath)} {shlex.join((python, '-c', _driver_hardware_smoke_code()))}"
    return ToolStep("driver-hardware-smoke", ("sh", "-c", script))


def remote_xdma_rebuild_step(config: NiteFuryToolchainConfig, *, remote_host: str, remote_xdma_root: Path) -> ToolStep:
    return ToolStep("remote-xdma-rebuild", _ssh_argv(remote_host, f"cd {shlex.quote(str(remote_xdma_root))} && make clean && make"))


def remote_xdma_unload_step(*, remote_host: str) -> ToolStep:
    return ToolStep("remote-xdma-unload", _ssh_argv(remote_host, "modprobe -r xdma || true"))


def remote_xdma_load_step(config: NiteFuryToolchainConfig, *, remote_host: str, remote_xdma_root: Path) -> ToolStep:
    return ToolStep("remote-xdma-load", _ssh_argv(remote_host, f"cd {shlex.quote(str(remote_xdma_root))} && insmod ./xdma.ko"))


def remote_flash_step(
    config: NiteFuryToolchainConfig,
    *,
    remote_host: str,
    remote_nite_root: Path,
    vivado_settings: Path,
) -> ToolStep:
    script = vivado_flash_script(
        nite_root=remote_nite_root,
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
    )
    return ToolStep("remote-flash", _ssh_argv(remote_host, script))


def flash_step(config: NiteFuryToolchainConfig, *, vivado_settings: Path) -> ToolStep:
    script = vivado_flash_script(
        nite_root=config.nite_root,
        vivado_settings=vivado_settings,
        vivado_executable=config.vivado_executable,
    )
    return ToolStep("flash", ("bash", "-lc", script))


def thunderbolt_hold_step(config: NiteFuryToolchainConfig, *, dau_utils_root: Path | None = None, python: str = "python3") -> ToolStep:
    return ToolStep(
        "thunderbolt-hold",
        _runtime_pm_argv(config, "hold")
        if dau_utils_root is None
        else ("sh", "-c", _local_runtime_pm_script(config, "hold", dau_utils_root, python)),
    )


def thunderbolt_release_step(config: NiteFuryToolchainConfig, *, dau_utils_root: Path | None = None, python: str = "python3") -> ToolStep:
    return ToolStep(
        "thunderbolt-release",
        _runtime_pm_argv(config, "release")
        if dau_utils_root is None
        else ("sh", "-c", _local_runtime_pm_script(config, "release", dau_utils_root, python)),
    )


def jtag_detect_step(config: NiteFuryToolchainConfig) -> ToolStep:
    return ToolStep("jtag-detect", (config.openfpgaloader_executable, "-c", config.jtag_cable, "--detect"))


def program_volatile_step(config: NiteFuryToolchainConfig) -> ToolStep:
    return ToolStep("program-volatile", (config.openfpgaloader_executable, "-c", config.jtag_cable, str(config.bitstream)))


def remove_endpoint_step(config: NiteFuryToolchainConfig) -> ToolStep:
    remove_path = f"/sys/bus/pci/devices/{config.endpoint_bdf}/remove"
    return ToolStep("remove-endpoint", ("sh", "-c", f"test ! -e {remove_path} || echo 1 > {remove_path}"))


def pci_global_rescan_step() -> ToolStep:
    return ToolStep("pci-global-rescan", ("sh", "-c", "echo 1 > /sys/bus/pci/rescan"))


def pci_rescan_step(bridge_bdfs: Sequence[str] = PCI_RESCAN_BDFS) -> ToolStep:
    return ToolStep("pci-rescan", ("sh", "-c", _pci_rescan_script(bridge_bdfs)))


def lspci_endpoint_step(config: NiteFuryToolchainConfig) -> ToolStep:
    return ToolStep("lspci-endpoint", ("sh", "-c", _lspci_endpoint_script(config)))


def _runtime_pm_argv(config: NiteFuryToolchainConfig, mode: str) -> tuple[str, ...]:
    argv = [config.runtime_pm_executable, mode]
    for pattern in config.runtime_pm_patterns:
        argv.extend(("--pattern", pattern))
    return tuple(argv)


def _driver_hardware_smoke_code() -> str:
    return "; ".join(
        (
            "from pathlib import Path",
            "from dau_core.registers import DAU_MAGIC_WORD, RegisterOffset",
            "from dau_driver import discover_devices",
            "devices = discover_devices(Path('/dev'))",
            "assert devices, 'expected at least one DAU XDMA device'",
            "device = devices[0]",
            "assert device.read_register(RegisterOffset.MAGIC) == DAU_MAGIC_WORD",
            "snapshot = device.read_register_block_snapshot()",
            "assert snapshot.platform_id",
            "print('DAU_SMOKE_OK', device.name, snapshot.platform_id)",
        )
    )


def _lspci_endpoint_script(config: NiteFuryToolchainConfig, bridge_bdfs: Sequence[str] = PCI_RESCAN_BDFS) -> str:
    expected_id = shlex.quote(config.expected_endpoint_id.lower())
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


def _remote_runtime_pm_script(config: NiteFuryToolchainConfig, mode: str, remote_dau_utils_root: Path | None, remote_python: str) -> str:
    if remote_dau_utils_root is None:
        return shlex.join(_runtime_pm_argv(config, mode))

    argv = [remote_python, "-c", "from dau_utils.pci_runtime_pm import main; raise SystemExit(main())", mode]
    for pattern in config.runtime_pm_patterns:
        argv.extend(("--pattern", pattern))
    return f"PYTHONPATH={shlex.quote(str(remote_dau_utils_root))} {shlex.join(argv)}"


def _local_runtime_pm_script(config: NiteFuryToolchainConfig, mode: str, dau_utils_root: Path, python: str) -> str:
    argv = [python, "-c", "from dau_utils.pci_runtime_pm import main; raise SystemExit(main())", mode]
    for pattern in config.runtime_pm_patterns:
        argv.extend(("--pattern", pattern))
    return f"PYTHONPATH={shlex.quote(str(dau_utils_root))} {shlex.join(argv)}"


def _ssh_argv(remote_host: str, script: str) -> tuple[str, ...]:
    return ("ssh", remote_host, script)


def _remote_nite_path(remote_nite_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return remote_nite_root / path


def _nite_path(nite_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return nite_root / path


def _directory_argument(path: Path) -> str:
    return str(path).rstrip("/") + "/"


def _stage_nitefury_shell_script(*, source_nite_root: Path, work_nite_root: Path) -> str:
    argv = ["rsync", "-a", "--delete", "--delete-excluded"]
    for pattern in NITEFURY_SHELL_STAGE_EXCLUDES:
        argv.extend(("--exclude", pattern))
    argv.extend((_directory_argument(source_nite_root), _directory_argument(work_nite_root)))
    return f"mkdir -p {shlex.quote(str(work_nite_root.parent))} && {shlex.join(argv)}"


def _execute_steps(steps: Sequence[ToolStep]) -> int:
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


def _print_vivado_artifact_validation(validation: NiteFuryBackendArtifactValidation | NiteFuryProjectArtifactValidation) -> None:
    project = f"project={validation.project_manifest_path} " if isinstance(validation, NiteFuryProjectArtifactValidation) else ""
    if validation.ok:
        print(
            "vivado-artifacts-valid\t"
            f"{project}"
            f"manifest={validation.manifest_path} "
            f"overlay={validation.overlay_tcl_path} "
            f"command_plan={validation.command_plan_path} "
            f"bitstream={validation.bitstream_path}"
        )
        return
    print(f"vivado-artifacts-invalid\t{project}manifest={validation.manifest_path} command_plan={validation.command_plan_path}")
    for error in validation.errors:
        print(f"error\t{error}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print NiteFury build/program/recovery command plans")
    parser.add_argument(
        "plan",
        choices=(
            "build-and-program",
            "flash",
            "local-build-and-program",
            "recovery",
            "remote-build",
            "remote-build-and-program",
            "remote-flash",
            "remote-xdma-load",
            "remote-xdma-rebuild",
            "stage-nitefury-project",
            "stage-nitefury-shell",
            "stage-vivado-overlay",
            "thunderbolt-hold",
            "thunderbolt-release",
            "validate-bitstream",
            "validate-vivado-artifacts",
        ),
        help="Command plan to print",
    )
    parser.add_argument("--nite-root", required=True, type=Path, help="Path to the NiteFury project root")
    parser.add_argument("--source-nite-root", type=Path, help="Read-only NiteFury shell seed copied into --nite-root before staging/building")
    parser.add_argument("--bitstream", type=Path, help="Bitstream path to program; relative paths are resolved under --nite-root")
    parser.add_argument("--vivado", default="vivado", help="Vivado executable to use in build plans")
    parser.add_argument("--openfpgaloader", default="openFPGALoader", help="openFPGALoader executable to use")
    parser.add_argument("--jtag-cable", default="digilent_hs2", help="openFPGALoader cable profile")
    parser.add_argument("--endpoint-bdf", default="0000:04:00.0", help="PCI endpoint BDF for recovery/check steps")
    parser.add_argument("--dau-core-root", type=Path, help="Path to the local dau-core checkout")
    parser.add_argument("--dau-driver-root", type=Path, help="Path to the local dau-driver checkout")
    parser.add_argument("--dau-utils-root", type=Path, help="Path to the local dau-utils checkout")
    parser.add_argument(
        "--overlay-tcl", default=Path("scripts/dau_overlay.tcl"), type=Path, help="Overlay TCL path relative to the local NiteFury root"
    )
    parser.add_argument("--artifact-stem", default="dau-nitefury", help="Stem used for generated backend manifest and command-plan artifacts")
    parser.add_argument("--backend-platform", default="nitefury", help="Structured backend platform name recorded in generated manifests")
    parser.add_argument("--backend-shell", default="nitefury-xdma", help="Structured backend shell name recorded in generated manifests")
    parser.add_argument("--operator", action="append", help="Operator included in the structured backend request; may be passed more than once")
    parser.add_argument("--register-map-version", default="0.1", help="Register-map contract version recorded in generated manifests")
    parser.add_argument("--stream-protocol-version", default="0.1", help="Stream protocol contract version recorded in generated manifests")
    parser.add_argument(
        "--manifest-path", type=Path, help="Dry-run manifest path relative to the local NiteFury root; defaults to <artifact-stem>.manifest"
    )
    parser.add_argument(
        "--command-plan-path", type=Path, help="Dry-run command plan path relative to the local NiteFury root; defaults to <artifact-stem>.plan"
    )
    parser.add_argument(
        "--project-manifest-path",
        type=Path,
        help="Structured project manifest path relative to the local NiteFury root; defaults to <artifact-stem>.project",
    )
    parser.add_argument("--remote-host", help="SSH host for remote plans")
    parser.add_argument("--remote-nite-root", type=Path, help="Path to the NiteFury project root on the remote host")
    parser.add_argument("--remote-source-nite-root", type=Path, help="Read-only remote NiteFury shell seed copied into --remote-nite-root")
    parser.add_argument("--remote-dau-core-root", type=Path, help="Path to the dau-core checkout on the remote host")
    parser.add_argument("--remote-dau-driver-root", type=Path, help="Path to the dau-driver checkout on the remote host")
    parser.add_argument("--remote-dau-utils-root", type=Path, help="Path to the dau-utils checkout on the remote host")
    parser.add_argument("--remote-xdma-root", type=Path, help="Path to the remote XDMA driver source directory")
    parser.add_argument(
        "--remote-overlay-tcl", default=Path("scripts/dau_overlay.tcl"), type=Path, help="Overlay TCL path relative to the remote NiteFury root"
    )
    parser.add_argument("--remote-python", default="python3", help="Remote Python executable for hardware smoke tests")
    parser.add_argument("--python", default="python3", help="Local Python executable for hardware smoke tests and source-checkout runtime PM")
    parser.add_argument("--vivado-settings", default=Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"), type=Path, help="Remote Vivado settings script")
    parser.add_argument("--execute", action="store_true", help="Execute the selected plan instead of printing it")
    args = parser.parse_args(argv)

    config = NiteFuryToolchainConfig(
        nite_root=args.nite_root,
        vivado_executable=args.vivado,
        bitstream_path=args.bitstream,
        openfpgaloader_executable=args.openfpgaloader,
        jtag_cable=args.jtag_cable,
        endpoint_bdf=args.endpoint_bdf,
    )
    if args.plan == "build-and-program":
        steps = build_and_program_plan(config)
    elif args.plan == "local-build-and-program":
        if args.dau_core_root is None or args.dau_driver_root is None:
            parser.error("local-build-and-program requires --dau-core-root and --dau-driver-root")
        steps = local_build_and_program_plan(
            config,
            dau_core_root=args.dau_core_root,
            dau_driver_root=args.dau_driver_root,
            source_nite_root=args.source_nite_root,
            dau_utils_root=args.dau_utils_root,
            overlay_tcl=args.overlay_tcl,
            python=args.python,
            vivado_settings=args.vivado_settings,
        )
    elif args.plan == "validate-bitstream":
        if args.dau_core_root is None or args.dau_driver_root is None:
            parser.error("validate-bitstream requires --dau-core-root and --dau-driver-root")
        steps = validate_bitstream_plan(
            config,
            dau_core_root=args.dau_core_root,
            dau_driver_root=args.dau_driver_root,
            dau_utils_root=args.dau_utils_root,
            python=args.python,
        )
    elif args.plan == "validate-vivado-artifacts":
        validation = validate_vivado_artifacts(
            config,
            manifest_path=args.manifest_path or Path("dau-nitefury.manifest"),
            command_plan_path=args.command_plan_path or Path("dau-nitefury.plan"),
            project_manifest_path=args.project_manifest_path,
        )
        _print_vivado_artifact_validation(validation)
        return 0 if validation.ok else 1
    elif args.plan == "stage-nitefury-shell":
        if args.source_nite_root is None:
            parser.error("stage-nitefury-shell requires --source-nite-root")
        steps = stage_nitefury_shell_plan(config, source_nite_root=args.source_nite_root)
    elif args.plan == "stage-nitefury-project":
        if args.source_nite_root is None or args.dau_core_root is None or args.dau_driver_root is None:
            parser.error("stage-nitefury-project requires --source-nite-root, --dau-core-root, and --dau-driver-root")
        steps = stage_nitefury_project_plan(
            config,
            source_nite_root=args.source_nite_root,
            dau_core_root=args.dau_core_root,
            dau_driver_root=args.dau_driver_root,
            dau_utils_root=args.dau_utils_root,
            artifact_stem=args.artifact_stem,
            platform=args.backend_platform,
            shell=args.backend_shell,
            operator_set=tuple(args.operator or ("identity",)),
            register_map_version=args.register_map_version,
            stream_protocol_version=args.stream_protocol_version,
            overlay_tcl=args.overlay_tcl,
            manifest_path=args.manifest_path,
            command_plan_path=args.command_plan_path,
            project_manifest_path=args.project_manifest_path,
            vivado_settings=args.vivado_settings,
        )
    elif args.plan == "stage-vivado-overlay":
        if args.dau_core_root is None:
            parser.error("stage-vivado-overlay requires --dau-core-root")
        steps = stage_vivado_overlay_plan(
            config,
            dau_core_root=args.dau_core_root,
            source_nite_root=args.source_nite_root,
            artifact_stem=args.artifact_stem,
            platform=args.backend_platform,
            shell=args.backend_shell,
            operator_set=tuple(args.operator or ("identity",)),
            register_map_version=args.register_map_version,
            stream_protocol_version=args.stream_protocol_version,
            overlay_tcl=args.overlay_tcl,
            manifest_path=args.manifest_path,
            command_plan_path=args.command_plan_path,
            vivado_settings=args.vivado_settings,
        )
    elif args.plan == "flash":
        steps = flash_plan(config, dau_utils_root=args.dau_utils_root, python=args.python, vivado_settings=args.vivado_settings)
    elif args.plan == "recovery":
        steps = recovery_plan(config)
    elif args.plan == "remote-build":
        if args.remote_host is None or args.remote_nite_root is None:
            parser.error("remote-build requires --remote-host and --remote-nite-root")
        steps = remote_build_plan(
            config,
            remote_host=args.remote_host,
            remote_nite_root=args.remote_nite_root,
            remote_source_nite_root=args.remote_source_nite_root,
            remote_dau_utils_root=args.remote_dau_utils_root,
            remote_python=args.remote_python,
            vivado_settings=args.vivado_settings,
        )
    elif args.plan == "remote-build-and-program":
        if args.remote_host is None or args.remote_nite_root is None or args.remote_dau_core_root is None or args.remote_dau_driver_root is None:
            parser.error("remote-build-and-program requires --remote-host, --remote-nite-root, --remote-dau-core-root, and --remote-dau-driver-root")
        steps = remote_build_and_program_plan(
            config,
            remote_host=args.remote_host,
            remote_nite_root=args.remote_nite_root,
            remote_dau_core_root=args.remote_dau_core_root,
            remote_dau_driver_root=args.remote_dau_driver_root,
            remote_source_nite_root=args.remote_source_nite_root,
            remote_dau_utils_root=args.remote_dau_utils_root,
            remote_overlay_tcl=args.remote_overlay_tcl,
            remote_python=args.remote_python,
            vivado_settings=args.vivado_settings,
        )
    elif args.plan == "remote-flash":
        if args.remote_host is None or args.remote_nite_root is None:
            parser.error("remote-flash requires --remote-host and --remote-nite-root")
        steps = remote_flash_plan(
            config,
            remote_host=args.remote_host,
            remote_nite_root=args.remote_nite_root,
            remote_dau_utils_root=args.remote_dau_utils_root,
            remote_python=args.remote_python,
            vivado_settings=args.vivado_settings,
        )
    elif args.plan == "remote-xdma-load":
        if args.remote_host is None or args.remote_xdma_root is None:
            parser.error("remote-xdma-load requires --remote-host and --remote-xdma-root")
        steps = remote_xdma_load_plan(
            config,
            remote_host=args.remote_host,
            remote_xdma_root=args.remote_xdma_root,
            remote_dau_utils_root=args.remote_dau_utils_root,
            remote_python=args.remote_python,
        )
    elif args.plan == "remote-xdma-rebuild":
        if args.remote_host is None or args.remote_xdma_root is None:
            parser.error("remote-xdma-rebuild requires --remote-host and --remote-xdma-root")
        steps = remote_xdma_rebuild_plan(config, remote_host=args.remote_host, remote_xdma_root=args.remote_xdma_root)
    elif args.plan == "thunderbolt-hold":
        steps = thunderbolt_hold_plan(config)
    else:
        steps = thunderbolt_release_plan(config)
    if args.execute:
        return _execute_steps(steps)
    for step in steps:
        print(f"{step.name}\t{step.command_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
