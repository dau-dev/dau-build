from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from dau_sim.integrations.profiles import SimulationProfile
from dau_sim.integrations.verilator_profiles import VerilatorProfile
from pydantic import BaseModel, BeforeValidator, ConfigDict, Discriminator, Field, Tag, ValidationError

from dau_build.packaging import Artifact, ArtifactManifestError, artifact_path, load_artifact_manifest

SIMULATION_PROFILE_SCHEMA = "dau.simulation-profile/v0"


class _ArtifactSource(BaseModel):
    """A source named by the id of an artifact in the same manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: str = Field(min_length=1)


class _PathSource(BaseModel):
    """A source file, relative to the manifest that registers the profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)


class _UriSource(BaseModel):
    """A source inside an installed package (``package://<pkg>/<resource>``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = Field(min_length=1)


def _source_tag(value: Any) -> str | None:
    if isinstance(value, Mapping) and len(value) == 1:
        return next(iter(value))
    return None


_TaggedSource = Annotated[
    Annotated[_ArtifactSource, Tag("artifact")] | Annotated[_PathSource, Tag("path")] | Annotated[_UriSource, Tag("uri")],
    Discriminator(_source_tag, custom_error_type="profile_source", custom_error_message="a profile source is one of artifact, path or uri"),
]
# a bare string is an artifact id
_ProfileSource = Annotated[_TaggedSource, BeforeValidator(lambda value: {"artifact": value} if isinstance(value, str) else value)]


class _ProfileEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    simulator: Literal["verilator", "cocotb"]
    top_module: str = Field(min_length=1)
    expect_stdout: str | None = None
    test_module: str | None = None
    sources: tuple[_ProfileSource, ...] = ()


class _ProfileDocument(BaseModel):
    """A simulation-profile artifact: the schema line and its profiles."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_id: Literal["dau.simulation-profile/v0"] = Field(alias="schema")
    profiles: tuple[_ProfileEntry, ...]


class ManifestVerilatorProfile(VerilatorProfile):
    """A Verilator bench registered by an artifact manifest. Its bench
    prints ``expect_stdout`` on success, which is how a run is judged."""

    expect_stdout: str


class CocotbProfile(SimulationProfile):
    """A cocotb bench registered by an artifact manifest: HDL sources, the
    toplevel they build, and the cocotb test module that drives it."""

    hdl_toplevel: str
    test_module: str


SIMULATION_PROFILE_ROLE = "simulation-profile"
SIMULATION_PROFILE_FORMAT = SIMULATION_PROFILE_SCHEMA
PACKAGE_URI_PREFIX = "package://"


class SimulationProfileError(ValueError):
    pass


def default_profile_manifest_paths() -> tuple[Path, ...]:
    """dau-build ships no profiles of its own (it is generic build
    integration); consumers pass manifests explicitly or via config."""
    return ()


def available_verilator_profiles(profile_manifests: Iterable[Path] = ()) -> tuple[str, ...]:
    return tuple(sorted(_load_verilator_profile_map(profile_manifests)))


def resolve_verilator_profile(name: str, profile_manifests: Iterable[Path] = ()) -> VerilatorProfile | CocotbProfile:
    profiles = _load_verilator_profile_map(profile_manifests)
    try:
        return profiles[name]
    except KeyError as exc:
        known = ", ".join(sorted(profiles))
        raise SimulationProfileError(f"unknown DAU Verilator profile {name!r}; expected one of: {known}") from exc


def resolve_profile(name: str, profile_manifests: Iterable[Path] = ()) -> VerilatorProfile | CocotbProfile:
    """The single profile resolution chain: dau-sim's registered profiles
    first, then manifest-registered ones. Every dau-build simulate entrypoint
    resolves through here so a profile name means the same thing everywhere."""
    from dau_sim.integrations.verilator_profiles import (
        available_verilator_profiles as registered_names,
        resolve_verilator_profile as resolve_registered,
    )

    try:
        return resolve_registered(name)
    except KeyError:
        pass
    try:
        return resolve_verilator_profile(name, profile_manifests=profile_manifests)
    except SimulationProfileError:
        known = ", ".join(sorted({*registered_names(), *available_verilator_profiles(profile_manifests)}))
        raise SimulationProfileError(f"unknown DAU Verilator profile {name!r}; expected one of: {known}") from None


