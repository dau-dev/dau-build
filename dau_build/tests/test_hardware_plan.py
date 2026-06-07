import base64
import runpy
import shlex
import sys
from pathlib import Path

import pytest

import dau_build.hardware_plan as hardware_plan
import dau_build.vivado_backend as vivado_backend
from dau_build.hardware_plan import (
    HardwareToolchainConfig,
    build_and_program_plan,
    flash_plan,
    local_build_and_program_plan,
    main,
    recovery_plan,
    stage_shell_plan,
    stage_vivado_overlay_plan,
    stage_vivado_project_plan,
    thunderbolt_hold_plan,
    thunderbolt_release_plan,
    validate_bitstream_plan,
)
from dau_build.vivado_backend import (
    VivadoBackendRequest,
    VivadoProjectGenerationRequest,
    dau_overlay_manifest,
    dau_overlay_manifest_text,
    dau_overlay_tcl,
    generate_vivado_backend_artifacts,
    generate_vivado_project_generation_artifacts,
    validate_vivado_backend_artifact_bundle,
    validate_vivado_project_artifact_bundle,
    write_dau_overlay_tcl,
)

EXPECTED_PCI_RESCAN_BDFS = (
    "0000:03:01.0",
    "0000:02:00.0",
    "0000:00:0d.3",
    "0000:00:0d.2",
    "0000:00:0d.0",
    "0000:00:07.2",
    "0000:00:07.0",
)
EXPECTED_PCI_RESCAN_SCRIPT = (
    f"for bdf in {' '.join(EXPECTED_PCI_RESCAN_BDFS)}; "
    "do test ! -w /sys/bus/pci/devices/$bdf/rescan || "
    "echo 1 > /sys/bus/pci/devices/$bdf/rescan; "
    "done && echo 1 > /sys/bus/pci/rescan"
)
EXPECTED_LSPCI_ENDPOINT_SNIPPET = "endpoint_output=$(lspci -Dnn -d 10ee:7011 || true)"
EXPECTED_LSPCI_ENDPOINT_RETRY_SNIPPET = f"for attempt in 1 2 3; do {EXPECTED_PCI_RESCAN_SCRIPT};"


def test_hardware_plan_source_fixtures_do_not_embed_local_hardware_paths() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    checked_files = (Path(__file__), repo_root / "README.md")
    forbidden_fragments = tuple(
        "".join(parts)
        for parts in (
            ("/", "Users", "/"),
            ("/home/", "tim", "kpaine"),
            ("10.", "0.", "1."),
            ("48:", "21:"),
            ("root@", "n", "uc"),
            ("n", "uc", "1"),
            ("n", "uc", "2"),
            ("tim", "kpaine"),
        )
    )

    leaks = [
        f"{path.relative_to(repo_root)} contains {fragment}"
        for path in checked_files
        for fragment in forbidden_fragments
        if fragment in path.read_text()
    ]

    assert leaks == []


def _decode_write_text_step_source(step) -> str:
    tokens = shlex.split(step.argv[2])
    payload = tokens[tokens.index("%s") + 1]
    return base64.b64decode(payload).decode("utf-8")


def _write_backend_artifacts(request: VivadoBackendRequest):
    artifacts = generate_vivado_backend_artifacts(request)
    artifacts.overlay_tcl_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.overlay_tcl_path.write_text(artifacts.overlay_tcl_text, encoding="utf-8")
    artifacts.build_tcl_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.build_tcl_path.write_text(artifacts.build_tcl_text, encoding="utf-8")
    artifacts.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.manifest_path.write_text(artifacts.manifest_text, encoding="utf-8")
    artifacts.command_plan_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.command_plan_path.write_text(artifacts.command_plan_text, encoding="utf-8")
    return artifacts


def _write_project_artifacts(request: VivadoProjectGenerationRequest):
    artifacts = generate_vivado_project_generation_artifacts(request)
    backend_artifacts = artifacts.backend_artifacts
    artifacts.project_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.project_manifest_path.write_text(artifacts.project_manifest_text, encoding="utf-8")
    backend_artifacts.overlay_tcl_path.parent.mkdir(parents=True, exist_ok=True)
    backend_artifacts.overlay_tcl_path.write_text(backend_artifacts.overlay_tcl_text, encoding="utf-8")
    backend_artifacts.build_tcl_path.parent.mkdir(parents=True, exist_ok=True)
    backend_artifacts.build_tcl_path.write_text(backend_artifacts.build_tcl_text, encoding="utf-8")
    backend_artifacts.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    backend_artifacts.manifest_path.write_text(backend_artifacts.manifest_text, encoding="utf-8")
    backend_artifacts.command_plan_path.parent.mkdir(parents=True, exist_ok=True)
    backend_artifacts.command_plan_path.write_text(backend_artifacts.command_plan_text, encoding="utf-8")
    return artifacts


def test_structured_vivado_backend_request_records_ci_artifact_contract() -> None:
    request = VivadoBackendRequest(
        dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
        build_root=Path("/repo/dau-build/outputs/vivado"),
        artifact_stem="dau-ci",
        platform="vivado-xdma",
        shell="seeded-xdma",
        operator_set=("identity", "sum_i64"),
        register_map_version="0.1",
        stream_protocol_version="0.2",
        overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
        bitstream_path=Path("artifacts/dau-ci.bit"),
        vivado_settings=Path("/tools/Vivado/settings64.sh"),
        vivado_executable="vivado2025.1",
    )

    artifacts = generate_vivado_backend_artifacts(request)

    manifest = dict(line.split("=", 1) for line in artifacts.manifest_text.splitlines())
    assert artifacts.overlay_tcl_path == Path("/repo/dau-build/outputs/vivado/scripts/dau_ci_overlay.tcl")
    assert artifacts.manifest_path == Path("/repo/dau-build/outputs/vivado/dau-ci.manifest")
    assert artifacts.command_plan_path == Path("/repo/dau-build/outputs/vivado/dau-ci.plan")
    assert artifacts.bitstream_path == Path("/repo/dau-build/outputs/vivado/artifacts/dau-ci.bit")
    assert manifest["backend"] == "dau_build.vivado_backend.vivado_overlay"
    assert manifest["platform"] == "vivado-xdma"
    assert manifest["shell"] == "seeded-xdma"
    assert manifest["artifact_stem"] == "dau-ci"
    assert manifest["build_root"] == "/repo/dau-build/outputs/vivado"
    assert manifest["overlay"] == "scripts/dau_ci_overlay.tcl"
    assert manifest["bitstream"] == "artifacts/dau-ci.bit"
    assert manifest["manifest"] == "dau-ci.manifest"
    assert manifest["command_plan"] == "dau-ci.plan"
    assert manifest["register_map_version"] == "0.1"
    assert manifest["stream_protocol_version"] == "0.2"
    assert manifest["operator_set"] == "identity,sum_i64"
    assert manifest["vivado_settings"] == "/tools/Vivado/settings64.sh"
    assert manifest["vivado_executable"] == "vivado2025.1"
    assert "cd /repo/dau-build/outputs/vivado" in artifacts.command_plan_text
    assert 'puts $manifest_file "overlay=scripts/dau_ci_overlay.tcl"' in artifacts.overlay_tcl_text
    assert 'puts $manifest_file "bitstream=artifacts/dau-ci.bit"' in artifacts.overlay_tcl_text
    assert "vivado2025.1 -mode batch -source scripts/dau_ci_overlay.tcl" in artifacts.command_plan_text


