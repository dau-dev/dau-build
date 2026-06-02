from pathlib import Path

import pytest

from dau_build.artifact_bundle import ArtifactBundleError, load_artifact_bundle


def test_artifact_bundle_rejects_manifest_missing_required_roles(tmp_path: Path) -> None:
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "model.py").write_text("class ReferenceModel: pass\n", encoding="utf-8")
    manifest_path = tmp_path / "package.artifacts.yaml"
    manifest_path.write_text(
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

    with pytest.raises(ArtifactBundleError) as exc_info:
        load_artifact_bundle((manifest_path,), required_roles=("hdl-source",))

    assert "missing required artifact role(s): hdl-source" in str(exc_info.value)
    assert manifest_path.as_posix() in str(exc_info.value)


def test_artifact_bundle_rejects_duplicate_module_providers(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "filter_a.sv").write_text("module filter_a; endmodule\n", encoding="utf-8")
    (tmp_path / "rtl" / "filter_b.sv").write_text("module filter_b; endmodule\n", encoding="utf-8")
    manifest_path = tmp_path / "package.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: duplicate-module-package",
                "artifacts:",
                "  - path: rtl/filter_a.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "    provides:",
                "      - kind: hdl-module",
                "        name: filter",
                "  - path: rtl/filter_b.sv",
                "    kind: source",
                "    role: hdl-source",
                "    language: systemverilog",
                "    provides:",
                "      - kind: hdl-module",
                "        name: filter",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactBundleError) as exc_info:
        load_artifact_bundle((manifest_path,))

    assert "module filter is provided by multiple artifacts" in str(exc_info.value)
    assert "rtl/filter_a.sv" in str(exc_info.value)
    assert "rtl/filter_b.sv" in str(exc_info.value)


def test_artifact_bundle_rejects_unsupported_source_languages(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kernel.cpp").write_text("void kernel() {}\n", encoding="utf-8")
    manifest_path = tmp_path / "package.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: unsupported-source-package",
                "artifacts:",
                "  - path: src/kernel.cpp",
                "    kind: source",
                "    role: native-source",
                "    language: cpp",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactBundleError) as exc_info:
        load_artifact_bundle((manifest_path,))

    assert "unsupported source language for" in str(exc_info.value)
    assert "src/kernel.cpp" in str(exc_info.value)
    assert "cpp" in str(exc_info.value)


def test_artifact_bundle_rejects_missing_hdl_sources_when_required(tmp_path: Path) -> None:
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "model.py").write_text("class Model: pass\n", encoding="utf-8")
    manifest_path = tmp_path / "model.artifacts.yaml"
    manifest_path.write_text(
        "\n".join(
            (
                "schema: artlink.manifest/v0",
                "name: model-only",
                "artifacts:",
                "  - path: python/model.py",
                "    kind: source",
                "    role: python-reference",
                "    language: python",
                "    provides:",
                "      - kind: python-symbol",
                "        name: Model",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactBundleError) as exc_info:
        load_artifact_bundle((manifest_path,), require_hdl_sources=True)

    assert "does not provide HDL source artifacts" in str(exc_info.value)
    assert manifest_path.as_posix() in str(exc_info.value)
