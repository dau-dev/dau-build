from __future__ import annotations

import argparse
import base64
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ccflow import BaseModel

from dau_build.vivado_backend import (
    VivadoBackendArtifactValidation,
    VivadoBackendRequest,
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

# dpv1 board identity (platform integration lives here; the private DAU
# packages carry their own copy and the dau integration suite pins the two)
DPV1_PCI_ID = "10ee:7011"

PCI_RESCAN_BDFS = (
    "0000:03:01.0",
    "0000:02:00.0",
    "0000:00:0d.3",
    "0000:00:0d.2",
    "0000:00:0d.0",
    "0000:00:07.2",
    "0000:00:07.0",
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
    vivado_invocation: str = "standard"
    vivado_mount_root: Path | None = None
    openfpgaloader_executable: str = "openFPGALoader"
    runtime_pm_executable: str = "dau-pci-runtime-pm"
    runtime_pm_patterns: tuple[str, ...] = ("Thunderbolt", "JHL", DPV1_PCI_ID, "Xilinx")
    jtag_cable: str = "digilent_hs2"
    endpoint_bdf: str = "0000:04:00.0"
    expected_endpoint_id: str = DPV1_PCI_ID

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
        return self.endpoint_bdf.removeprefix("0000:")


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
        pci_rescan_step(),
        lspci_endpoint_step(config),
    )


def recovery_plan(config: HardwareToolchainConfig) -> tuple[ToolStep, ...]:
    return (
        thunderbolt_hold_step(config),
        remove_endpoint_step(config),
        program_volatile_step(config),
        pci_rescan_step(),
        lspci_endpoint_step(config),
    )


def local_build_and_program_plan(
    config: HardwareToolchainConfig,
    *,
    dau_core_root: Path,
    dau_driver_root: Path,
    source_shell_root: Path | None = None,
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl"),
    dau_utils_root: Path | None = None,
    python: str = "python3",
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"),
) -> tuple[ToolStep, ...]:
    overlay_path = _work_path(config.work_root, overlay_tcl)
    build_tcl = Path("scripts/dau_build.tcl")
    build_tcl_path = _work_path(config.work_root, build_tcl)
    vivado_path_base = config.work_root.resolve(strict=False) if config.vivado_mount_root is not None else None
    overlay_source = dau_overlay_tcl(dau_core_root / "dau_core" / "hdl", vivado_path_base=vivado_path_base)
    stage_steps = () if source_shell_root is None else stage_shell_plan(config, source_shell_root=source_shell_root)
    return (
        *stage_steps,
        thunderbolt_hold_step(config, dau_utils_root=dau_utils_root, python=python),
        write_dau_overlay_step(overlay_path=overlay_path, source=overlay_source),
        write_vivado_build_script_step(build_tcl_path=build_tcl_path, source=vivado_build_tcl()),
        *_local_vivado_driver_steps(config, overlay_tcl=overlay_tcl, build_tcl=build_tcl),
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


def driver_hardware_smoke_step(*, dau_core_root: Path, dau_driver_root: Path, python: str) -> ToolStep:
    pythonpath = f"{dau_core_root}:{dau_driver_root}"
    script = f"PYTHONPATH={shlex.quote(pythonpath)} {shlex.join((python, '-c', _driver_hardware_smoke_code()))}"
    return ToolStep("driver-hardware-smoke", ("sh", "-c", script))


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
    return ToolStep("jtag-detect", (config.openfpgaloader_executable, "-c", config.jtag_cable, "--detect"))


def program_volatile_step(config: HardwareToolchainConfig) -> ToolStep:
    return ToolStep("program-volatile", (config.openfpgaloader_executable, "-c", config.jtag_cable, str(config.bitstream)))


def remove_endpoint_step(config: HardwareToolchainConfig) -> ToolStep:
    remove_path = f"/sys/bus/pci/devices/{config.endpoint_bdf}/remove"
    return ToolStep("remove-endpoint", ("sh", "-c", f"test ! -e {remove_path} || echo 1 > {remove_path}"))


def pci_global_rescan_step() -> ToolStep:
    return ToolStep("pci-global-rescan", ("sh", "-c", "echo 1 > /sys/bus/pci/rescan"))


def pci_rescan_step(bridge_bdfs: Sequence[str] = PCI_RESCAN_BDFS) -> ToolStep:
    return ToolStep("pci-rescan", ("sh", "-c", _pci_rescan_script(bridge_bdfs)))


def lspci_endpoint_step(config: HardwareToolchainConfig) -> ToolStep:
    return ToolStep("lspci-endpoint", ("sh", "-c", _lspci_endpoint_script(config)))


def _runtime_pm_argv(config: HardwareToolchainConfig, mode: str) -> tuple[str, ...]:
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


def _lspci_endpoint_script(config: HardwareToolchainConfig, bridge_bdfs: Sequence[str] = PCI_RESCAN_BDFS) -> str:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Vivado build/program/recovery command plans")
    parser.add_argument(
        "plan",
        choices=(
            "build-and-program",
            "flash",
            "local-build-and-program",
            "recovery",
            "thunderbolt-hold",
            "thunderbolt-release",
            "validate-bitstream",
        ),
        help="Command plan to print",
    )
    parser.add_argument("--work-root", required=True, type=Path, help="Path to the Vivado project root")
    parser.add_argument("--source-shell-root", type=Path, help="Read-only Vivado shell seed copied into --work-root before staging/building")
    parser.add_argument("--bitstream", type=Path, help="Bitstream path to program; relative paths are resolved under --work-root")
    parser.add_argument("--vivado", default="vivado", help="Vivado executable to use in build plans")
    parser.add_argument(
        "--vivado-invocation",
        default="standard",
        choices=("standard", "source-only"),
        help="Use 'source-only' for wrappers that already run Vivado in batch source mode",
    )
    parser.add_argument(
        "--vivado-mount-root",
        type=Path,
        help="Host root mounted by a source-only Vivado wrapper; generated Tcl paths are made relative to the staged workdir",
    )
    parser.add_argument("--openfpgaloader", default="openFPGALoader", help="openFPGALoader executable to use")
    parser.add_argument("--jtag-cable", default="digilent_hs2", help="openFPGALoader cable profile")
    parser.add_argument("--endpoint-bdf", default="0000:04:00.0", help="PCI endpoint BDF for recovery/check steps")
    parser.add_argument("--dau-core-root", type=Path, help="Path to the local dau-core checkout")
    parser.add_argument("--dau-driver-root", type=Path, help="Path to the local dau-driver checkout")
    parser.add_argument("--dau-utils-root", type=Path, help="Path to the local dau-utils checkout")
    parser.add_argument(
        "--overlay-tcl", default=Path("scripts/dau_overlay.tcl"), type=Path, help="Overlay TCL path relative to the local Vivado root"
    )
    parser.add_argument("--python", default="python3", help="Local Python executable for hardware smoke tests and source-checkout runtime PM")
    parser.add_argument("--vivado-settings", default=Path("/opt/Xilinx/2025.1/Vivado/settings64.sh"), type=Path, help="Remote Vivado settings script")
    parser.add_argument("--execute", action="store_true", help="Execute the selected plan instead of printing it")
    args = parser.parse_args(argv)

    config = HardwareToolchainConfig(
        work_root=args.work_root,
        vivado_executable=args.vivado,
        vivado_invocation=args.vivado_invocation,
        vivado_mount_root=args.vivado_mount_root,
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
            source_shell_root=args.source_shell_root,
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
    elif args.plan == "flash":
        steps = flash_plan(config, dau_utils_root=args.dau_utils_root, python=args.python, vivado_settings=args.vivado_settings)
    elif args.plan == "recovery":
        steps = recovery_plan(config)
    elif args.plan == "thunderbolt-hold":
        steps = thunderbolt_hold_plan(config)
    else:
        steps = thunderbolt_release_plan(config)
    if args.execute:
        return execute_plan_steps(steps)
    print(format_plan_steps(steps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