def test_structured_vivado_backend_request_supports_source_only_vivado_wrapper() -> None:
    request = VivadoBackendRequest(
        dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
        build_root=Path("/repo/dau-build/outputs/vivado"),
        artifact_stem="dau-ci",
        overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
        vivado_settings=Path("/tools/Vivado/settings64.sh"),
        vivado_executable="vivado",
        vivado_invocation="source-only",
    )

    artifacts = generate_vivado_backend_artifacts(request)

    manifest = dict(line.split("=", 1) for line in artifacts.manifest_text.splitlines())
    assert manifest["vivado_invocation"] == "source-only"
    assert "cd /repo/dau-build/outputs/vivado" in artifacts.command_plan_text
    assert ". /tools/Vivado/settings64.sh" not in artifacts.command_plan_text
    assert "vivado -mode batch -source" not in artifacts.command_plan_text
    assert "vivado scripts/dau_ci_overlay.tcl" in artifacts.command_plan_text
    assert "vivado scripts/dau_build.tcl" in artifacts.command_plan_text


def test_structured_vivado_backend_request_supports_source_only_mount_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    build_root = repo_root / "dau-build" / "outputs" / "vivado-xdma"
    bundle_root = repo_root / "dau-build" / "outputs" / "dau-bundle"
    (bundle_root / "generated").mkdir(parents=True)
    (bundle_root / "rtl").mkdir()
    (bundle_root / "generated" / "dau_identity_top.sv").write_text("module dau_identity_top; endmodule\n", encoding="utf-8")
    (bundle_root / "rtl" / "operator.sv").write_text("module operator; endmodule\n", encoding="utf-8")
    bundle_path = bundle_root / "dau-identity.artifacts.yaml"
    bundle_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: dau-identity",
                "artifacts:",
                "  - path: generated/dau_identity_top.sv",
                "    kind: source",
                "    role: generated-top",
                "    language: systemverilog",
                "    provides:",
                "      - kind: hdl-module",
                "        name: dau_identity_top",
                "  - path: rtl/operator.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "    provides:",
                "      - kind: hdl-module",
                "        name: operator",
                "",
            )
        ),
        encoding="utf-8",
    )
    request = VivadoBackendRequest(
        dau_core_hdl_root=repo_root / "dau-core" / "dau_core" / "hdl",
        build_root=build_root,
        artifact_stem="dau-ci",
        dau_artifact_bundle_path=bundle_path,
        vivado_invocation="source-only",
        vivado_mount_root=repo_root,
    )

    artifacts = generate_vivado_backend_artifacts(request)
    manifest = dict(line.split("=", 1) for line in artifacts.manifest_text.splitlines())

    assert artifacts.overlay_driver_tcl_path == build_root / "scripts" / "dau_overlay.driver.tcl"
    assert artifacts.build_driver_tcl_path == build_root / "scripts" / "dau_build.driver.tcl"
    assert artifacts.overlay_driver_tcl_text == "cd dau-build/outputs/vivado-xdma\nsource scripts/dau_overlay.tcl\n"
    assert artifacts.build_driver_tcl_text == "cd dau-build/outputs/vivado-xdma\nsource scripts/dau_build.tcl\n"
    assert manifest["vivado_mount_root"] == repo_root.as_posix()
    assert manifest["dau_artifact_bundle"] == "../dau-bundle/dau-identity.artifacts.yaml"
    assert manifest["dau_generated_top"] == "../dau-bundle/generated/dau_identity_top.sv"
    assert manifest["dau_bundle_hdl_sources"] == "../dau-bundle/generated/dau_identity_top.sv,../dau-bundle/rtl/operator.sv"
    assert repo_root.as_posix() not in artifacts.overlay_tcl_text
    assert 'set dau_identity_registers_sv [file normalize "../../../dau-core/dau_core/hdl/dau_identity_registers.sv"]' in artifacts.overlay_tcl_text
    assert '[file normalize "../dau-bundle/generated/dau_identity_top.sv"]' in artifacts.overlay_tcl_text
    assert f"cd {repo_root.as_posix()}" in artifacts.command_plan_text
    assert "vivado dau-build/outputs/vivado-xdma/scripts/dau_overlay.driver.tcl" in artifacts.command_plan_text
    assert "rm -f dau-build/outputs/vivado-xdma/Top.v" in artifacts.command_plan_text
    assert "vivado dau-build/outputs/vivado-xdma/scripts/dau_build.driver.tcl" in artifacts.command_plan_text

    artifacts.overlay_tcl_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.overlay_tcl_path.write_text(artifacts.overlay_tcl_text, encoding="utf-8")
    artifacts.manifest_path.write_text(artifacts.manifest_text, encoding="utf-8")
    artifacts.command_plan_path.write_text(artifacts.command_plan_text, encoding="utf-8")
    validation = validate_vivado_backend_artifact_bundle(build_root, manifest_path=Path("dau-ci.manifest"), command_plan_path=Path("dau-ci.plan"))

    assert validation.ok


