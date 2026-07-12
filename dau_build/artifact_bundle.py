from __future__ import annotations

from pathlib import Path
from typing import Any

from artlink import Artifact
from ccflow import BaseModel

from dau_build.packaging import ArtifactManifestError, artifact_modules, artifact_path, load_artifact_manifest

SUPPORTED_SOURCE_LANGUAGES = frozenset(("python", "systemverilog", "verilog"))
HDL_SOURCE_LANGUAGES = frozenset(("systemverilog", "verilog"))

__all__ = (
    "HDL_SOURCE_LANGUAGES",
    "SUPPORTED_SOURCE_LANGUAGES",
    "ArtifactBundleError",
    "ArtifactBundleEntry",
    "ArtifactBundle",
    "load_artifact_bundle",
    "is_hdl_source_artifact",
    "source_language_from_path",
)


class ArtifactBundleError(ValueError):
    pass


class ArtifactBundleEntry(BaseModel):
    artifact: Artifact
    origin: str
    manifest_path: Path | None = None


class ArtifactBundle(BaseModel):
    name: str
    entries: tuple[ArtifactBundleEntry, ...]
    manifest_paths: tuple[Path, ...] = ()

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(entry.artifact for entry in self.entries)

    def entries_for_kind(self, kind: str) -> tuple[ArtifactBundleEntry, ...]:
        return tuple(entry for entry in self.entries if entry.artifact.kind == kind)

    def entries_for_role(self, role: str) -> tuple[ArtifactBundleEntry, ...]:
        return tuple(entry for entry in self.entries if entry.artifact.role == role)

    def hdl_source_entries(self) -> tuple[ArtifactBundleEntry, ...]:
        return tuple(entry for entry in self.entries_for_kind("source") if is_hdl_source_artifact(entry.artifact))

    def validate(self, *, required_roles: tuple[str, ...] = (), require_hdl_sources: bool = False) -> "ArtifactBundle":
        errors: list[str] = []
        roles = {entry.artifact.role for entry in self.entries}
        missing_roles = tuple(role for role in required_roles if role not in roles)
        if missing_roles:
            errors.append(f"missing required artifact role(s): {', '.join(missing_roles)} in {_origin_summary(self)}")
        if require_hdl_sources and not self.hdl_source_entries():
            errors.append(f"artifact bundle does not provide HDL source artifacts: {_origin_summary(self)}")
        errors.extend(_unsupported_source_language_errors(self.entries))
        errors.extend(_duplicate_module_provider_errors(self.entries))
        if errors:
            raise ArtifactBundleError("; ".join(errors))
        return self


def load_artifact_bundle(
    manifest_paths: tuple[Path, ...],
    *,
    direct_artifacts: tuple[Artifact, ...] = (),
    name: str = "artifact-bundle",
    required_roles: tuple[str, ...] = (),
    require_hdl_sources: bool = False,
    validate_paths: bool = True,
) -> ArtifactBundle:
    entries: list[ArtifactBundleEntry] = []
    resolved_manifest_paths = tuple(path.resolve() for path in manifest_paths)
    for manifest_path in resolved_manifest_paths:
        try:
            manifest = load_artifact_manifest(manifest_path, validate_paths=validate_paths)
        except ArtifactManifestError as exc:
            raise ArtifactBundleError(f"{manifest_path.as_posix()}: {exc}") from exc
        entries.extend(
            ArtifactBundleEntry(
                artifact=_normalized_artifact(_resolved_artifact(manifest_path.parent, artifact)),
                origin=manifest_path.as_posix(),
                manifest_path=manifest_path,
            )
            for artifact in manifest.artifacts
        )
    entries.extend(ArtifactBundleEntry(artifact=_normalized_artifact(artifact), origin="direct", manifest_path=None) for artifact in direct_artifacts)
    return ArtifactBundle(name=name, entries=tuple(entries), manifest_paths=resolved_manifest_paths).validate(
        required_roles=required_roles,
        require_hdl_sources=require_hdl_sources,
    )


def is_hdl_source_artifact(artifact: Artifact) -> bool:
    return artifact.kind == "source" and (
        artifact.role in ("hdl-source", "generated-top") or artifact.language in HDL_SOURCE_LANGUAGES or bool(artifact_modules(artifact))
    )


def source_language_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".sv", ".svh"):
        return "systemverilog"
    if suffix == ".v":
        return "verilog"
    if suffix == ".py":
        return "python"
    return ""


def _resolved_artifact(root: Path, artifact: Artifact) -> Artifact:
    if artifact.path is None:
        return artifact
    return _copy_artifact(artifact, path=artifact_path(root, artifact).resolve())


def _normalized_artifact(artifact: Artifact) -> Artifact:
    if artifact.kind != "source" or artifact.language:
        return artifact
    if artifact.path is None:
        return artifact
    return _copy_artifact(artifact, language=source_language_from_path(artifact.path))


def _unsupported_source_language_errors(entries: tuple[ArtifactBundleEntry, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    supported = ", ".join(sorted(SUPPORTED_SOURCE_LANGUAGES))
    for entry in entries:
        artifact = entry.artifact
        if artifact.kind != "source":
            continue
        if artifact.language not in SUPPORTED_SOURCE_LANGUAGES:
            location = artifact.location
            language = artifact.language or "unknown"
            errors.append(f"unsupported source language for {location} from {entry.origin}: {language}; supported: {supported}")
    return tuple(errors)


def _duplicate_module_provider_errors(entries: tuple[ArtifactBundleEntry, ...]) -> tuple[str, ...]:
    providers_by_module: dict[str, list[ArtifactBundleEntry]] = {}
    for entry in entries:
        if entry.artifact.kind != "source":
            continue
        for module in artifact_modules(entry.artifact):
            providers_by_module.setdefault(module, []).append(entry)
    return tuple(
        f"module {module} is provided by multiple artifacts: " + ", ".join(entry.artifact.location for entry in providers)
        for module, providers in providers_by_module.items()
        if len(providers) > 1
    )


def _origin_summary(bundle: ArtifactBundle) -> str:
    if bundle.manifest_paths:
        return ", ".join(path.as_posix() for path in bundle.manifest_paths)
    return bundle.name


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
