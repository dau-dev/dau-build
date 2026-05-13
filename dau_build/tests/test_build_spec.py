from pathlib import Path

from dau_build.build_spec import DauBuildSpec, generate_dau_build_artifacts, load_dau_build_spec, main, write_dau_build_artifacts

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def _write_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "dau-build.toml"
    spec_path.write_text(
        "\n".join(
            (
                'name = "identity-pipeline"',
                'top_name = "dau_identity_top"',
                'platform = "nitefury"',
                'shell = "xdma-ddr"',
                'artifact_stem = "dau-identity"',
                'register_map_version = "0.1"',
                'stream_protocol_version = "0.1"',
                'operators = ["identity", "sum_i64"]',
                "sources = [",
                f'  "{(_SV_DIR / "ff.sv").as_posix()}",',
                f'  "{(_SV_DIR / "decoder.sv").as_posix()}",',
                "]",
                'modules = ["ff", "decoder"]',
                'backend = "nitefury-vivado"',
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
        platform="nitefury",
        shell="xdma-ddr",
        artifact_stem="dau-identity",
        register_map_version="0.1",
        stream_protocol_version="0.1",
        operators=("identity", "sum_i64"),
        sources=(_SV_DIR / "ff.sv", _SV_DIR / "decoder.sv"),
        modules=("ff", "decoder"),
        backend="nitefury-vivado",
    )


def test_generate_dau_build_artifacts_loads_sv_and_emits_top_and_manifest(tmp_path: Path) -> None:
    spec = load_dau_build_spec(_write_spec(tmp_path))
    artifacts = generate_dau_build_artifacts(spec, output_root=tmp_path / "out")

    assert artifacts.top_sv_path == tmp_path / "out" / "generated" / "dau_identity_top.sv"
    assert artifacts.manifest_path == tmp_path / "out" / "dau-identity.manifest"
    assert "module dau_identity_top" in artifacts.top_sv_text
    assert "ff #(.SIZE(32)) ff_inst" in artifacts.top_sv_text
    assert "decoder #(.SIZE(5)) decoder_inst" in artifacts.top_sv_text
    assert "builder=dau_build.build_spec" in artifacts.manifest_text
    assert "top_sv=generated/dau_identity_top.sv" in artifacts.manifest_text
    assert "operators=identity,sum_i64" in artifacts.manifest_text
    assert "backend=nitefury-vivado" in artifacts.manifest_text
    assert "vivado" not in artifacts.top_sv_text.lower()


def test_write_dau_build_artifacts_persists_bundle_without_toolchain_invocation(tmp_path: Path) -> None:
    spec = load_dau_build_spec(_write_spec(tmp_path))
    artifacts = write_dau_build_artifacts(spec, output_root=tmp_path / "out")

    assert artifacts.top_sv_path.read_text(encoding="utf-8") == artifacts.top_sv_text
    assert artifacts.manifest_path.read_text(encoding="utf-8") == artifacts.manifest_text
    assert not (tmp_path / "out" / "scripts" / "dau_overlay.tcl").exists()


def test_cli_build_emits_dau_native_artifact_bundle(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path)

    exit_code = main(["build", "--spec", str(spec_path), "--out", str(tmp_path / "out")])

    assert exit_code == 0
    assert (tmp_path / "out" / "generated" / "dau_identity_top.sv").is_file()
    assert (tmp_path / "out" / "dau-identity.manifest").is_file()
    assert capsys.readouterr().out.splitlines() == [
        f"dau-build-artifacts\tmanifest={tmp_path / 'out' / 'dau-identity.manifest'} top_sv={tmp_path / 'out' / 'generated' / 'dau_identity_top.sv'}"
    ]