def test_structured_vivado_backend_request_consumes_dau_artifact_bundle(tmp_path: Path) -> None:
    bundle_root = tmp_path / "dau-bundle"
    (bundle_root / "generated").mkdir(parents=True)
    (bundle_root / "rtl").mkdir()
    (bundle_root / "generated" / "dau_identity_top.sv").write_text("module dau_identity_top; endmodule\n", encoding="utf-8")
    (bundle_root / "rtl" / "operator.sv").write_text("module operator; endmodule\n", encoding="utf-8")
    bundle_path = bundle_root / "dau-identity.artifacts.yaml"
    bundle_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: dau-identity",
                "artifacts:",
                "  - path: generated/dau_identity_top.sv",
                "    kind: source",
                "    role: generated-top",
                "    language: systemverilog",
                "    provides:",
                "      - kind: hdl-module",
                "        name: dau_identity_top",
                "  - path: rtl/operator.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "    provides:",
                "      - kind: hdl-module",
                "        name: operator",
                "",
            )
        ),
        encoding="utf-8",
    )
    request = VivadoBackendRequest(
        dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
        build_root=tmp_path / "vivado-xdma",
        artifact_stem="dau-ci",
        dau_artifact_bundle_path=bundle_path,
    )

    artifacts = generate_vivado_backend_artifacts(request)

    manifest = dict(line.split("=", 1) for line in artifacts.manifest_text.splitlines())
    generated_top = (bundle_root / "generated" / "dau_identity_top.sv").resolve().as_posix()
    operator_source = (bundle_root / "rtl" / "operator.sv").resolve().as_posix()
    assert manifest["dau_artifact_bundle"] == bundle_path.resolve().as_posix()
    assert manifest["dau_generated_top"] == generated_top
    assert manifest["dau_bundle_hdl_sources"] == f"{generated_top},{operator_source}"
    assert "set dau_bundle_hdl_sources [list" in artifacts.overlay_tcl_text
    assert generated_top in artifacts.overlay_tcl_text
    assert operator_source in artifacts.overlay_tcl_text
    assert "foreach dau_bundle_hdl_source" in artifacts.overlay_tcl_text
    assert f'puts $manifest_file "dau_artifact_bundle={bundle_path.resolve().as_posix()}"' in artifacts.overlay_tcl_text
    assert f'puts $manifest_file "dau_generated_top={generated_top}"' in artifacts.overlay_tcl_text


def test_structured_vivado_project_generation_request_records_workdir_inputs() -> None:
    request = VivadoProjectGenerationRequest(
        source_shell_root=Path("/repo/projects/vivado-shell"),
        work_root=Path("/repo/dau-build/outputs/vivado"),
        dau_core_root=Path("/repo/dau-core"),
        dau_driver_root=Path("/repo/dau-driver"),
        dau_utils_root=Path("/repo/dau-utils"),
        dau_build_manifest_path=Path("/repo/dau-build/outputs/dau-identity/dau-identity.manifest"),
        dau_top_sv_path=Path("/repo/dau-build/outputs/dau-identity/generated/dau_identity_top.sv"),
        artifact_stem="dau-ci",
        platform="vivado-xdma",
        shell="seeded-xdma",
        operator_set=("identity", "sum_i64"),
        register_map_version="0.1",
        stream_protocol_version="0.2",
        overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
        bitstream_path=Path("artifacts/dau-ci.bit"),
        vivado_settings=Path("/tools/Vivado/settings64.sh"),
        vivado_executable="vivado2025.1",
    )

    artifacts = generate_vivado_project_generation_artifacts(request)

    manifest = dict(line.split("=", 1) for line in artifacts.project_manifest_text.splitlines())
    assert artifacts.project_manifest_path == Path("/repo/dau-build/outputs/vivado/dau-ci.project")
    assert artifacts.backend_artifacts.manifest_path == Path("/repo/dau-build/outputs/vivado/dau-ci.manifest")
    assert manifest["project_generator"] == "dau_build.vivado_backend.vivado_project"
    assert manifest["source_shell_root"] == "/repo/projects/vivado-shell"
    assert manifest["work_root"] == "/repo/dau-build/outputs/vivado"
    assert manifest["dau_core_root"] == "/repo/dau-core"
    assert manifest["dau_core_hdl_root"] == "/repo/dau-core/dau_core/hdl"
    assert manifest["dau_driver_root"] == "/repo/dau-driver"
    assert manifest["dau_utils_root"] == "/repo/dau-utils"
    assert manifest["dau_build_manifest"] == "/repo/dau-build/outputs/dau-identity/dau-identity.manifest"
    assert manifest["dau_top_sv"] == "/repo/dau-build/outputs/dau-identity/generated/dau_identity_top.sv"
    assert manifest["backend_manifest"] == "dau-ci.manifest"
    assert manifest["backend_command_plan"] == "dau-ci.plan"
    assert manifest["overlay_tcl"] == "scripts/dau_ci_overlay.tcl"
    assert manifest["build_tcl"] == "scripts/dau_build.tcl"
    assert manifest["bitstream"] == "artifacts/dau-ci.bit"
    assert manifest["xdma_module"] == "sw/xdma/xdma.ko"
    assert manifest["vivado_invocation"] == "standard"
    assert manifest["stage_command"].startswith("dau-build task=stage-vivado-overlay ")
    assert "source_shell_root=/repo/projects/vivado-shell" in manifest["stage_command"]
    assert "--source-shell-root" not in manifest["stage_command"]
    assert manifest["build_command"].startswith("dau-build task=build-vivado-artifacts ")
    assert "manifest_path=dau-ci.manifest" in manifest["build_command"]
    assert "command_plan_path=dau-ci.plan" in manifest["build_command"]
    assert "project_manifest_path=dau-ci.project" in manifest["build_command"]
    assert manifest["validate_command"].startswith("dau-build task=validate-vivado-artifacts ")
    assert "manifest_path=dau-ci.manifest" in manifest["validate_command"]
    assert "command_plan_path=dau-ci.plan" in manifest["validate_command"]
    assert "project_manifest_path=dau-ci.project" in manifest["validate_command"]


def test_structured_vivado_project_generation_records_source_only_vivado_wrapper() -> None:
    request = VivadoProjectGenerationRequest(
        source_shell_root=Path("/repo/projects/vivado-shell"),
        work_root=Path("/repo/dau-build/outputs/vivado"),
        dau_core_root=Path("/repo/dau-core"),
        dau_driver_root=Path("/repo/dau-driver"),
        artifact_stem="dau-ci",
        vivado_invocation="source-only",
    )

    artifacts = generate_vivado_project_generation_artifacts(request)

    manifest = dict(line.split("=", 1) for line in artifacts.project_manifest_text.splitlines())
    assert manifest["vivado_invocation"] == "source-only"
    assert "vivado_invocation=source-only" in manifest["stage_command"]
    assert "vivado_invocation=source-only" in manifest["build_command"]


