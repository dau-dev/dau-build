from pathlib import Path

import pytest
from artlink import Capability

from dau_build.packaging import Artifact, ArtifactManifest, ArtifactManifestError, artifact_modules, load_artifact_manifest


def test_artifact_manifest_round_trips_sources_metadata_and_binaries(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "python").mkdir()
    (tmp_path / "constraints").mkdir()
    (tmp_path / "bitstreams").mkdir()
    (tmp_path / "rtl" / "filter.sv").write_text("module filter; endmodule\n", encoding="utf-8")
    (tmp_path / "python" / "model.py").write_text("class ReferenceModel: pass\n", encoding="utf-8")
    (tmp_path / "constraints" / "board.xdc").write_text("set_property PACKAGE_PIN A1 [get_ports clk]\n", encoding="utf-8")
    (tmp_path / "bitstreams" / "candidate.bit").write_bytes(b"DAU")
    manifest = ArtifactManifest(
        name="portable-bundle",
        artifacts=(
            Artifact(
                path=Path("rtl/filter.sv"),
                kind="source",
                role="hdl-source",
                language="systemverilog",
                provides=(Capability(kind="hdl-module", name="filter"),),
            ),
            Artifact(
                path=Path("python/model.py"),
                kind="source",
                role="python-source",
                language="python",
                provides=(Capability(kind="python-symbol", name="ReferenceModel"),),
            ),
            Artifact(path=Path("constraints/board.xdc"), kind="metadata", role="constraints", format="xdc"),
            Artifact(path=Path("bitstreams/candidate.bit"), kind="binary", role="bitstream", format="xilinx-bitstream"),
        ),
    )
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(manifest.to_yaml_text(), encoding="utf-8")

    loaded = load_artifact_manifest(manifest_path, validate_paths=True)

    assert loaded == manifest
    assert "schema: artlink.manifest/v0" in manifest.to_yaml_text()
    assert "kind: source" in manifest.to_yaml_text()
    assert "role: constraints" in manifest.to_yaml_text()
    assert "role: bitstream" in manifest.to_yaml_text()
    assert artifact_modules(loaded.artifacts[0]) == ("filter",)


def test_artifact_manifest_is_json_ready_for_pydantic_integrations() -> None:
    manifest = ArtifactManifest(
        name="portable-bundle",
        artifacts=(
            Artifact(
                path=Path("rtl/filter.sv"),
                kind="source",
                role="hdl-source",
                language="systemverilog",
                provides=(Capability(kind="hdl-module", name="filter"),),
            ),
        ),
    )

    assert manifest.model_dump(mode="json") == {
        "schema": "artlink.manifest/v0",
        "name": "portable-bundle",
        "version": "",
        "intent": "",
        "artifacts": [
            {
                "id": "",
                "name": "",
                "path": "rtl/filter.sv",
                "uri": "",
                "kind": "source",
                "role": "hdl-source",
                "format": "sv",
                "media_type": "",
                "language": "systemverilog",
                "provides": [{"kind": "hdl-module", "name": "filter"}],
                "requires": [],
                "digest": None,
                "metadata": {},
            }
        ],
        "references": [],
        "metadata": {},
    }


def test_artifact_manifest_rejects_duplicate_artifact_paths() -> None:
    with pytest.raises(ArtifactManifestError) as exc_info:
        ArtifactManifest(
            name="bad-bundle",
            artifacts=(
                Artifact(path=Path("rtl/filter.sv"), kind="source", role="hdl-source", language="systemverilog"),
                Artifact(path=Path("rtl/filter.sv"), kind="source", role="hdl-source", language="systemverilog"),
            ),
        )

    assert "duplicate artifact location" in str(exc_info.value)


def test_load_artifact_manifest_rejects_missing_artifact_files(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "schema: artlink.manifest/v0\nname: missing-file-bundle\nartifacts:\n  - path: rtl/missing.sv\n    kind: source\n    role: hdl-source\n    language: systemverilog\n",
        encoding="utf-8",
    )

    with pytest.raises(ArtifactManifestError) as exc_info:
        load_artifact_manifest(manifest_path, validate_paths=True)

    assert "missing artifact file" in str(exc_info.value)


def test_artifact_manifest_rejects_unknown_kind() -> None:
    with pytest.raises(ArtifactManifestError) as exc_info:
        ArtifactManifest(name="bad-bundle", artifacts=(Artifact(path=Path("rtl/filter.sv"), kind="hardware", role="hdl-source"),))

    assert "unsupported artifact kind" in str(exc_info.value)


def test_load_artifact_manifest_rejects_removed_artifact_schema(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "filter.sv").write_text("module filter; endmodule\n", encoding="utf-8")
    manifest_path = tmp_path / "old-schema.artifacts.yaml"
    manifest_path.write_text(
        "schema: artlink.artifact-manifest/v0\nname: removed-schema-bundle\nartifacts:\n  - path: rtl/filter.sv\n    kind: source\n    role: hdl-source\n    language: systemverilog\n",
        encoding="utf-8",
    )

    with pytest.raises(ArtifactManifestError) as exc_info:
        load_artifact_manifest(manifest_path, validate_paths=True)

    assert "unsupported manifest schema" in str(exc_info.value)


@pytest.mark.parametrize("removed_field", ("modules: [filter]", "sha256: abc"))
def test_load_artifact_manifest_rejects_removed_artifact_fields(tmp_path: Path, removed_field: str) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "filter.sv").write_text("module filter; endmodule\n", encoding="utf-8")
    manifest_path = tmp_path / "removed-field.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: current-bundle",
                "artifacts:",
                "  - path: rtl/filter.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                f"    {removed_field}",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactManifestError) as exc_info:
        load_artifact_manifest(manifest_path, validate_paths=True)

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_load_artifact_manifest_refuses_a_manifest_without_a_schema(tmp_path: Path) -> None:
    """artlink assumes the current schema when the key is absent; a manifest
    that does not say what it was written for is refused instead."""
    manifest_path = tmp_path / "unversioned.artifacts.yaml"
    manifest_path.write_text("name: unversioned\nartifacts: []\n", encoding="utf-8")
    with pytest.raises(ArtifactManifestError, match="declares no schema"):
        load_artifact_manifest(manifest_path)


def test_shell_build_manifests_are_written_with_their_schema(tmp_path: Path) -> None:
    from dau_build.shell_build import write_shell_build_manifest

    (tmp_path / "dau_mm_job.bit").write_bytes(b"bits")
    manifest_path = write_shell_build_manifest(tmp_path, name="t", metadata={"build_status": "built", "wns_ns": 0.2})
    assert manifest_path.read_text(encoding="utf-8").startswith("schema: artlink.manifest/v0\n")
    assert load_artifact_manifest(manifest_path).name == "t"


def test_artifact_copies_keep_every_field_and_validate() -> None:
    """Copies go through the artlink model, so a field artlink adds is
    carried over without a hand-kept field list, and a bad change fails."""
    import pydantic
    from artlink import Artifact, Capability, Digest

    from dau_build.packaging import _copy_artifact

    original = Artifact(
        id="top",
        path=Path("top.sv"),
        kind="source",
        role="hdl-source",
        language="systemverilog",
        provides=(Capability(kind="hdl-module", name="top"),),
        digest=Digest(algorithm="sha256", value="0" * 64),
        metadata={"owner": "example"},
    )
    copied = _copy_artifact(original, role="generated-top")
    assert copied.model_dump() == {**original.model_dump(), "role": "generated-top"}
    with pytest.raises(pydantic.ValidationError):
        _copy_artifact(original, no_such_field="x")
