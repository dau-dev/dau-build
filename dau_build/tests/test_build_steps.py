from __future__ import annotations

from pathlib import Path
from shutil import which

import pytest
from ccflow import CallableModel

from dau_build.build_spec import main_callable_steps
from dau_build.build_steps import (
    STEP_MODEL_TYPES,
    TASK_MODEL_TYPES,
    BuildStepError,
    BuildStepResult,
    available_step_names,
    available_task_names,
    execute_override_step,
    parse_override_dict,
)

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_build_step_and_task_dispatch_uses_ccflow_callable_models() -> None:
    assert available_step_names() == ("explain", "generate", "inspect", "resolved-config", "simulate", "synthesis", "validate", "write")
    assert available_task_names() == (
        "build-shell-project",
        "build-vivado-artifacts",
        "flash",
        "hardware-plan",
        "overlay-build",
        "simulate",
        "smoke-test",
        "stage-shell",
        "stage-vivado-overlay",
        "stage-vivado-project",
        "synthesize",
        "validate-vivado-artifacts",
    )
    assert all(issubclass(model_type, CallableModel) for model_type in STEP_MODEL_TYPES.values())
    assert all(issubclass(model_type, CallableModel) for model_type in TASK_MODEL_TYPES.values())


def test_parse_override_dict_accepts_hydra_style_keys() -> None:
    overrides = parse_override_dict(("step=inspect", "spec_path=examples/identity/dau-build.yaml", "+driver.os=linux"))

    assert overrides == {
        "step": "inspect",
        "spec_path": "examples/identity/dau-build.yaml",
        "driver.os": "linux",
    }


def test_parse_override_dict_rejects_non_override_tokens() -> None:
    with pytest.raises(BuildStepError, match="expected key=value"):
        parse_override_dict(("inspect",))