def test_validate_structured_backend_artifact_bundle_accepts_generated_bundle(tmp_path: Path) -> None:
    artifacts = _write_backend_artifacts(
        VivadoBackendRequest(
            dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
            build_root=tmp_path,
            artifact_stem="dau-ci",
            platform="vivado-xdma",
            shell="seeded-xdma",
            operator_set=("identity", "sum_i64"),
            overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
            bitstream_path=Path("artifacts/dau-ci.bit"),
            vivado_settings=Path("/tools/Vivado/settings64.sh"),
            vivado_executable="vivado2025.1",
        )
    )

    validation = validate_vivado_backend_artifact_bundle(
        tmp_path,
        manifest_path=Path("dau-ci.manifest"),
        command_plan_path=Path("dau-ci.plan"),
    )

    manifest = dict(validation.manifest_items)
    assert validation.ok
    assert validation.errors == ()
    assert validation.overlay_tcl_path == artifacts.overlay_tcl_path
    assert validation.command_plan_path == artifacts.command_plan_path
    assert validation.bitstream_path == artifacts.bitstream_path
    assert manifest["platform"] == "vivado-xdma"
    assert manifest["shell"] == "seeded-xdma"
    assert manifest["operator_set"] == "identity,sum_i64"


def test_validate_structured_backend_artifact_bundle_accepts_built_outputs(tmp_path: Path) -> None:
    artifacts = _write_backend_artifacts(
        VivadoBackendRequest(
            dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
            build_root=tmp_path,
            artifact_stem="dau-ci",
            overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
            bitstream_path=Path("artifacts/dau-ci.bit"),
        )
    )
    for path in (artifacts.bitstream_path, artifacts.resource_summary_path, artifacts.timing_summary_path, artifacts.vivado_log_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("built\n", encoding="utf-8")
    artifacts.manifest_path.write_text(
        artifacts.manifest_text.replace("build_status=planned", "build_status=built"),
        encoding="utf-8",
    )

    validation = validate_vivado_backend_artifact_bundle(
        tmp_path,
        manifest_path=Path("dau-ci.manifest"),
        command_plan_path=Path("dau-ci.plan"),
    )

    assert validation.ok
    assert validation.build_status == "built"
    assert validation.resource_summary_path == artifacts.resource_summary_path
    assert validation.timing_summary_path == artifacts.timing_summary_path
    assert validation.vivado_log_path == artifacts.vivado_log_path


def test_validate_structured_backend_artifact_bundle_rejects_built_status_without_reports(tmp_path: Path) -> None:
    artifacts = _write_backend_artifacts(
        VivadoBackendRequest(
            dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
            build_root=tmp_path,
            artifact_stem="dau-ci",
            overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
            bitstream_path=Path("artifacts/dau-ci.bit"),
        )
    )
    artifacts.manifest_path.write_text(
        artifacts.manifest_text.replace("build_status=planned", "build_status=built"),
        encoding="utf-8",
    )

    validation = validate_vivado_backend_artifact_bundle(
        tmp_path,
        manifest_path=Path("dau-ci.manifest"),
        command_plan_path=Path("dau-ci.plan"),
    )

    assert not validation.ok
    assert f"build_status=built but missing bitstream: {artifacts.bitstream_path}" in validation.errors
    assert f"build_status=built but missing resource_summary: {artifacts.resource_summary_path}" in validation.errors
    assert f"build_status=built but missing timing_summary: {artifacts.timing_summary_path}" in validation.errors
    assert f"build_status=built but missing vivado_log: {artifacts.vivado_log_path}" in validation.errors


def test_validate_structured_project_artifact_bundle_accepts_generated_bundle(tmp_path: Path) -> None:
    artifacts = _write_project_artifacts(
        VivadoProjectGenerationRequest(
            source_shell_root=Path("/repo/projects/vivado-shell"),
            work_root=tmp_path,
            dau_core_root=Path("/repo/dau-core"),
            dau_driver_root=Path("/repo/dau-driver"),
            dau_utils_root=Path("/repo/dau-utils"),
            artifact_stem="dau-ci",
            platform="vivado-xdma",
            shell="seeded-xdma",
            operator_set=("identity", "sum_i64"),
            overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
            bitstream_path=Path("artifacts/dau-ci.bit"),
            vivado_settings=Path("/tools/Vivado/settings64.sh"),
            vivado_executable="vivado2025.1",
        )
    )

    validation = validate_vivado_project_artifact_bundle(
        tmp_path,
        project_manifest_path=Path("dau-ci.project"),
        manifest_path=Path("dau-ci.manifest"),
        command_plan_path=Path("dau-ci.plan"),
    )

    assert validation.ok
    assert validation.errors == ()
    assert validation.project_manifest_path == artifacts.project_manifest_path
    assert validation.backend_validation.ok
    assert dict(validation.project_manifest_items)["backend_manifest"] == "dau-ci.manifest"


def test_validate_structured_project_artifact_bundle_reports_backend_manifest_mismatch(tmp_path: Path) -> None:
    artifacts = _write_project_artifacts(
        VivadoProjectGenerationRequest(
            source_shell_root=Path("/repo/projects/vivado-shell"),
            work_root=tmp_path,
            dau_core_root=Path("/repo/dau-core"),
            dau_driver_root=Path("/repo/dau-driver"),
            artifact_stem="dau-ci",
        )
    )
    artifacts.project_manifest_path.write_text(
        artifacts.project_manifest_text.replace("backend_manifest=dau-ci.manifest", "backend_manifest=wrong.manifest"), encoding="utf-8"
    )

    validation = validate_vivado_project_artifact_bundle(
        tmp_path,
        project_manifest_path=Path("dau-ci.project"),
        manifest_path=Path("dau-ci.manifest"),
        command_plan_path=Path("dau-ci.plan"),
    )

    assert not validation.ok
    assert "project backend manifest mismatch: wrong.manifest != dau-ci.manifest" in validation.errors


def test_validate_structured_backend_artifact_bundle_reports_missing_overlay(tmp_path: Path) -> None:
    artifacts = _write_backend_artifacts(
        VivadoBackendRequest(
            dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
            build_root=tmp_path,
            artifact_stem="dau-ci",
            overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
        )
    )
    artifacts.overlay_tcl_path.unlink()

    validation = validate_vivado_backend_artifact_bundle(
        tmp_path,
        manifest_path=Path("dau-ci.manifest"),
        command_plan_path=Path("dau-ci.plan"),
    )

    assert not validation.ok
    assert validation.errors == (f"missing overlay Tcl: {artifacts.overlay_tcl_path}",)


def test_backend_build_tcl_guards_legacy_xdma_lane_cells() -> None:
    build_tcl = vivado_backend.vivado_build_tcl()

    assert "get_cells -quiet $cell_path" in build_tcl
    assert "skipping missing PCIe lane cell" in build_tcl
    assert "reset_property LOC [get_cells" not in build_tcl


def test_hardware_toolchain_defaults_point_at_existing_vivado_project_layout() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"))

    assert config.project_tcl == Path("/repo/projects/vivado-shell/project.tcl")
    assert config.bitstream == Path("/repo/projects/vivado-shell/project.runs/impl_1/Top_wrapper.bit")


def test_stage_shell_plan_copies_seed_to_generated_workdir() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/dau-build/outputs/vivado"))

    steps = stage_shell_plan(
        config,
        source_shell_root=Path("/repo/reference/vivado-shell"),
    )

    assert [step.name for step in steps] == ["stage-shell"]
    assert steps[0].argv[0:2] == ("sh", "-c")
    assert "mkdir -p /repo/dau-build/outputs" in steps[0].argv[2]
    assert "rsync -a --delete --delete-excluded" in steps[0].argv[2]
    assert "--exclude .Xil" in steps[0].argv[2]
    assert "--exclude project.gen" in steps[0].argv[2]
    assert "--exclude project.runs" in steps[0].argv[2]
    assert "/repo/reference/vivado-shell/ /repo/dau-build/outputs/vivado/" in steps[0].argv[2]


def test_recovery_plan_keeps_reprogramming_and_pcie_rescan_as_separate_steps() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"))

    steps = recovery_plan(config)

    assert [step.name for step in steps] == ["thunderbolt-hold", "remove-endpoint", "program-volatile", "pci-rescan", "lspci-endpoint"]
    assert steps[1].argv == (
        "sh",
        "-c",
        "test ! -e /sys/bus/pci/devices/0000:04:00.0/remove || echo 1 > /sys/bus/pci/devices/0000:04:00.0/remove",
    )
    assert steps[2].argv == ("openFPGALoader", "-c", "digilent_hs2", "/repo/projects/vivado-shell/project.runs/impl_1/Top_wrapper.bit")
    assert steps[3].argv == ("sh", "-c", EXPECTED_PCI_RESCAN_SCRIPT)
    assert steps[4].argv[0:2] == ("sh", "-c")
    assert EXPECTED_LSPCI_ENDPOINT_SNIPPET in steps[4].argv[2]
    assert EXPECTED_LSPCI_ENDPOINT_RETRY_SNIPPET in steps[4].argv[2]
    assert "expected PCI endpoint 10ee:7011 after rescan" in steps[4].argv[2]


def test_build_and_program_plan_names_vivado_jtag_and_pcie_steps() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"), vivado_executable="vivado2025.1")

    steps = build_and_program_plan(config)

    assert [step.name for step in steps] == [
        "thunderbolt-hold",
        "vivado-build",
        "jtag-detect",
        "program-volatile",
        "pci-rescan",
        "lspci-endpoint",
    ]
    assert steps[0].argv == (
        "dau-pci-runtime-pm",
        "hold",
        "--pattern",
        "Thunderbolt",
        "--pattern",
        "JHL",
        "--pattern",
        "10ee:7011",
        "--pattern",
        "Xilinx",
    )
    assert steps[1].argv == ("vivado2025.1", "-mode", "batch", "-source", "/repo/projects/vivado-shell/project.tcl")
    assert steps[2].argv == ("openFPGALoader", "-c", "digilent_hs2", "--detect")
    assert steps[3].command_line == "openFPGALoader -c digilent_hs2 /repo/projects/vivado-shell/project.runs/impl_1/Top_wrapper.bit"


def test_thunderbolt_power_plans_hold_and_release_runtime_pm() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"))

    hold_step = thunderbolt_hold_plan(config)[0]
    release_step = thunderbolt_release_plan(config)[0]

    assert hold_step.name == "thunderbolt-hold"
    assert hold_step.argv == (
        "dau-pci-runtime-pm",
        "hold",
        "--pattern",
        "Thunderbolt",
        "--pattern",
        "JHL",
        "--pattern",
        "10ee:7011",
        "--pattern",
        "Xilinx",
    )
    assert release_step.name == "thunderbolt-release"
    assert release_step.argv == (
        "dau-pci-runtime-pm",
        "release",
        "--pattern",
        "Thunderbolt",
        "--pattern",
        "JHL",
        "--pattern",
        "10ee:7011",
        "--pattern",
        "Xilinx",
    )


def test_cli_prints_recovery_plan_without_running_privileged_commands(capsys) -> None:
    exit_code = main(["recovery", "--work-root", "/repo/projects/vivado-shell"])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "thunderbolt-hold\tdau-pci-runtime-pm hold --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx"
    assert lines[1:4] == [
        "remove-endpoint\tsh -c 'test ! -e /sys/bus/pci/devices/0000:04:00.0/remove || echo 1 > /sys/bus/pci/devices/0000:04:00.0/remove'",
        "program-volatile\topenFPGALoader -c digilent_hs2 /repo/projects/vivado-shell/project.runs/impl_1/Top_wrapper.bit",
        f"pci-rescan\tsh -c '{EXPECTED_PCI_RESCAN_SCRIPT}'",
    ]
    assert lines[4].startswith("lspci-endpoint\tsh -c ")
    assert EXPECTED_LSPCI_ENDPOINT_SNIPPET in lines[4]


def test_cli_prints_thunderbolt_release_plan(capsys) -> None:
    exit_code = main(["thunderbolt-release", "--work-root", "/repo/projects/vivado-shell"])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0] == "thunderbolt-release\tdau-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx"


