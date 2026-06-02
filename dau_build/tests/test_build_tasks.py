from __future__ import annotations

from pathlib import Path

import pytest
import tomllib
from ccflow import CallableModel
from dau_core.hdl import DAU_INT32_ARROW_LITE_STREAM_AGGREGATION_SV

from dau_build.build_spec import main
from dau_build.build_steps import BuildStepError, BuildStepResult, SimulateTask, execute_override_request, execute_override_task

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_execute_override_request_accepts_public_task_simulate_surface(tmp_path: Path) -> None:
    assert issubclass(SimulateTask, CallableModel)

    spec_path = _write_spec(tmp_path)

    result = execute_override_request(("task=simulate", "simulator=svparser", "module=dau_identity_top", f"spec_path={spec_path}"))

    assert result == BuildStepResult(
        step="simulate",
        message=f"dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec={spec_path} status=validated",
    )


def test_execute_override_task_accepts_public_cocotb_simulate_surface(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    result = execute_override_task(("task=simulate", "simulator=cocotb", "module=dau_identity_top", f"spec_path={spec_path}"))

    assert result == BuildStepResult(
        step="simulate",
        message=f"dau-build-simulate\ttask=simulate simulator=cocotb module=dau_identity_top spec={spec_path} status=validated",
    )


def test_execute_override_task_requires_selected_module_to_match_spec(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    with pytest.raises(BuildStepError, match="module 'missing' is not provided by spec"):
        execute_override_task(("task=simulate", "simulator=svparser", "module=missing", f"spec_path={spec_path}"))


def test_execute_override_task_maps_synthesize_engine_to_backend_handoff(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    output_root = tmp_path / "out"

    result = execute_override_task(
        (
            "task=synthesize",
            "engine=vivado",
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
            "task=synthesize",
            "engine=vivado",
            "module=dau_int32_arrow_lite_stream_aggregation",
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
    assert Path(str(DAU_INT32_ARROW_LITE_STREAM_AGGREGATION_SV)).resolve().as_posix() in backend_manifest["dau_bundle_hdl_sources"]
    assert backend_manifest["selected_module"] == "dau_int32_arrow_lite_stream_aggregation"
    assert backend_manifest["job_control_offset"] == "0x00000050"
    assert backend_manifest["job_status_offset"] == "0x00000054"
    assert backend_manifest["input_buffer_address"] == "0x0000000000000000"
    assert backend_manifest["output_buffer_address"] == "0x0000000000100000"

    bitstream = output_root / "vivado" / backend_manifest["bitstream"]
    bitstream.parent.mkdir(parents=True)
    bitstream.write_bytes(b"bit")
    flash_result = execute_override_task(("task=flash", f"manifest_path={backend_manifest_path}"))
    smoke_result = execute_override_task(("task=smoke-test", "test=aggregation", f"manifest_path={backend_manifest_path}"))

    assert flash_result == BuildStepResult(
        step="flash",
        message=f"dau-build-flash\ttask=flash tool=openFPGAloader bitstream={bitstream} manifest={backend_manifest_path} mode=volatile status=planned",
    )
    assert smoke_result == BuildStepResult(
        step="smoke-test",
        message=(
            f"dau-build-smoke-test\ttask=smoke-test test=aggregation manifest={backend_manifest_path} "
            "register_window_offset=0x00001000 input_buffer=0x0000000000000000 output_buffer=0x0000000000100000 status=planned"
        ),
    )


def test_execute_override_task_plans_openfpgaloader_flash(tmp_path: Path) -> None:
    bitstream = tmp_path / "Top_wrapper.bit"
    bitstream.write_bytes(b"bit")

    result = execute_override_task(("task=flash", "tool=openFPGAloader", f"bitstream={bitstream}"))

    assert result == BuildStepResult(
        step="flash",
        message=f"dau-build-flash\ttask=flash tool=openFPGAloader bitstream={bitstream} mode=volatile status=planned",
    )


def test_execute_override_task_plans_identity_smoke_test() -> None:
    result = execute_override_task(("task=smoke-test", "test=identity"))

    assert result == BuildStepResult(
        step="smoke-test",
        message="dau-build-smoke-test\ttask=smoke-test test=identity status=planned",
    )


def test_execute_override_task_accepts_public_hardware_plan_surface() -> None:
    result = execute_override_task(("task=hardware-plan", "plan=thunderbolt-release", "work_root=/repo/projects/vivado-shell"))

    assert result == BuildStepResult(
        step="hardware-plan",
        message="thunderbolt-release\tdau-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx",
    )


def test_dau_build_main_dispatches_public_task_arguments(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = main(["task=simulate", "simulator=svparser", "module=dau_identity_top", f"spec_path={spec_path}"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        f"dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec={spec_path} status=validated"
    ]


def test_dau_build_main_dispatches_public_hardware_plan_arguments(capsys) -> None:
    exit_code = main(["task=hardware-plan", "plan=thunderbolt-release", "work_root=/repo/projects/vivado-shell"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "thunderbolt-release\tdau-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx"
    ]


def test_package_scripts_stay_on_hydra_style_dau_build_entrypoints() -> None:
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert "dau-hardware-plan" not in scripts
    assert scripts == {
        "dau-build": "dau_build.build_spec:main",
        "dau-build-steps": "dau_build.build_spec:main_callable_steps",
    }


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
                f"  - {Path(str(DAU_INT32_ARROW_LITE_STREAM_AGGREGATION_SV)).as_posix()}",
                "modules:",
                "  - dau_int32_arrow_lite_stream_aggregation",
                "backend: vivado",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def _read_manifest(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())
