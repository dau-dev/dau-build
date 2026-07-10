from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from artlink import ARTLINK_MANIFEST_SCHEMA, Artifact, Capability, Manifest, ManifestError, capability_from_value
from pydantic import ConfigDict, model_validator

__all__ = (
    "ARTIFACT_MANIFEST_SCHEMA",
    "SUPPORTED_ARTIFACT_KINDS",
    "ArtifactManifestError",
    "Artifact",
    "ArtifactManifest",
    "load_artifact_manifest",
    "artifact_manifest_from_mapping",
    "validate_artifact_files",
    "artifact_path",
    "artifact_modules",
    "artifact_with_modules",
)

ARTIFACT_MANIFEST_SCHEMA = ARTLINK_MANIFEST_SCHEMA
SUPPORTED_ARTIFACT_KINDS = frozenset(("source", "metadata", "binary"))
HDL_MODULE_CAPABILITY_KIND = "hdl-module"


class ArtifactManifestError(ManifestError):
    pass


class ArtifactManifest(Manifest):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ManifestError as exc:
            raise ArtifactManifestError(str(exc)) from exc

    @model_validator(mode="after")
    def _validate_supported_kinds(self) -> "ArtifactManifest":
        unsupported_kinds = tuple(dict.fromkeys(artifact.kind for artifact in self.artifacts if artifact.kind not in SUPPORTED_ARTIFACT_KINDS))
        if unsupported_kinds:
            raise ValueError(f"unsupported artifact kind(s): {', '.join(unsupported_kinds)}")
        return self


def load_yaml_mapping(path: Path, *, description: str, error_type: type[Exception], schema: str | None = None) -> dict[str, Any]:
    """Shared read -> safe_load -> mapping/schema-check skeleton for every
    dau-build YAML surface (manifests, build specs, simulation profiles)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise error_type(f"failed to read {description}: {path.as_posix()}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise error_type(f"invalid {description} YAML: {path.as_posix()}") from exc
    if not isinstance(raw, dict):
        raise error_type(f"{description} must be a YAML mapping: {path.as_posix()}")
    if schema is not None and raw.get("schema", "") != schema:
        raise error_type(f"unsupported {description} schema {raw.get('schema')!r}: {path.as_posix()}")
    return raw


def load_artifact_manifest(path: Path, *, validate_paths: bool = False, root: Path | None = None) -> ArtifactManifest:
    raw = load_yaml_mapping(path, description="artifact manifest", error_type=ArtifactManifestError)
    manifest = artifact_manifest_from_mapping(raw)
    if validate_paths:
        validate_artifact_files(manifest, root=path.parent if root is None else root)
    return manifest


def artifact_manifest_from_mapping(raw: dict[str, Any]) -> ArtifactManifest:
    raw_artifacts = raw.get("artifacts", [])
    if not isinstance(raw_artifacts, list):
        raise ArtifactManifestError("artifact manifest artifacts must be a list")
    schema = raw.get("schema", ARTIFACT_MANIFEST_SCHEMA)
    if schema != ARTIFACT_MANIFEST_SCHEMA:
        raise ArtifactManifestError(f"unsupported artifact manifest schema: {schema}")

    normalized = dict(raw)
    normalized["schema"] = ARTIFACT_MANIFEST_SCHEMA
    normalized["artifacts"] = tuple(_artifact_mapping_from_current_schema(item) for item in raw_artifacts)
    try:
        return ArtifactManifest(**normalized)
    except ManifestError as exc:
        raise ArtifactManifestError(str(exc)) from exc


def validate_artifact_files(manifest: ArtifactManifest, *, root: Path) -> None:
    missing_paths = tuple(
        resolved_path
        for artifact in manifest.artifacts
        if artifact.path is not None and not (resolved_path := artifact_path(root, artifact)).is_file()
    )
    if missing_paths:
        missing_text = ", ".join(path.as_posix() for path in missing_paths)
        raise ArtifactManifestError(f"missing artifact file(s): {missing_text}")


def artifact_path(root: Path, artifact: Artifact) -> Path:
    if artifact.path is None:
        raise ArtifactManifestError(f"artifact has no filesystem path: {artifact.display_id}")
    if artifact.path.is_absolute():
        return artifact.path
    return root / artifact.path


def artifact_modules(artifact: Artifact, *, capability_kind: str = HDL_MODULE_CAPABILITY_KIND) -> tuple[str, ...]:
    modules: list[str] = []
    for capability_value in artifact.provides:
        capability = capability_from_value(capability_value)
        if capability.kind == capability_kind and capability.name not in modules:
            modules.append(capability.name)
    return tuple(modules)


def artifact_with_modules(
    artifact: Artifact,
    modules: tuple[str, ...],
    *,
    capability_kind: str = HDL_MODULE_CAPABILITY_KIND,
) -> Artifact:
    provides = list(artifact.provides)
    existing_keys = {capability_from_value(capability).key for capability in provides}
    for module in modules:
        capability = Capability(kind=capability_kind, name=module)
        if capability.key in existing_keys:
            continue
        provides.append(capability)
        existing_keys.add(capability.key)
    return _copy_artifact(artifact, provides=tuple(provides))


def _artifact_mapping_from_current_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactManifestError("artifact entries must be YAML mappings")
    artifact = dict(value)
    removed_fields = tuple(field for field in ("modules", "sha256") if field in artifact)
    if removed_fields:
        raise ArtifactManifestError("unsupported artifact field(s) in artlink.manifest/v0: " + ", ".join(removed_fields))
    return artifact


def _copy_artifact(artifact: Artifact, **changes: Any) -> Artifact:
    data = {
        "id": artifact.id,
        "name": artifact.name,
        "path": artifact.path,
        "uri": artifact.uri,
        "kind": artifact.kind,
        "role": artifact.role,
        "format": artifact.format,
        "media_type": artifact.media_type,
        "language": artifact.language,
        "provides": artifact.provides,
        "requires": artifact.requires,
        "digest": artifact.digest,
        "metadata": artifact.metadata,
    }
    data.update(changes)
    return Artifact(**data)