def test_execute_inspect_step_from_override_dict(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    result = execute_override_step(("step=inspect", f"spec_path={spec_path}"))

    assert result == BuildStepResult(
        step="inspect",
        message="dau-build-spec\tname=identity-pipeline platform=vivado-xdma shell=xdma-ddr modules=ff sources=1 clock=clk reset=reset backend=none",
    )


def test_execute_validate_step_from_override_dict(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    result = execute_override_step(("step=validate", f"spec_path={spec_path}"))

    assert result == BuildStepResult(step="validate", message=f"dau-build-spec-valid\tspec={spec_path}")


def test_execute_generate_step_returns_unwritten_artifact_paths(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    output_root = tmp_path / "out"

    result = execute_override_step(("step=generate", f"spec_path={spec_path}", f"output_root={output_root}"))

    assert result == BuildStepResult(
        step="generate",
        message=f"dau-build-artifacts-generated\tmanifest={output_root / 'dau-identity.manifest'} top_sv={output_root / 'generated' / 'dau_identity_top.sv'}",
    )
    assert not (output_root / "generated" / "dau_identity_top.sv").exists()


def test_execute_write_step_persists_artifacts(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    output_root = tmp_path / "out"

    result = execute_override_step(("step=write", f"spec_path={spec_path}", f"output_root={output_root}"))

    assert result == BuildStepResult(
        step="write",
        message=f"dau-build-artifacts\tmanifest={output_root / 'dau-identity.manifest'} top_sv={output_root / 'generated' / 'dau_identity_top.sv'}",
    )
    assert (output_root / "generated" / "dau_identity_top.sv").is_file()


def test_resolved_config_step_reports_typed_board_driver_operator_and_memory_models(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    result = execute_override_step(
        (
            "step=resolved-config",
            f"spec_path={spec_path}",
            "board.name=lab-fpga",
            "driver.os=linux",
            "operator.set=int32-aggregation",
            "memory.host_staging_bytes=4096",
        )
    )

    assert result == BuildStepResult(
        step="resolved-config",
        message="\n".join(
            (
                "dau-build-resolved-config",
                "board\tname=lab-fpga platform=vivado-xdma shell=xdma-ddr",
                "backend\tname=none invocation=dry-run",
                "driver\tos=linux transport=xdma",
                "operators\tset=int32-aggregation names=identity",
                "memory\thost_staging_bytes=4096 device_staging_bytes=0",
            )
        ),
    )


def test_simulate_step_validates_sources_and_reports_local_simulation_inputs(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    result = execute_override_step(("step=simulate", f"spec_path={spec_path}"))

    assert result == BuildStepResult(
        step="simulate",
        message="dau-build-simulate\tspec={} top=dau_identity_top modules=ff sources=1 engine=svparser status=validated".format(spec_path),
    )


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_simulate_step_can_run_verilator_testbench(tmp_path: Path) -> None:
    pytest.importorskip("dau_sim.integrations.verilator")
    spec_path = _write_counter_spec(tmp_path)
    testbench_path = _write_counter_testbench(tmp_path)
    work_dir = tmp_path / "verilator-work"

    result = execute_override_step(
        (
            "step=simulate",
            f"spec_path={spec_path}",
            "simulate.engine=verilator",
            f"simulate.testbench_path={testbench_path}",
            "simulate.top_module=counter_tb",
            "simulate.expect_stdout=DAU_BUILD_COUNTER_TB_OK",
            f"output_root={work_dir}",
        )
    )

    assert result == BuildStepResult(
        step="simulate",
        message=(
            f"dau-build-simulate\tspec={spec_path} top=counter_top modules=counter sources=1 engine=verilator "
            f"testbench={testbench_path} testbench_top=counter_tb work_dir={work_dir} status=passed"
        ),
    )
    assert (work_dir / "obj_dir" / "Vcounter_tb").is_file()


@pytest.mark.skipif(which("verilator") is None, reason="verilator not found")
def test_simulate_step_can_run_verilator_profile_from_artlink_manifest(tmp_path: Path) -> None:
    pytest.importorskip("dau_sim.integrations.verilator")
    spec_path = _write_counter_spec(tmp_path)
    testbench_path = _write_counter_testbench(tmp_path)
    profile_manifest_path = _write_counter_profile_manifest(tmp_path, testbench_path)
    work_dir = tmp_path / "verilator-artlink-profile-work"

    result = execute_override_step(
        (
            "step=simulate",
            f"spec_path={spec_path}",
            "simulate.engine=verilator",
            "simulate.profile=counter-profile",
            f"simulate.profile_manifest={profile_manifest_path}",
            f"output_root={work_dir}",
        )
    )

    assert result == BuildStepResult(
        step="simulate",
        message=(
            f"dau-build-simulate\tspec={spec_path} top=counter_top modules=counter sources=1 engine=verilator "
            f"profile=counter-profile testbench_top=counter_tb work_dir={work_dir} status=passed"
        ),
    )
    assert (work_dir / "obj_dir" / "Vcounter_tb").is_file()


def test_synthesis_step_writes_local_artifacts_for_backend_handoff(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    output_root = tmp_path / "out"

    result = execute_override_step(("step=synthesis", f"spec_path={spec_path}", f"output_root={output_root}"))

    assert result == BuildStepResult(
        step="synthesis",
        message=(
            f"dau-build-synthesis\tbackend=none platform=vivado-xdma shell=xdma-ddr output_root={output_root} "
            f"manifest={output_root / 'dau-identity.manifest'} top_sv={output_root / 'generated' / 'dau_identity_top.sv'} vivado=not-invoked"
        ),
    )
    assert (output_root / "generated" / "dau_identity_top.sv").is_file()
    assert (output_root / "dau-identity.manifest").is_file()


def test_explain_step_describes_resolved_plan(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)

    result = execute_override_step(("step=explain", f"spec_path={spec_path}", "board.name=lab-fpga"))

    assert result.message.splitlines() == [
        "dau-build-explain",
        f"spec\tpath={spec_path} name=identity-pipeline top=dau_identity_top",
        "board\tname=lab-fpga platform=vivado-xdma shell=xdma-ddr",
        "actions\tvalidate=local simulate=local synthesis=local-handoff artifacts=generate-or-write",
    ]


def test_public_build_docs_and_tests_do_not_name_internal_hardware_hosts() -> None:
    forbidden = ("nu" + "c2", "ma" + "tx")
    repo_root = Path(__file__).resolve().parents[2]
    checked_paths = [repo_root / "README.md", repo_root / "examples", repo_root / "dau_build"]
    matches: list[str] = []
    for path in checked_paths:
        files = path.rglob("*") if path.is_dir() else (path,)
        for file_path in files:
            if not file_path.is_file() or file_path.suffix not in {".md", ".py", ".yaml", ".yml", ".toml"}:
                continue
            text = file_path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    matches.append(file_path.relative_to(repo_root).as_posix())
    assert matches == []


def test_execute_step_validates_required_overrides(tmp_path: Path) -> None:
    with pytest.raises(BuildStepError, match="missing required override"):
        execute_override_step(("step=inspect",))


def test_callable_steps_entrypoint_prints_result(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = main_callable_steps(["step=validate", f"spec_path={spec_path}"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [f"dau-build-spec-valid\tspec={spec_path}"]


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


def _write_counter_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "counter-dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: counter-pipeline",
                "top_name: counter_top",
                "platform: sim",
                "shell: unit-test",
                "artifact_stem: dau-counter",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - counter",
                "sources:",
                f"  - {(Path(__file__).parent / 'sv' / 'counter.sv').as_posix()}",
                "modules:",
                "  - counter",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def _write_counter_testbench(tmp_path: Path) -> Path:
    testbench_path = tmp_path / "counter_tb.sv"
    testbench_path.write_text(
        "\n".join(
            (
                "`timescale 1ns/1ps",
                "module counter_tb;",
                "  logic clk = 1'b0;",
                "  logic [31:0] out;",
                "  counter dut(.clk(clk), .out(out));",
                "  always #5 clk = ~clk;",
                "  initial begin",
                "    repeat (3) @(posedge clk);",
                "    #1;",
                '    if (out != 32\'d3) $fatal(1, "counter mismatch: %0d", out);',
                '    $display("DAU_BUILD_COUNTER_TB_OK");',
                "    $finish;",
                "  end",
                "endmodule",
                "",
            )
        ),
        encoding="utf-8",
    )
    return testbench_path


def _write_counter_profile_manifest(tmp_path: Path, testbench_path: Path) -> Path:
    profile_path = tmp_path / "counter-profiles.yaml"
    profile_path.write_text(
        "\n".join(
            (
                "schema: dau.simulation-profile/v0",
                "profiles:",
                "  - name: counter-profile",
                "    simulator: verilator",
                "    top_module: counter_tb",
                "    expect_stdout: DAU_BUILD_COUNTER_TB_OK",
                "    sources:",
                "      - artifact: counter-tb",
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "counter-profiles.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: counter-profiles",
                "artifacts:",
                "  - id: counter-profile-metadata",
                f"    path: {profile_path.name}",
                "    kind: metadata",
                "    role: simulation-profile",
                "    format: dau.simulation-profile/v0",
                "    provides:",
                "      - kind: simulation-profile",
                "        name: counter-profile",
                "  - id: counter-tb",
                f"    path: {testbench_path.name}",
                "    kind: source",
                "    role: testbench-source",
                "    language: systemverilog",
                "",
            )
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_task_dispatch_import_stays_light() -> None:
    """Hardware hosts run flash/shell tasks with none of the SV-parser
    stack installed: importing the task surface must not pull it."""
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dau_build.build_steps; heavy = [m for m in ('amaranth', 'pyslang', 'dau_sim') if m in sys.modules]; raise SystemExit(1 if heavy else 0)",
        ],
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode()
