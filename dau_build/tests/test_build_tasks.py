from __future__ import annotations

from pathlib import Path

import pytest
import tomllib
from ccflow import CallableModel

from dau_build.build_steps import BuildStepError, BuildStepResult, SimulateTask, execute_override_request, execute_override_task
from dau_build.cli import main
from dau_build.config import run_request_config
from dau_build.vivado_backend import VivadoBackendArtifactValidation, VivadoBackendRequest, generate_vivado_backend_artifacts

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_execute_override_request_accepts_public_task_simulate_surface(tmp_path: Path) -> None:
    assert issubclass(SimulateTask, CallableModel)

    spec_path = _write_spec(tmp_path)

    # default simulator is svparser (no simulator= group override)
    result = execute_override_request(("task=tasks/sim/simulate", "module=dau_identity_top", f"spec_path={spec_path}"))

    assert result == BuildStepResult(
        step="simulate",
        message=f"dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec={spec_path} status=validated",
    )


def test_execute_override_task_accepts_public_cocotb_simulate_surface(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    # the simulator is the composed simulator group
    result = run_request_config(
        "task",
        "tasks/sim/simulate",
        overrides=["simulator=simulators/cocotb"],
        model_values={"module": "dau_identity_top", "spec_path": str(spec_path)},
    )

    assert result == BuildStepResult(
        step="simulate",
        message=f"dau-build-simulate\ttask=simulate simulator=cocotb module=dau_identity_top spec={spec_path} status=validated",
    )


def test_execute_override_task_requires_selected_module_to_match_spec(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    with pytest.raises(BuildStepError, match="module 'missing' is not provided by spec"):
        execute_override_task(("task=tasks/sim/simulate", "module=missing", f"spec_path={spec_path}"))


def test_spec_tasks_inspect_build_and_validate_a_bundle(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    output_root = tmp_path / "artifacts"

    inspect = execute_override_task(("task=tasks/spec/inspect", f"spec_path={spec_path}"))
    assert inspect.step == "inspect"
    assert "name=identity-pipeline" in inspect.message

    build = execute_override_task(("task=tasks/spec/build", f"spec_path={spec_path}", f"output_root={output_root}"))
    manifest_path = output_root / "dau-identity.manifest"
    assert build == BuildStepResult(
        step="build",
        message=f"dau-build-artifacts\tmanifest={manifest_path} top_sv={output_root / 'generated' / 'dau_identity_top.sv'}",
    )
    assert manifest_path.is_file()

    # the generated bundle validates through the same task (no subcommand)
    validated = execute_override_task(("task=tasks/spec/validate", f"manifest_path={manifest_path}", f"root={output_root}"))
    assert validated.step == "validate"
    assert f"dau-build-artifacts-valid\tmanifest={manifest_path}" in validated.message


def test_execute_override_task_maps_synthesize_engine_to_backend_handoff(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    output_root = tmp_path / "out"

    result = execute_override_task(
        (
            "task=tasks/build/synthesize",
            "module=dau_identity_top",
            f"spec_path={spec_path}",
            f"output_root={output_root}",
        )
    )

    assert result.step == "synthesize"
    assert result.message.startswith(
        f"dau-build-synthesize\ttask=synthesize engine=vivado module=dau_identity_top spec={spec_path} "
        f"output_root={output_root} manifest={output_root / 'dau-identity.manifest'} "
        f"top_sv={output_root / 'generated' / 'dau_identity_top.sv'} "
    )
    assert f"backend_manifest={output_root / 'vivado' / 'dau-identity.manifest'}" in result.message
    assert f"command_plan={output_root / 'vivado' / 'dau-identity.plan'}" in result.message
    assert result.message.endswith("status=handoff-written")
    assert (output_root / "generated" / "dau_identity_top.sv").is_file()
    assert (output_root / "dau-identity.manifest").is_file()
    assert (output_root / "vivado" / "dau-identity.manifest").is_file()


def test_synthesize_vivado_consumes_arrow_lite_aggregator_bundle_for_flash_and_smoke_tasks(tmp_path: Path) -> None:
    spec_path = _write_arrow_lite_aggregator_spec(tmp_path)
    output_root = tmp_path / "out"

    synthesize_result = execute_override_task(
        (
            "task=tasks/build/synthesize",
            "module=stream_doubler",
            f"spec_path={spec_path}",
            f"output_root={output_root}",
        )
    )

    backend_manifest_path = output_root / "vivado" / "dau-int32-arrow-lite.manifest"
    backend_manifest = _read_manifest(backend_manifest_path)
    assert synthesize_result.step == "synthesize"
    assert f"backend_manifest={backend_manifest_path}" in synthesize_result.message
    assert backend_manifest["dau_artifact_bundle"] == (output_root / "dau-int32-arrow-lite.artifacts.yaml").resolve().as_posix()
    assert backend_manifest["dau_generated_top"] == (output_root / "generated" / "dau_int32_arrow_lite_top.sv").resolve().as_posix()
    assert (output_root / "generated" / "dau_int32_arrow_lite_top.sv").resolve().as_posix() in backend_manifest["dau_bundle_hdl_sources"]
    assert (Path(__file__).parent / "sv" / "stream_doubler.sv").resolve().as_posix() in backend_manifest["dau_bundle_hdl_sources"]
    assert backend_manifest["selected_module"] == "stream_doubler"
    assert backend_manifest["job_control_offset"] == "0x00000050"
    assert backend_manifest["job_status_offset"] == "0x00000054"
    assert backend_manifest["input_buffer_address"] == "0x0000000000000000"
    assert backend_manifest["output_buffer_address"] == "0x0000000000100000"

    bitstream = _write_built_backend_outputs(output_root / "vivado", backend_manifest_path)
    flash_result = execute_override_task(("task=tasks/flash/flash", f"manifest_path={backend_manifest_path}"))
    smoke_result = execute_override_task(("task=tasks/flash/smoke-test", "test=aggregation", f"manifest_path={backend_manifest_path}"))

    assert flash_result == BuildStepResult(
        step="flash",
        message=f"dau-build-flash\ttask=flash programmer=openfpgaloader bitstream={bitstream} manifest={backend_manifest_path} mode=volatile status=planned",
    )
    assert smoke_result == BuildStepResult(
        step="smoke-test",
        message=(
            f"dau-build-smoke-test\ttask=smoke-test test=aggregation manifest={backend_manifest_path} "
            "register_window_offset=0x00001000 input_buffer=0x0000000000000000 output_buffer=0x0000000000100000 status=planned"
        ),
    )


def test_manifest_driven_flash_rejects_planned_backend_manifest(tmp_path: Path) -> None:
    spec_path = _write_arrow_lite_aggregator_spec(tmp_path)
    output_root = tmp_path / "out"
    execute_override_task(
        (
            "task=tasks/build/synthesize",
            "module=stream_doubler",
            f"spec_path={spec_path}",
            f"output_root={output_root}",
        )
    )

    backend_manifest_path = output_root / "vivado" / "dau-int32-arrow-lite.manifest"

    with pytest.raises(BuildStepError, match="is not built: build_status=planned; expected built"):
        execute_override_task(("task=tasks/flash/flash", f"manifest_path={backend_manifest_path}"))


def test_manifest_driven_smoke_rejects_incomplete_built_backend_manifest(tmp_path: Path) -> None:
    spec_path = _write_arrow_lite_aggregator_spec(tmp_path)
    output_root = tmp_path / "out"
    execute_override_task(
        (
            "task=tasks/build/synthesize",
            "module=stream_doubler",
            f"spec_path={spec_path}",
            f"output_root={output_root}",
        )
    )

    backend_manifest_path = output_root / "vivado" / "dau-int32-arrow-lite.manifest"
    backend_manifest_path.write_text(
        backend_manifest_path.read_text(encoding="utf-8").replace("build_status=planned", "build_status=built"),
        encoding="utf-8",
    )

    with pytest.raises(BuildStepError, match="is built but incomplete: missing bitstream:"):
        execute_override_task(("task=tasks/flash/smoke-test", "test=aggregation", f"manifest_path={backend_manifest_path}"))


def test_execute_override_task_plans_openfpgaloader_flash(tmp_path: Path) -> None:
    bitstream = tmp_path / "Top_wrapper.bit"
    bitstream.write_bytes(b"bit")

    # default programmer (openFPGALoader)
    result = execute_override_task(("task=tasks/flash/flash", f"bitstream={bitstream}"))

    assert result == BuildStepResult(
        step="flash",
        message=f"dau-build-flash\ttask=flash programmer=openfpgaloader bitstream={bitstream} mode=volatile status=planned",
    )


def test_flash_task_composes_the_programmer_group(tmp_path: Path) -> None:
    from dau_build.config import run_request_config

    bitstream = tmp_path / "Top_wrapper.bit"
    bitstream.write_bytes(b"bit")

    # programmer=programmers/<name> composes the adapter into the flash task
    result = run_request_config(
        "task",
        "tasks/flash/flash",
        overrides=["programmer=programmers/vivado-hwserver"],
        model_values={"bitstream": bitstream},
    )
    assert result == BuildStepResult(
        step="flash",
        message=f"dau-build-flash\ttask=flash programmer=vivado-hwserver bitstream={bitstream} mode=volatile status=planned",
    )


def _write_minimal_built_backend_manifest(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Top_wrapper.bit").write_bytes(b"bit")
    for name in ("dau_utilization.rpt", "dau_timing_summary.rpt", "vivado.log"):
        (root / name).write_text("built\n", encoding="utf-8")
    manifest_path = root / "dau-vivado.manifest"
    manifest_path.write_text(
        "bitstream=Top_wrapper.bit\n"
        "resource_summary=dau_utilization.rpt\n"
        "timing_summary=dau_timing_summary.rpt\n"
        "vivado_log=vivado.log\n"
        "build_status=built\n",
        encoding="utf-8",
    )
    return manifest_path


def test_flash_refuses_backend_manifest_without_packaged_provenance(tmp_path: Path) -> None:
    # a key=value backend manifest alone carries no digests: flash must
    # demand the packaged artlink manifest the validate step writes
    manifest_path = _write_minimal_built_backend_manifest(tmp_path)

    with pytest.raises(BuildStepError, match="no packaged artlink manifest"):
        execute_override_task(("task=tasks/flash/flash", f"manifest_path={manifest_path}"))


def test_flash_refuses_backend_manifest_bitstream_digest_mismatch(tmp_path: Path) -> None:
    from dau_build.shell_build import write_overlay_build_manifest

    manifest_path = _write_minimal_built_backend_manifest(tmp_path)
    write_overlay_build_manifest(tmp_path, manifest_path, name="dau-vivado")
    (tmp_path / "Top_wrapper.bit").write_bytes(b"replaced-after-build")

    with pytest.raises(BuildStepError, match="digest mismatch"):
        execute_override_task(("task=tasks/flash/flash", f"manifest_path={manifest_path}"))


def test_flash_refuses_packaged_manifest_without_bitstream_digest(tmp_path: Path) -> None:
    # digest is optional in the artlink model: a packaged manifest whose
    # bitstream artifact carries none must be refused, not silently trusted
    import yaml

    from dau_build.shell_build import write_overlay_build_manifest

    manifest_path = _write_minimal_built_backend_manifest(tmp_path)
    packaged = write_overlay_build_manifest(tmp_path, manifest_path, name="dau-vivado")
    data = yaml.safe_load(packaged.read_text(encoding="utf-8"))
    for artifact in data["artifacts"]:
        artifact.pop("digest", None)
    packaged.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(BuildStepError, match="no digest"):
        execute_override_task(("task=tasks/flash/flash", f"manifest_path={manifest_path}"))


def test_execute_override_task_plans_identity_smoke_test() -> None:
    result = execute_override_task(("task=tasks/flash/smoke-test", "test=identity"))

    assert result == BuildStepResult(
        step="smoke-test",
        message="dau-build-smoke-test\ttask=smoke-test test=identity status=planned",
    )


def test_hardware_plan_task_via_plan_group() -> None:
    # the plan is the composed plan group: plan=plans/thunderbolt-release;
    # host access (the runtime-PM patterns) composes from the platform group
    result = run_request_config(
        "task",
        "tasks/hardware/hardware-plan",
        overrides=["plan=plans/thunderbolt-release", "platform=platforms/dau/dpv1"],
        model_values={"work_root": "/repo/projects/vivado-shell"},
    )

    assert result == BuildStepResult(
        step="hardware-plan",
        message="thunderbolt-release\tdau-utils-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx",
    )


def test_execute_override_task_accepts_stage_shell_surface() -> None:
    result = execute_override_task(
        (
            "task=tasks/stage/stage-shell",
            "source_shell_root=/repo/reference/vivado-shell",
            "work_root=/repo/dau-build/outputs/vivado",
        )
    )

    assert result.step == "stage-shell"
    assert result.message.startswith("stage-shell\tsh -c ")
    assert "/repo/reference/vivado-shell/ /repo/dau-build/outputs/vivado/" in result.message


def test_execute_override_task_accepts_stage_vivado_overlay_surface() -> None:
    result = execute_override_task(
        (
            "task=tasks/stage/stage-vivado-overlay",
            "work_root=/repo/projects/vivado-shell",
            "dau_core_root=/repo/dau-core",
        )
    )

    lines = result.message.splitlines()
    assert result.step == "stage-vivado-overlay"
    assert len(lines) == 4
    assert lines[0].startswith("write-dau-overlay\tsh -c ")
    assert lines[1].startswith("write-dau-manifest\tsh -c ")
    assert lines[2].startswith("write-vivado-build-script\tsh -c ")
    assert lines[3].startswith("write-vivado-command-plan\tsh -c ")


def test_execute_override_task_accepts_stage_vivado_project_surface() -> None:
    result = execute_override_task(
        (
            "task=tasks/stage/stage-vivado-project",
            "source_shell_root=/repo/projects/vivado-shell",
            "work_root=/repo/dau-build/outputs/vivado",
            "dau_core_root=/repo/dau-core",
            "dau_driver_root=/repo/dau-driver",
            "dau_utils_root=/repo/dau-utils",
            "artifact_stem=dau-ci",
        )
    )

    lines = result.message.splitlines()
    assert result.step == "stage-vivado-project"
    assert len(lines) == 6
    assert lines[0].startswith("stage-shell\tsh -c ")
    assert lines[1].startswith("write-vivado-project-manifest\tsh -c ")
    assert "dau-ci.project" in lines[1]
    assert lines[2].startswith("write-dau-overlay\tsh -c ")
    assert lines[5].startswith("write-vivado-command-plan\tsh -c ")


def test_execute_override_task_accepts_build_vivado_artifacts_surface() -> None:
    result = execute_override_task(
        (
            "task=tasks/build/build-vivado-artifacts",
            "work_root=/repo/projects/vivado-shell",
            "artifact_stem=dau-ci",
        )
    )

    assert result.step == "build-vivado-artifacts"
    assert result.message.splitlines()[0].startswith("vivado-overlay-build\tbash -lc ")
    assert result.message.splitlines()[1] == (
        "validate-vivado-artifacts\tdau-build task=tasks/validate/validate-vivado-artifacts "
        "work_root=/repo/projects/vivado-shell manifest_path=dau-ci.manifest command_plan_path=dau-ci.plan"
    )


def test_execute_override_task_runs_build_vivado_artifacts_with_graph_models(monkeypatch) -> None:
    calls = {"execute_plan_steps": 0, "validate": 0}

    def fake_execute_plan_steps(steps):
        calls["execute_plan_steps"] += 1
        assert [step.name for step in steps] == ["vivado-overlay-build"]
        return 0

    def fake_validate_vivado_artifacts(config, *, manifest_path, command_plan_path, project_manifest_path):
        calls["validate"] += 1
        assert config.work_root == Path("/repo/projects/vivado-shell")
        assert manifest_path == Path("dau-ci.manifest")
        assert command_plan_path == Path("dau-ci.plan")
        assert project_manifest_path is None
        return VivadoBackendArtifactValidation(
            manifest_path=Path("/repo/projects/vivado-shell/dau-ci.manifest"),
            command_plan_path=Path("/repo/projects/vivado-shell/dau-ci.plan"),
            overlay_tcl_path=Path("/repo/projects/vivado-shell/scripts/dau_overlay.tcl"),
            bitstream_path=Path("/repo/projects/vivado-shell/project.runs/impl_1/Top_wrapper.bit"),
            resource_summary_path=Path("/repo/projects/vivado-shell/reports/dau_utilization.rpt"),
            timing_summary_path=Path("/repo/projects/vivado-shell/reports/dau_timing_summary.rpt"),
            vivado_log_path=Path("/repo/projects/vivado-shell/vivado.log"),
            build_status="built",
            manifest_items=(),
            errors=(),
        )

    monkeypatch.setattr("dau_build.build_steps.execute_plan_steps", fake_execute_plan_steps)
    monkeypatch.setattr("dau_build.build_steps.validate_vivado_artifacts", fake_validate_vivado_artifacts)

    result = execute_override_task(
        (
            "task=tasks/build/build-vivado-artifacts",
            "work_root=/repo/projects/vivado-shell",
            "artifact_stem=dau-ci",
            "execute=true",
        )
    )

    assert calls == {"execute_plan_steps": 1, "validate": 1}
    assert result.step == "build-vivado-artifacts"
    assert result.message.splitlines()[0] == "dau-build-artifacts\ttask=build-vivado-artifacts backend=vivado steps=2 status=executed"
    assert result.message.splitlines()[1] == "dau-build-overlay-build\ttask=overlay-build backend=vivado steps=1 status=executed"
    assert result.message.splitlines()[2].startswith("vivado-artifacts-valid\tmanifest=/repo/projects/vivado-shell/dau-ci.manifest ")


def test_execute_override_task_validates_vivado_artifacts_in_process(tmp_path: Path) -> None:
    _write_backend_artifacts(
        generate_vivado_backend_artifacts(
            VivadoBackendRequest(
                dau_core_hdl_root=Path("/repo/dau-core/dau_core/hdl"),
                build_root=tmp_path,
                artifact_stem="dau-ci",
                overlay_tcl=Path("scripts/dau_ci_overlay.tcl"),
                bitstream_path=Path("artifacts/dau-ci.bit"),
            )
        )
    )

    result = execute_override_task(
        (
            "task=tasks/validate/validate-vivado-artifacts",
            f"work_root={tmp_path}",
            "manifest_path=dau-ci.manifest",
            "command_plan_path=dau-ci.plan",
            "execute=true",
        )
    )

    assert result == BuildStepResult(
        step="validate-vivado-artifacts",
        message=(
            f"vivado-artifacts-valid\tmanifest={tmp_path / 'dau-ci.manifest'} overlay={tmp_path / 'scripts/dau_ci_overlay.tcl'} "
            f"command_plan={tmp_path / 'dau-ci.plan'} bitstream={tmp_path / 'artifacts/dau-ci.bit'} build_status=planned "
            f"resource_summary={tmp_path / 'reports/dau_utilization.rpt'} timing_summary={tmp_path / 'reports/dau_timing_summary.rpt'} "
            f"vivado_log={tmp_path / 'vivado.log'}"
        ),
    )


def test_dau_build_main_dispatches_public_task_arguments(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = main(["task=tasks/sim/simulate", "model.module=dau_identity_top", f"model.spec_path={spec_path}"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        f"dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec={spec_path} status=validated"
    ]


def test_dau_build_cfg_dispatches_hardware_plan_via_group(capsys) -> None:
    from dau_build.cli import main as cfg_main

    exit_code = cfg_main(
        [
            "task=tasks/hardware/hardware-plan",
            "plan=plans/thunderbolt-release",
            "platform=platforms/dau/dpv1",
            "model.work_root=/repo/projects/vivado-shell",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "thunderbolt-release\tdau-utils-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx"
    ]


def test_package_scripts_stay_on_hydra_style_dau_build_entrypoints() -> None:
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert "dau-hardware-plan" not in scripts
    # one CLI: dau-build is the hydra composition entry point. Steps, config
    # explain, and the hydra.main variant are gone (step=/--explain cover them).
    assert scripts == {"dau-build": "dau_build.cli:main"}
    # the config tree is registered on the Hydra search path for extension
    assert pyproject["project"]["entry-points"]["hydra.lernaplugins"]["dau-build"] == "pkg:dau_build.config"


def _write_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: identity-pipeline",
                "top_name: dau_identity_top",
                "platform: vivado-xdma",
                "shell: xdma-ddr",
                "artifact_stem: dau-identity",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - identity",
                "sources:",
                f"  - {(_SV_DIR / 'ff.sv').as_posix()}",
                "modules:",
                "  - ff",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def _write_arrow_lite_aggregator_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "arrow-lite-dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: arrow-lite-aggregation-pipeline",
                "top_name: dau_int32_arrow_lite_top",
                "platform: vivado-xdma",
                "shell: xdma-ddr",
                "artifact_stem: dau-int32-arrow-lite",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: rst",
                "operators:",
                "  - int32-arrow-lite-aggregation",
                "sources:",
                f"  - {(Path(__file__).parent / 'sv' / 'stream_doubler.sv').as_posix()}",
                "modules:",
                "  - stream_doubler",
                "backend: vivado",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def _read_manifest(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def _write_built_backend_outputs(build_root: Path, manifest_path: Path) -> Path:
    manifest = _read_manifest(manifest_path)
    paths = {key: build_root / manifest[key] for key in ("bitstream", "resource_summary", "timing_summary", "vivado_log")}
    paths["bitstream"].parent.mkdir(parents=True, exist_ok=True)
    paths["bitstream"].write_bytes(b"bit")
    for key in ("resource_summary", "timing_summary", "vivado_log"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths[key].write_text("built\n", encoding="utf-8")
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("build_status=planned", "build_status=built"),
        encoding="utf-8",
    )
    # mirror the validate step: package the digested artlink manifest beside
    # the key=value handoff (flash provenance consumes the packaged form)
    from dau_build.shell_build import write_overlay_build_manifest

    write_overlay_build_manifest(build_root, manifest_path, name="dau-vivado")
    return paths["bitstream"]


def _write_backend_artifacts(artifacts) -> None:
    outputs = [
        (artifacts.overlay_tcl_path, artifacts.overlay_tcl_text),
        (artifacts.build_tcl_path, artifacts.build_tcl_text),
        (artifacts.manifest_path, artifacts.manifest_text),
        (artifacts.command_plan_path, artifacts.command_plan_text),
        (artifacts.overlay_driver_tcl_path, artifacts.overlay_driver_tcl_text),
        (artifacts.build_driver_tcl_path, artifacts.build_driver_tcl_text),
    ]
    for path, text in outputs:
        if path is None or text is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_hardware_plan_task_composes_platform_host_access() -> None:
    from dau_build.build_steps import HardwarePlanTask
    from dau_build.hardware_plan import RecoveryPlan
    from dau_build.platforms import PlaceholderPlatformError, dpv1_platform

    explicit = HardwarePlanTask(
        plan=RecoveryPlan(),
        work_root=Path("/repo/projects/vivado-shell"),
        jtag_cable="digilent_hs2",
        endpoint_bdf="0000:04:00.0",
        expected_endpoint_id="10ee:7011",
        runtime_pm_patterns=("Thunderbolt", "JHL", "10ee:7011", "Xilinx"),
        rescan_bdfs=("0000:03:01.0", "0000:02:00.0", "0000:00:0d.3", "0000:00:0d.2", "0000:00:0d.0", "0000:00:07.2", "0000:00:07.0"),
        privilege_prefix=("sudo",),
    )(None)
    composed = HardwarePlanTask(plan=RecoveryPlan(), work_root=Path("/repo/projects/vivado-shell"), platform=dpv1_platform())(None)
    # dpv1's host_access reproduces the explicit bench-fact plan text
    # byte-identically; without either, hardware steps refuse to render
    assert composed == explicit
    with pytest.raises(ValueError, match="runtime_pm_patterns is unset"):
        HardwarePlanTask(plan=RecoveryPlan(), work_root=Path("/repo/projects/vivado-shell"))(None)

    # explicit task fields override the platform's host_access
    overridden = HardwarePlanTask(
        plan=RecoveryPlan(),
        work_root=Path("/repo/projects/vivado-shell"),
        platform=dpv1_platform(),
        jtag_cable="ft4232",
    )(None)
    assert "-c ft4232" in overridden.message

    # a placeholder board is refused for hardware-affecting execution
    placeholder = dpv1_platform().model_copy(update={"name": "probe", "placeholders": ("host_access",)})
    with pytest.raises(PlaceholderPlatformError, match="host_access"):
        HardwarePlanTask(plan=RecoveryPlan(), work_root=Path("/w"), platform=placeholder, execute=True)(None)


def test_host_group_supplies_checkout_roots(tmp_path: Path) -> None:
    from hydra.utils import instantiate

    from dau_build.config import compose_config

    overlay = tmp_path / "user-configs"
    (overlay / "host" / "hosts").mkdir(parents=True)
    (overlay / "host" / "hosts" / "bench.yaml").write_text(
        "# @package host\n"
        "_target_: dau_build.build_config.HostConfig\n"
        "name: bench\n"
        "dau_core_root: /repo/dau-core\n"
        "dau_driver_root: /repo/dau-driver\n"
        "dau_utils_root: /repo/dau-utils\n"
    )
    cfg = compose_config(
        [
            "task=tasks/stage/stage-vivado-project",
            "host=hosts/bench",
            "model.work_root=/repo/outputs/vivado",
            "model.source_shell_root=/repo/reference/vivado-shell",
        ],
        config_dir=str(overlay),
    )
    model = instantiate(cfg.cfg.model)
    assert model.dau_core_root == Path("/repo/dau-core")
    assert model.dau_driver_root == Path("/repo/dau-driver")
    assert model.dau_utils_root == Path("/repo/dau-utils")

    # the composed host also feeds the hardware plans
    plan_cfg = compose_config(["plan=plans/local-build-and-program", "host=hosts/bench"], config_dir=str(overlay))
    plan = instantiate(plan_cfg.cfg.plan)
    assert plan.dau_core_root == Path("/repo/dau-core")
    assert plan.dau_utils_root == Path("/repo/dau-utils")

    # without a host the roots stay unset and the task demands them at call time
    bare = instantiate(
        compose_config(
            [
                "task=tasks/stage/stage-vivado-project",
                "model.work_root=/repo/outputs/vivado",
                "model.source_shell_root=/repo/reference/vivado-shell",
            ]
        ).cfg.model
    )
    with pytest.raises(BuildStepError, match="host=hosts/<name>"):
        bare.stage_steps()


def test_hardware_plan_task_refuses_execution_without_host_access() -> None:
    from dau_build.build_steps import HardwarePlanTask
    from dau_build.hardware_plan import RecoveryPlan
    from dau_build.platforms import dpv1_platform

    # a selected platform must state how the host reaches it; the dpv1
    # defaults are never silently applied to another board
    accessless = dpv1_platform().model_copy(update={"name": "probe", "host_access": None})
    with pytest.raises(BuildStepError, match="declares no host_access"):
        HardwarePlanTask(plan=RecoveryPlan(), work_root=Path("/w"), platform=accessless, execute=True)(None)
    # plan-only composition also needs the facts to render hardware steps
    # (dau-build carries no board defaults to fall back on)
    with pytest.raises(ValueError, match="runtime_pm_patterns is unset"):
        HardwarePlanTask(plan=RecoveryPlan(), work_root=Path("/w"), platform=accessless)(None)


def test_overlay_build_and_validate_tasks_refuse_inert_fields() -> None:
    # bitstream (and the vivado invocation fields on validation) had no effect
    # on these tasks — they are rejected loudly instead of silently dropped
    from pydantic import ValidationError

    from dau_build.build_steps import BuildVivadoArtifactsTask, ValidateVivadoArtifactsTask, VivadoOverlayBuildTask

    for cls in (VivadoOverlayBuildTask, ValidateVivadoArtifactsTask, BuildVivadoArtifactsTask):
        with pytest.raises(ValidationError):
            cls(work_root=Path("/tmp/x"), bitstream=Path("top.bit"))
    for field, value in (("vivado", "vivado"), ("vivado_invocation", "standard"), ("vivado_mount_root", Path("/mnt"))):
        with pytest.raises(ValidationError):
            ValidateVivadoArtifactsTask(work_root=Path("/tmp/x"), **{field: value})
