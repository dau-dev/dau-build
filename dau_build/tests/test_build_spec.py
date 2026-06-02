from pathlib import Path

import pytest
from dau_core.hdl import DAU_INT32_ARROW_LITE_STREAM_AGGREGATION_SV

from dau_build.build_spec import DauBuildSpec, generate_dau_build_artifacts, load_dau_build_spec, main, write_dau_build_artifacts
from dau_build.packaging import artifact_modules, load_artifact_manifest

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def _write_spec(tmp_path: Path) -> Path:
    constraints_dir = tmp_path / "constraints"
    bitstreams_dir = tmp_path / "bitstreams"
    constraints_dir.mkdir()
    bitstreams_dir.mkdir()
    (constraints_dir / "identity.xdc").write_text("set_property PACKAGE_PIN A1 [get_ports clk]\n", encoding="utf-8")
    (bitstreams_dir / "seed.bit").write_bytes(b"DAU")
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
                "  - sum_i64",
                "sources:",
                f"  - {(_SV_DIR / 'ff.sv').as_posix()}",
                f"  - {(_SV_DIR / 'decoder.sv').as_posix()}",
                "metadata:",
                "  - constraints/identity.xdc",
                "binary_assets:",
                "  - bitstreams/seed.bit",
                "modules:",
                "  - ff",
                "  - decoder",
                "backend: vivado",
                "",
            )
        ),
        encoding="utf-8",
    )
    return spec_path


def test_load_dau_build_spec_records_declarative_hardware_contract(tmp_path: Path) -> None:
    spec = load_dau_build_spec(_write_spec(tmp_path))

    assert spec == DauBuildSpec(
        name="identity-pipeline",
        top_name="dau_identity_top",
        platform="vivado-xdma",
        shell="xdma-ddr",
        artifact_stem="dau-identity",
        register_map_version="0.1",
        stream_protocol_version="0.1",
        clock="clk",
        reset="reset",
        operators=("identity", "sum_i64"),
        sources=(_SV_DIR / "ff.sv", _SV_DIR / "decoder.sv"),
        metadata=((tmp_path / "constraints" / "identity.xdc").resolve(),),
        binary_assets=((tmp_path / "bitstreams" / "seed.bit").resolve(),),
        modules=("ff", "decoder"),
        backend="vivado",
    )


def test_generate_dau_build_artifacts_loads_sv_and_emits_top_and_manifest(tmp_path: Path) -> None:
    spec = load_dau_build_spec(_write_spec(tmp_path))
    artifacts = generate_dau_build_artifacts(spec, output_root=tmp_path / "out")

    assert artifacts.top_sv_path == tmp_path / "out" / "generated" / "dau_identity_top.sv"
    assert artifacts.manifest_path == tmp_path / "out" / "dau-identity.manifest"
    assert artifacts.artifact_manifest_path == tmp_path / "out" / "dau-identity.artifacts.yaml"
    assert "module dau_identity_top" in artifacts.top_sv_text
    assert "ff #(.SIZE(32)) ff_inst" in artifacts.top_sv_text
    assert "decoder #(.SIZE(5)) decoder_inst" in artifacts.top_sv_text
    assert "builder=dau_build.build_spec" in artifacts.manifest_text
    assert "top_sv=generated/dau_identity_top.sv" in artifacts.manifest_text
    assert "clock=clk" in artifacts.manifest_text
    assert "reset=reset" in artifacts.manifest_text
    assert "operators=identity,sum_i64" in artifacts.manifest_text
    assert "backend=vivado" in artifacts.manifest_text
    assert "artifact_manifest=dau-identity.artifacts.yaml" in artifacts.manifest_text
    assert "schema: artlink.manifest/v0" in artifacts.artifact_manifest_text
    assert "path: generated/dau_identity_top.sv" in artifacts.artifact_manifest_text
    assert "role: generated-top" in artifacts.artifact_manifest_text
    assert "role: hdl-source" in artifacts.artifact_manifest_text
    assert "provides:" in artifacts.artifact_manifest_text
    assert "role: constraints" in artifacts.artifact_manifest_text
    assert "role: bitstream" in artifacts.artifact_manifest_text
    assert "vivado" not in artifacts.top_sv_text.lower()