def test_module_entrypoint_prints_plan_for_uninstalled_checkout(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vivado-xdma", "thunderbolt-release", "--work-root", "/repo/projects/vivado-shell"])
    monkeypatch.delitem(sys.modules, "dau_build.hardware_plan", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("dau_build.hardware_plan", run_name="__main__")

    assert exc_info.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["thunderbolt-release\tdau-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx"]


def test_local_build_and_program_plan_stages_overlay_programs_and_runs_hardware_smoke() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"), vivado_executable="vivado")

    steps = local_build_and_program_plan(
        config,
        dau_core_root=Path("/repo/dau-core"),
        dau_driver_root=Path("/repo/dau-driver"),
    )

    assert [step.name for step in steps] == [
        "thunderbolt-hold",
        "write-dau-overlay",
        "write-vivado-build-script",
        "vivado-overlay-build",
        "jtag-detect",
        "remove-endpoint",
        "program-volatile",
        "pci-rescan",
        "lspci-endpoint",
        "driver-hardware-smoke",
        "thunderbolt-release",
    ]
    assert "base64 -d > /repo/projects/vivado-shell/scripts/dau_overlay.tcl" in steps[1].argv[2]
    assert "base64 -d > /repo/projects/vivado-shell/scripts/dau_build.tcl" in steps[2].argv[2]
    assert steps[3].argv[0:2] == ("bash", "-lc")
    assert "cd /repo/projects/vivado-shell" in steps[3].argv[2]
    assert ". /opt/Xilinx/2025.1/Vivado/settings64.sh" in steps[3].argv[2]
    assert "vivado -mode batch -source project.tcl" not in steps[3].argv[2]
    assert "vivado -mode batch -source scripts/dau_overlay.tcl" in steps[3].argv[2]
    assert "rm -f Top.v" in steps[3].argv[2]
    assert "vivado -mode batch -source scripts/dau_build.tcl" in steps[3].argv[2]
    assert "scripts/build.tcl" not in steps[3].argv[2]
    assert EXPECTED_PCI_RESCAN_SCRIPT in steps[7].argv[2]
    assert EXPECTED_LSPCI_ENDPOINT_SNIPPET in steps[8].argv[2]
    assert steps[9].argv[0] == "sh"
    assert "PYTHONPATH=/repo/dau-core:/repo/dau-driver python3 -c" in steps[9].argv[2]
    assert "discover_devices" in steps[9].argv[2]
    assert "DAU_MAGIC_WORD" in steps[9].argv[2]


def test_local_build_and_program_plan_can_stage_shell_seed_before_overlay() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/dau-build/outputs/vivado"), vivado_executable="vivado")

    steps = local_build_and_program_plan(
        config,
        source_shell_root=Path("/repo/reference/vivado-shell"),
        dau_core_root=Path("/repo/dau-core"),
        dau_driver_root=Path("/repo/dau-driver"),
    )

    assert [step.name for step in steps][0:5] == [
        "stage-shell",
        "thunderbolt-hold",
        "write-dau-overlay",
        "write-vivado-build-script",
        "vivado-overlay-build",
    ]
    assert "/repo/reference/vivado-shell/ /repo/dau-build/outputs/vivado/" in steps[0].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/scripts/dau_overlay.tcl" in steps[2].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/scripts/dau_build.tcl" in steps[3].argv[2]
    assert "cd /repo/dau-build/outputs/vivado" in steps[4].argv[2]


def test_validate_bitstream_plan_programs_existing_bitstream_without_vivado() -> None:
    config = HardwareToolchainConfig(
        work_root=Path("/repo/projects/vivado-shell"),
        bitstream_path=Path("artifacts/candidate.bit"),
        vivado_executable="vivado",
    )

    steps = validate_bitstream_plan(
        config,
        dau_core_root=Path("/repo/dau-core"),
        dau_driver_root=Path("/repo/dau-driver"),
    )

    assert [step.name for step in steps] == [
        "thunderbolt-hold",
        "jtag-detect",
        "remove-endpoint",
        "program-volatile",
        "pci-rescan",
        "lspci-endpoint",
        "driver-hardware-smoke",
        "thunderbolt-release",
    ]
    assert steps[3].argv == ("openFPGALoader", "-c", "digilent_hs2", "/repo/projects/vivado-shell/artifacts/candidate.bit")
    assert all(step.name != "vivado-overlay-build" for step in steps)
    assert all(step.argv[0] != "vivado" for step in steps)
    assert all("dau_overlay" not in step.command_line for step in steps)


def test_stage_vivado_overlay_plan_writes_backend_artifacts_without_running_vivado() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"), vivado_executable="vivado")

    steps = stage_vivado_overlay_plan(
        config,
        dau_core_root=Path("/repo/dau-core"),
    )

    assert [step.name for step in steps] == ["write-dau-overlay", "write-dau-manifest", "write-vivado-build-script", "write-vivado-command-plan"]
    assert steps[0].argv[0:2] == ("sh", "-c")
    assert "base64 -d > /repo/projects/vivado-shell/scripts/dau_overlay.tcl" in steps[0].argv[2]
    assert "base64 -d > /repo/projects/vivado-shell/dau-vivado.manifest" in steps[1].argv[2]
    assert "base64 -d > /repo/projects/vivado-shell/scripts/dau_build.tcl" in steps[2].argv[2]
    assert "base64 -d > /repo/projects/vivado-shell/dau-vivado.plan" in steps[3].argv[2]
    assert all(not step.name.startswith("vivado") for step in steps)


def test_stage_vivado_overlay_plan_can_stage_shell_seed_into_workdir() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/dau-build/outputs/vivado"), vivado_executable="vivado")

    steps = stage_vivado_overlay_plan(
        config,
        source_shell_root=Path("/repo/reference/vivado-shell"),
        dau_core_root=Path("/repo/dau-core"),
    )

    assert [step.name for step in steps] == [
        "stage-shell",
        "write-dau-overlay",
        "write-dau-manifest",
        "write-vivado-build-script",
        "write-vivado-command-plan",
    ]
    assert "/repo/reference/vivado-shell/ /repo/dau-build/outputs/vivado/" in steps[0].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/scripts/dau_overlay.tcl" in steps[1].argv[2]


def test_stage_vivado_overlay_plan_emits_structured_backend_artifacts() -> None:
    config = HardwareToolchainConfig(
        work_root=Path("/repo/dau-build/outputs/vivado"),
        bitstream_path=Path("artifacts/dau-ci.bit"),
        vivado_executable="vivado2025.1",
    )

    steps = stage_vivado_overlay_plan(
        config,
        dau_core_root=Path("/repo/dau-core"),
        artifact_stem="dau-ci",
        platform="vivado-xdma",
        shell="seeded-xdma",
        operator_set=("identity", "sum_i64"),
        stream_protocol_version="0.2",
        vivado_settings=Path("/tools/Vivado/settings64.sh"),
    )

    assert [step.name for step in steps] == ["write-dau-overlay", "write-dau-manifest", "write-vivado-build-script", "write-vivado-command-plan"]
    assert "base64 -d > /repo/dau-build/outputs/vivado/dau-ci.manifest" in steps[1].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/scripts/dau_build.tcl" in steps[2].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/dau-ci.plan" in steps[3].argv[2]
    manifest = dict(line.split("=", 1) for line in _decode_write_text_step_source(steps[1]).splitlines())
    assert manifest["platform"] == "vivado-xdma"
    assert manifest["shell"] == "seeded-xdma"
    assert manifest["operator_set"] == "identity,sum_i64"
    assert manifest["stream_protocol_version"] == "0.2"
    assert manifest["bitstream"] == "artifacts/dau-ci.bit"
    assert manifest["manifest"] == "dau-ci.manifest"
    assert manifest["command_plan"] == "dau-ci.plan"
    assert manifest["build_tcl"] == "scripts/dau_build.tcl"
    build_tcl = _decode_write_text_step_source(steps[2])
    assert "skipping missing PCIe lane cell" in build_tcl
    assert "file copy -force $default_bitstream_path $expected_bitstream_path" in build_tcl
    assert 'report_utilization -file "reports/dau_utilization.rpt"' in build_tcl
    assert 'report_timing_summary -file "reports/dau_timing_summary.rpt"' in build_tcl
    assert 'puts $manifest_file "build_status=built"' in build_tcl
    command_plan = _decode_write_text_step_source(steps[3])
    assert "cd /repo/dau-build/outputs/vivado" in command_plan
    assert "vivado2025.1 -mode batch -source scripts/dau_overlay.tcl" in command_plan
    assert "vivado2025.1 -mode batch -source scripts/dau_build.tcl" in command_plan
    assert "scripts/build.tcl" not in command_plan


def test_stage_vivado_project_plan_writes_project_and_backend_artifacts_without_vivado() -> None:
    config = HardwareToolchainConfig(
        work_root=Path("/repo/dau-build/outputs/vivado"),
        bitstream_path=Path("artifacts/dau-ci.bit"),
        vivado_executable="vivado2025.1",
    )

    steps = stage_vivado_project_plan(
        config,
        source_shell_root=Path("/repo/projects/vivado-shell"),
        dau_core_root=Path("/repo/dau-core"),
        dau_driver_root=Path("/repo/dau-driver"),
        dau_utils_root=Path("/repo/dau-utils"),
        artifact_stem="dau-ci",
        platform="vivado-xdma",
        shell="seeded-xdma",
        operator_set=("identity", "sum_i64"),
        stream_protocol_version="0.2",
        overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
        vivado_settings=Path("/tools/Vivado/settings64.sh"),
    )

    assert [step.name for step in steps] == [
        "stage-shell",
        "write-vivado-project-manifest",
        "write-dau-overlay",
        "write-dau-manifest",
        "write-vivado-build-script",
        "write-vivado-command-plan",
    ]
    assert "/repo/projects/vivado-shell/ /repo/dau-build/outputs/vivado/" in steps[0].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/dau-ci.project" in steps[1].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/scripts/dau_ci_overlay.tcl" in steps[2].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/dau-ci.manifest" in steps[3].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/scripts/dau_build.tcl" in steps[4].argv[2]
    assert "base64 -d > /repo/dau-build/outputs/vivado/dau-ci.plan" in steps[5].argv[2]
    project_manifest = dict(line.split("=", 1) for line in _decode_write_text_step_source(steps[1]).splitlines())
    assert project_manifest["source_shell_root"] == "/repo/projects/vivado-shell"
    assert project_manifest["work_root"] == "/repo/dau-build/outputs/vivado"
    assert project_manifest["backend_manifest"] == "dau-ci.manifest"
    assert project_manifest["backend_command_plan"] == "dau-ci.plan"
    assert "stage-vivado-overlay" in project_manifest["stage_command"]
    assert "build-vivado-artifacts" in project_manifest["build_command"]
    assert "validate-vivado-artifacts" in project_manifest["validate_command"]
    assert all(not step.name.startswith("vivado") for step in steps)


def test_dau_overlay_manifest_records_backend_contract() -> None:
    manifest = dict(dau_overlay_manifest(Path("/repo/dau-core/dau_core/hdl")))

    assert {
        "backend": "dau_build.vivado_backend.vivado_overlay",
        "dau_identity_registers_sv": "/repo/dau-core/dau_core/hdl/dau_identity_registers.sv",
        "dau_identity_axil_v": "/repo/dau-core/dau_core/hdl/dau_identity_axil.v",
        "dau_identity_axil_sv_legacy": "/repo/dau-core/dau_core/hdl/dau_identity_axil.sv",
        "dau_identity_axil_cell": "dau_identity_axil_0",
        "spi_ss_i_tieoff": "dau_spi_ss_i_tieoff",
        "register_window_offset": "0x00001000",
        "overlay": "scripts/dau_overlay.tcl",
        "bitstream": "project.runs/impl_1/Top_wrapper.bit",
    }.items() <= manifest.items()
    assert manifest["job_control_offset"] == "0x00000050"
    assert manifest["job_status_offset"] == "0x00000054"
    assert manifest["input_buffer_address"] == "0x0000000000000000"
    assert manifest["output_buffer_address"] == "0x0000000000100000"
    assert dau_overlay_manifest_text(Path("/repo/dau-core/dau_core/hdl")).splitlines()[0] == "backend=dau_build.vivado_backend.vivado_overlay"


def test_local_flash_plan_uses_vivado_flash_script_inside_runtime_pm_session() -> None:
    config = HardwareToolchainConfig(work_root=Path("/repo/projects/vivado-shell"), vivado_executable="vivado")

    steps = flash_plan(config)

    assert [step.name for step in steps] == ["thunderbolt-hold", "flash", "thunderbolt-release"]
    assert steps[1].argv[0:2] == ("bash", "-lc")
    assert "cd /repo/projects/vivado-shell" in steps[1].argv[2]
    assert ". /opt/Xilinx/2025.1/Vivado/settings64.sh" in steps[1].argv[2]
    assert "vivado -mode batch -source scripts/flash.tcl" in steps[1].argv[2]


def test_cli_local_build_can_stage_shell_before_overlay(capsys) -> None:
    exit_code = main(
        [
            "local-build-and-program",
            "--source-shell-root",
            "/repo/reference/vivado-shell",
            "--work-root",
            "/repo/dau-build/outputs/vivado",
            "--dau-core-root",
            "/repo/dau-core",
            "--dau-driver-root",
            "/repo/dau-driver",
        ]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 12
    assert lines[0].startswith("stage-shell\tsh -c ")
    assert lines[2].startswith("write-dau-overlay\tsh -c ")
    assert lines[3].startswith("write-vivado-build-script\tsh -c ")


def test_cli_prints_local_build_and_program_plan(capsys) -> None:
    exit_code = main(
        [
            "local-build-and-program",
            "--work-root",
            "/repo/projects/vivado-shell",
            "--dau-core-root",
            "/repo/dau-core",
            "--dau-driver-root",
            "/repo/dau-driver",
        ]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 11
    assert lines[0].startswith("thunderbolt-hold\tdau-pci-runtime-pm hold ")
    assert lines[1].startswith("write-dau-overlay\tsh -c ")
    assert lines[2].startswith("write-vivado-build-script\tsh -c ")
    assert lines[3].startswith("vivado-overlay-build\tbash -lc ")
    assert lines[9].startswith("driver-hardware-smoke\tsh -c ")
    assert lines[10].startswith("thunderbolt-release\tdau-pci-runtime-pm release ")


def test_cli_prints_validate_bitstream_plan_without_vivado(capsys) -> None:
    exit_code = main(
        [
            "validate-bitstream",
            "--work-root",
            "/repo/projects/vivado-shell",
            "--bitstream",
            "/tmp/candidate.bit",
            "--dau-core-root",
            "/repo/dau-core",
            "--dau-driver-root",
            "/repo/dau-driver",
        ]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 8
    assert lines[0].startswith("thunderbolt-hold\tdau-pci-runtime-pm hold ")
    assert lines[3] == "program-volatile\topenFPGALoader -c digilent_hs2 /tmp/candidate.bit"
    assert lines[6].startswith("driver-hardware-smoke\tsh -c ")
    assert lines[7].startswith("thunderbolt-release\tdau-pci-runtime-pm release ")
    assert all("vivado" not in line.lower() for line in lines)


def test_cli_execute_runs_plan_steps_in_order(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0

    def fake_run(argv):
        calls.append(tuple(argv))
        return Completed()

    monkeypatch.setattr(hardware_plan.subprocess, "run", fake_run)

    exit_code = main(["thunderbolt-release", "--work-root", "/repo/projects/vivado-shell", "--execute"])

    assert exit_code == 0
    assert calls == [
        (
            "dau-pci-runtime-pm",
            "release",
            "--pattern",
            "Thunderbolt",
            "--pattern",
            "JHL",
            "--pattern",
            "10ee:7011",
            "--pattern",
            "Xilinx",
        )
    ]


def test_cli_execute_releases_runtime_pm_after_failed_local_plan_step(monkeypatch) -> None:
    calls = []
    return_codes = [0, 23, 0]

    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def fake_run(argv):
        calls.append(tuple(argv))
        return Completed(return_codes[len(calls) - 1])

    monkeypatch.setattr(hardware_plan.subprocess, "run", fake_run)

    exit_code = main(
        [
            "local-build-and-program",
            "--work-root",
            "/repo/projects/vivado-shell",
            "--dau-core-root",
            "/repo/dau-core",
            "--dau-driver-root",
            "/repo/dau-driver",
            "--execute",
        ]
    )

    assert exit_code == 23
    assert len(calls) == 3
    assert calls[0][1] == "hold"
    assert calls[1][0:2] == ("sh", "-c")
    assert calls[2][1] == "release"


def test_dau_overlay_tcl_imports_core_identity_hdl_and_writes_manifest(tmp_path: Path) -> None:
    source = dau_overlay_tcl(Path("/repo/dau-core/dau_core/hdl"))

    assert 'set dau_identity_registers_sv [file normalize "/repo/dau-core/dau_core/hdl/dau_identity_registers.sv"]' in source
    assert 'set dau_identity_axil_v [file normalize "/repo/dau-core/dau_core/hdl/dau_identity_axil.v"]' in source
    assert 'set dau_identity_axil_sv_legacy [file normalize "/repo/dau-core/dau_core/hdl/dau_identity_axil.sv"]' in source
    assert "set stale_dau_axil_source [get_files -quiet $dau_identity_axil_sv_legacy]" in source
    assert "remove_files $stale_dau_axil_source" in source
    assert "set locked_dau_ips [get_ips -quiet -filter {IS_LOCKED == 1}]" in source
    assert "upgrade_ip $locked_dau_ips" in source
    assert "foreach dau_hdl_source [list $dau_identity_registers_sv $dau_identity_axil_v]" in source
    assert "add_files -norecurse -fileset sources_1 $dau_hdl_source" in source
    assert "set_property file_type SystemVerilog [get_files $dau_identity_registers_sv]" in source
    assert "set_property file_type Verilog [get_files $dau_identity_axil_v]" in source
    assert 'puts $manifest_file "dau_identity_registers_sv=$dau_identity_registers_sv"' in source
    assert 'puts $manifest_file "dau_identity_axil_v=$dau_identity_axil_v"' in source
    assert "set dau_identity_ooc_runs [get_runs -quiet *dau_identity_axil*]" in source
    assert "reset_run $dau_identity_ooc_runs" in source
    assert 'puts $manifest_file "dau_identity_ooc_runs=$dau_identity_ooc_runs"' in source
    assert "save_project" not in source

    overlay_path = write_dau_overlay_tcl(tmp_path / "scripts" / "dau_overlay.tcl", dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"))

    assert overlay_path == tmp_path / "scripts" / "dau_overlay.tcl"
    assert overlay_path.read_text() == source


def test_dau_overlay_tcl_wires_axi_lite_identity_block_at_existing_register_window() -> None:
    source = dau_overlay_tcl(Path("/repo/dau-core/dau_core/hdl"))

    assert "open_bd_design [get_files project.srcs/sources_1/bd/Top/Top.bd]" in source
    assert "foreach cell_name {dau_identity_axil_0 axi_gpio_0 Model Version}" in source
    assert "delete_bd_objs $old_cell" in source
    assert "create_bd_cell -type module -reference dau_identity_axil dau_identity_axil_0" in source
    assert "connect_bd_intf_net -intf_net axi_interconnect_0_M00_AXI" in source
    assert "[get_bd_intf_pins axi_interconnect_0/M00_AXI] [get_bd_intf_pins dau_identity_axil_0/S_AXI]" in source
    assert "connect_bd_net -net S00_ACLK_1 [get_bd_pins xdma_0/axi_aclk] [get_bd_pins dau_identity_axil_0/s_axi_aclk]" in source
    assert "connect_bd_net -net S00_ARESETN_1 [get_bd_pins xdma_0/axi_aresetn] [get_bd_pins dau_identity_axil_0/s_axi_aresetn]" in source
    assert "assign_bd_address -offset 0x00001000 -range 0x00001000" in source
    assert "dau_identity_axil_0/S_AXI" in source


def test_dau_overlay_tcl_regenerates_wrapper_and_pins_top_module() -> None:
    source = dau_overlay_tcl(Path("/repo/dau-core/dau_core/hdl"))

    assert "set wrapper_path [make_wrapper -files [get_files project.srcs/sources_1/bd/Top/Top.bd] -top]" in source
    assert "add_files -norecurse -fileset sources_1 $wrapper_path" in source
    assert 'set_property -name "top" -value "Top_wrapper" -objects [get_filesets sources_1]' in source
    assert "update_compile_order -fileset sources_1" in source


def test_dau_overlay_tcl_ties_off_upgraded_quad_spi_ss_input() -> None:
    source = dau_overlay_tcl(Path("/repo/dau-core/dau_core/hdl"))

    assert "set spi_ss_i_pin [get_bd_pins -quiet axi_quad_spi_0/ss_i]" in source
    assert "create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 dau_spi_ss_i_tieoff" in source
    assert "CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}" in source
    assert "connect_bd_net -net dau_spi_ss_i_tieoff_dout" in source
    assert 'puts $manifest_file "spi_ss_i_tieoff=dau_spi_ss_i_tieoff"' in source
