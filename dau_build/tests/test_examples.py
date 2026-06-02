from pathlib import Path

from dau_build.build_spec import generate_dau_build_artifacts, load_dau_build_spec
from dau_build.packaging import artifact_modules, load_artifact_manifest

_EXAMPLE_DIR = Path(__file__).parents[2] / "examples" / "identity"


def test_identity_example_build_spec_loads_and_generates_artifacts(tmp_path: Path) -> None:
    spec = load_dau_build_spec(_EXAMPLE_DIR / "dau-build.yaml")
    artifacts = generate_dau_build_artifacts(spec, output_root=tmp_path / "out")

    assert spec.name == "identity-pipeline"
    assert spec.artifact_manifests == ((_EXAMPLE_DIR / "package.artifacts.yaml").resolve(),)
    assert spec.sources == ((_EXAMPLE_DIR / "rtl" / "identity.sv").resolve(),)
    assert "module dau_identity_top" in artifacts.top_sv_text
    assert "identity identity_inst" in artifacts.top_sv_text
    assert "schema: artlink.manifest/v0" in artifacts.artifact_manifest_text
    assert "role: generated-top" in artifacts.artifact_manifest_text


def test_identity_generated_artifact_bundle_example_is_portable() -> None:
    manifest = load_artifact_manifest(_EXAMPLE_DIR / "generated" / "dau-identity.artifacts.yaml", validate_paths=True, root=_EXAMPLE_DIR)

    assert all(not artifact.path.is_absolute() for artifact in manifest.artifacts)
    assert {artifact.role for artifact in manifest.artifacts} == {"hdl-source", "python-source", "constraints", "bitstream", "generated-top"}
    assert any(
        artifact.path == Path("generated/dau_identity_top.sv") and artifact_modules(artifact) == ("dau_identity_top",)
        for artifact in manifest.artifacts
    )