def test_write_dau_build_artifacts_persists_bundle_without_toolchain_invocation(tmp_path: Path) -> None:
    spec = load_dau_build_spec(_write_spec(tmp_path))
    artifacts = write_dau_build_artifacts(spec, output_root=tmp_path / "out")

    assert artifacts.top_sv_path.read_text(encoding="utf-8") == artifacts.top_sv_text
    assert artifacts.manifest_path.read_text(encoding="utf-8") == artifacts.manifest_text
    assert artifacts.artifact_manifest_path.read_text(encoding="utf-8") == artifacts.artifact_manifest_text
    assert not (tmp_path / "out" / "scripts" / "dau_overlay.tcl").exists()


def test_generate_dau_build_artifacts_emits_stream_job_top_boundary_for_arrow_lite_aggregator(tmp_path: Path) -> None:
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

    artifacts = generate_dau_build_artifacts(load_dau_build_spec(spec_path), output_root=tmp_path / "out")
    top = artifacts.top_sv_text

    assert "input wire logic register_read_enable" in top
    assert "input wire logic register_write_enable" in top
    assert "input wire logic [15:0] register_address" in top
    assert "output logic [31:0] register_read_data" in top
    assert "input wire logic stream_input_valid" in top
    assert "output logic stream_input_ready" in top
    assert "output logic [63:0] dma_input_address" in top
    assert "output logic [63:0] dma_output_address" in top
    assert "output logic [31:0] capability_operator_bitmap" in top
    assert "localparam logic [15:0] DAU_REGISTER_JOB_CONTROL_OFFSET = 16'h0050;" in top
    assert "assign capability_magic = 32'h44415531;" in top
    assert ".input_valid(stream_input_valid)" in top
    assert ".input_ready(stream_input_ready)" in top
    assert ".output_valid(stream_output_valid)" in top
    assert ".status_error_code(stream_status_error_code)" in top