def load_verilator_profiles_from_manifest(manifest_path: Path) -> dict[str, VerilatorProfile | CocotbProfile]:
    try:
        manifest = load_artifact_manifest(manifest_path, validate_paths=False)
    except ArtifactManifestError as exc:
        raise SimulationProfileError(f"{manifest_path.as_posix()}: {exc}") from exc

    artifacts_by_id = _artifacts_by_id(manifest.artifacts)
    profiles: dict[str, VerilatorProfile | CocotbProfile] = {}
    for artifact in manifest.artifacts:
        if artifact.kind != "metadata" or artifact.role != SIMULATION_PROFILE_ROLE:
            continue
        if artifact.format and artifact.format != SIMULATION_PROFILE_FORMAT:
            raise SimulationProfileError(f"unsupported simulation profile artifact format {artifact.format!r}: {artifact.location}")
        profile_path = _artifact_to_path(artifact, root=manifest_path.parent)
        for entry in _load_profile_document(profile_path).profiles:
            profile = _verilator_profile_from_entry(entry, artifacts_by_id=artifacts_by_id, root=manifest_path.parent)
            profiles[profile.name] = profile
    return profiles


def _load_verilator_profile_map(profile_manifests: Iterable[Path]) -> dict[str, VerilatorProfile | CocotbProfile]:
    profiles: dict[str, VerilatorProfile | CocotbProfile] = {}
    for manifest_path in (*default_profile_manifest_paths(), *(Path(path) for path in profile_manifests)):
        profiles.update(load_verilator_profiles_from_manifest(manifest_path))
    return profiles


def _load_profile_document(path: Path) -> _ProfileDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SimulationProfileError(f"cannot read simulation profile {path.as_posix()}: {exc}") from exc
    try:
        return _ProfileDocument.model_validate(raw)
    except ValidationError as exc:
        raise SimulationProfileError(f"invalid simulation profile {path.as_posix()}: {exc}") from exc


def _verilator_profile_from_entry(entry: _ProfileEntry, *, artifacts_by_id: Mapping[str, Artifact], root: Path):
    sources = tuple(_profile_source(source, artifacts_by_id=artifacts_by_id, root=root) for source in entry.sources)
    if entry.simulator == "verilator":
        if not entry.expect_stdout:
            raise SimulationProfileError(f"profile {entry.name!r} (verilator) requires expect_stdout")
        return ManifestVerilatorProfile(name=entry.name, sources=sources, top_module=entry.top_module, expect_stdout=entry.expect_stdout)

    if not entry.test_module:
        raise SimulationProfileError(f"profile {entry.name!r} (cocotb) requires test_module")
    return CocotbProfile(name=entry.name, sources=sources, hdl_toplevel=entry.top_module, test_module=entry.test_module)


def _profile_source(source: _ArtifactSource | _PathSource | _UriSource, *, artifacts_by_id: Mapping[str, Artifact], root: Path) -> Path:
    if isinstance(source, _ArtifactSource):
        return _artifact_ref_to_path(source.artifact, artifacts_by_id=artifacts_by_id, root=root)
    if isinstance(source, _PathSource):
        path = Path(source.path)
        return path if path.is_absolute() else root / path
    return _package_uri_to_path(source.uri)


def _artifact_ref_to_path(artifact_id: str, *, artifacts_by_id: Mapping[str, Artifact], root: Path) -> Path:
    try:
        artifact = artifacts_by_id[artifact_id]
    except KeyError as exc:
        known = ", ".join(sorted(artifacts_by_id))
        raise SimulationProfileError(f"unknown profile source artifact {artifact_id!r}; expected one of: {known}") from exc
    if artifact.kind != "source":
        raise SimulationProfileError(f"profile source artifact {artifact_id!r} is {artifact.kind!r}, not 'source'")
    return _artifact_to_path(artifact, root=root)


def _artifact_to_path(artifact: Artifact, *, root: Path) -> Path:
    if artifact.path is not None:
        path = artifact_path(root, artifact)
        return path.resolve()
    if artifact.uri.startswith(PACKAGE_URI_PREFIX):
        return _package_uri_to_path(artifact.uri)
    raise SimulationProfileError(f"artifact {artifact.display_id} has unsupported URI: {artifact.uri}")


def _package_uri_to_path(uri: str) -> Path:
    resource = uri.removeprefix(PACKAGE_URI_PREFIX)
    package, separator, resource_name = resource.partition("/")
    if not package or not separator or not resource_name:
        raise SimulationProfileError(f"invalid package resource URI: {uri}")
    return _resource_path(files(package).joinpath(resource_name))


def _artifacts_by_id(artifacts: tuple[Artifact, ...]) -> dict[str, Artifact]:
    by_id: dict[str, Artifact] = {}
    for artifact in artifacts:
        artifact_id = artifact.id or artifact.name
        if artifact_id:
            by_id[artifact_id] = artifact
    return by_id


def _resource_path(resource) -> Path:
    return Path(str(resource))