def test_cli_build_emits_dau_native_artifact_bundle(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = main(["build", "--spec", str(spec_path), "--out", str(tmp_path / "out")])

    assert exit_code == 0
    assert (tmp_path / "out" / "generated" / "dau_identity_top.sv").is_file()
    assert (tmp_path / "out" / "dau-identity.manifest").is_file()
    assert (tmp_path / "out" / "dau-identity.artifacts.yaml").is_file()
    assert capsys.readouterr().out.splitlines() == [
        f"dau-build-artifacts\tmanifest={tmp_path / 'out' / 'dau-identity.manifest'} top_sv={tmp_path / 'out' / 'generated' / 'dau_identity_top.sv'}"
    ]


def test_load_dau_build_spec_rejects_missing_source_file(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("ff.sv", "missing.sv"), encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        load_dau_build_spec(spec_path)

    assert exc_info.type.__name__ == "DauBuildSpecError"
    assert "missing source file" in str(exc_info.value)


def test_load_dau_build_spec_rejects_empty_modules(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("modules:\n  - ff\n  - decoder", "modules: []"), encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        load_dau_build_spec(spec_path)

    assert exc_info.type.__name__ == "DauBuildSpecError"
    assert "modules must contain at least one entry" in str(exc_info.value)


def test_load_dau_build_spec_rejects_unsupported_backend(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("backend: vivado", "backend: quartus"), encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        load_dau_build_spec(spec_path)

    assert exc_info.type.__name__ == "DauBuildSpecError"
    assert "unsupported backend" in str(exc_info.value)


def test_generate_dau_build_artifacts_rejects_unknown_requested_module(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("modules:\n  - ff\n  - decoder", "modules:\n  - missing_module"),
        encoding="utf-8",
    )
    spec = load_dau_build_spec(spec_path)

    with pytest.raises(Exception) as exc_info:
        generate_dau_build_artifacts(spec, output_root=tmp_path / "out")

    assert exc_info.type.__name__ == "DauBuildSpecError"
    assert "unknown module(s): missing_module" in str(exc_info.value)


def test_cli_inspect_reports_spec_without_generating_outputs(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = _main_exit_code(["inspect", "--spec", str(spec_path)])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "dau-build-spec\tname=identity-pipeline platform=vivado-xdma shell=xdma-ddr modules=ff,decoder sources=2 clock=clk reset=reset backend=vivado"
    ]
    assert not (tmp_path / "generated").exists()


def test_cli_validate_accepts_spec_and_artifact_bundle(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    spec_exit_code = _main_exit_code(["validate", "--spec", str(spec_path)])
    build_exit_code = main(["build", "--spec", str(spec_path), "--out", str(tmp_path / "out")])
    bundle_exit_code = _main_exit_code(["validate", "--manifest", str(tmp_path / "out" / "dau-identity.manifest"), "--root", str(tmp_path / "out")])

    assert spec_exit_code == 0
    assert build_exit_code == 0
    assert bundle_exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        f"dau-build-spec-valid\tspec={spec_path}",
        f"dau-build-artifacts\tmanifest={tmp_path / 'out' / 'dau-identity.manifest'} top_sv={tmp_path / 'out' / 'generated' / 'dau_identity_top.sv'}",
        f"dau-build-artifacts-valid\tmanifest={tmp_path / 'out' / 'dau-identity.manifest'} top_sv={tmp_path / 'out' / 'generated' / 'dau_identity_top.sv'}",
    ]


def test_build_spec_consumes_yaml_artifact_manifest_inputs(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    (package_dir / "rtl").mkdir(parents=True)
    (package_dir / "python").mkdir()
    (package_dir / "constraints").mkdir()
    (package_dir / "bitstreams").mkdir()
    (package_dir / "rtl" / "packaged_filter.sv").write_text("module packaged_filter(input bit clk, output logic done); endmodule\n", encoding="utf-8")
    (package_dir / "python" / "model.py").write_text("class PackagedFilter: pass\n", encoding="utf-8")
    (package_dir / "constraints" / "package.xdc").write_text("set_property PACKAGE_PIN A1 [get_ports clk]\n", encoding="utf-8")
    (package_dir / "bitstreams" / "package.bit").write_bytes(b"DAU")
    package_manifest_path = package_dir / "package.artifacts.yaml"
    package_manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: packaged-filter",
                "artifacts:",
                "  - path: rtl/packaged_filter.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "  - path: python/model.py",
                "    kind: source",
                "    role: python-source",
                "    language: python",
                "    provides:",
                "      - kind: python-symbol",
                "        name: PackagedFilter",
                "  - path: constraints/package.xdc",
                "    kind: metadata",
                "    role: constraints",
                "    format: xdc",
                "  - path: bitstreams/package.bit",
                "    kind: binary",
                "    role: bitstream",
                "    format: xilinx-bitstream",
                "",
            )
        ),
        encoding="utf-8",
    )
    spec_path = tmp_path / "dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: packaged-filter-pipeline",
                "top_name: dau_packaged_top",
                "platform: sim",
                "shell: unit-test",
                "artifact_stem: dau-packaged",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - packaged-filter",
                "artifact_manifests:",
                "  - package/package.artifacts.yaml",
                "modules:",
                "  - packaged_filter",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )

    spec = load_dau_build_spec(spec_path)
    artifacts = write_dau_build_artifacts(spec, output_root=tmp_path / "out")
    artifact_manifest = load_artifact_manifest(artifacts.artifact_manifest_path, validate_paths=True, root=tmp_path / "out")

    assert spec.sources == ((package_dir / "rtl" / "packaged_filter.sv").resolve(),)
    assert spec.metadata == ((package_dir / "constraints" / "package.xdc").resolve(),)
    assert spec.binary_assets == ((package_dir / "bitstreams" / "package.bit").resolve(),)
    assert (package_dir / "package.artifacts.yaml").resolve() in spec.artifact_manifests
    assert "module dau_packaged_top" in artifacts.top_sv_text
    assert any(
        artifact.path == (package_dir / "python" / "model.py").resolve() and artifact.role == "python-source"
        for artifact in artifact_manifest.artifacts
    )
    assert any(
        artifact.path == (package_dir / "rtl" / "packaged_filter.sv").resolve() and artifact_modules(artifact) == ("packaged_filter",)
        for artifact in artifact_manifest.artifacts
    )


def test_build_spec_reports_manifest_inputs_without_hdl(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    (package_dir / "python").mkdir(parents=True)
    (package_dir / "python" / "model.py").write_text("class ReferenceModel: pass\n", encoding="utf-8")
    package_manifest_path = package_dir / "package.artifacts.yaml"
    package_manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: python-only-package",
                "artifacts:",
                "  - path: python/model.py",
                "    kind: source",
                "    role: python-source",
                "    language: python",
                "",
            )
        ),
        encoding="utf-8",
    )
    spec_path = tmp_path / "dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: python-only-pipeline",
                "top_name: dau_python_top",
                "platform: sim",
                "shell: unit-test",
                "artifact_stem: dau-python",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - python-only",
                "artifact_manifests:",
                "  - package/package.artifacts.yaml",
                "modules:",
                "  - missing_hdl",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception) as exc_info:
        load_dau_build_spec(spec_path)

    assert exc_info.type.__name__ == "DauBuildSpecError"
    assert "artifact manifest input(s) do not provide HDL source artifacts" in str(exc_info.value)
    assert package_manifest_path.resolve().as_posix() in str(exc_info.value)


def test_cli_inspect_reports_manifest_input_origins(tmp_path: Path, capsys) -> None:
    package_dir = tmp_path / "package"
    (package_dir / "rtl").mkdir(parents=True)
    (package_dir / "python").mkdir()
    (package_dir / "constraints").mkdir()
    (package_dir / "bitstreams").mkdir()
    (package_dir / "rtl" / "packaged_filter.sv").write_text("module packaged_filter(input bit clk, output logic done); endmodule\n", encoding="utf-8")
    (package_dir / "python" / "model.py").write_text("class PackagedFilter: pass\n", encoding="utf-8")
    (package_dir / "constraints" / "package.xdc").write_text("set_property PACKAGE_PIN A1 [get_ports clk]\n", encoding="utf-8")
    (package_dir / "bitstreams" / "package.bit").write_bytes(b"DAU")
    package_manifest_path = package_dir / "package.artifacts.yaml"
    package_manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: packaged-filter",
                "artifacts:",
                "  - path: rtl/packaged_filter.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "  - path: python/model.py",
                "    kind: source",
                "    role: python-source",
                "    language: python",
                "  - path: constraints/package.xdc",
                "    kind: metadata",
                "    role: constraints",
                "    format: xdc",
                "  - path: bitstreams/package.bit",
                "    kind: binary",
                "    role: bitstream",
                "    format: xilinx-bitstream",
                "",
            )
        ),
        encoding="utf-8",
    )
    spec_path = tmp_path / "dau-build.yaml"
    spec_path.write_text(
        "\n".join(
            (
                "name: packaged-filter-pipeline",
                "top_name: dau_packaged_top",
                "platform: sim",
                "shell: unit-test",
                "artifact_stem: dau-packaged",
                'register_map_version: "0.1"',
                'stream_protocol_version: "0.1"',
                "clock: clk",
                "reset: reset",
                "operators:",
                "  - packaged-filter",
                "artifact_manifests:",
                "  - package/package.artifacts.yaml",
                "modules:",
                "  - packaged_filter",
                "backend: none",
                "",
            )
        ),
        encoding="utf-8",
    )

    exit_code = _main_exit_code(["inspect", "--spec", str(spec_path)])

    assert exit_code == 0
    origin = package_manifest_path.resolve().as_posix()
    assert capsys.readouterr().out.splitlines() == [
        "dau-build-spec\tname=packaged-filter-pipeline platform=sim shell=unit-test modules=packaged_filter sources=1 clock=clk reset=reset backend=none",
        f"manifest\tindex=0 path={origin}",
        f"source\tindex=0 path={(package_dir / 'rtl' / 'packaged_filter.sv').resolve().as_posix()} role=hdl-source language=systemverilog origin={origin}",
        f"source\tindex=1 path={(package_dir / 'python' / 'model.py').resolve().as_posix()} role=python-source language=python origin={origin}",
        f"metadata\tindex=0 path={(package_dir / 'constraints' / 'package.xdc').resolve().as_posix()} role=constraints format=xdc origin={origin}",
        f"binary\tindex=0 path={(package_dir / 'bitstreams' / 'package.bit').resolve().as_posix()} role=bitstream format=xilinx-bitstream origin={origin}",
    ]


def _main_exit_code(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        return int(exc.code)
