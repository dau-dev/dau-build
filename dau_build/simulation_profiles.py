from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

from dau_sim.integrations.verilator_profiles import VerilatorProfile

from dau_build.packaging import Artifact, ArtifactManifestError, artifact_path, load_artifact_manifest, load_yaml_mapping

SIMULATION_PROFILE_SCHEMA = "dau.simulation-profile/v0"
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


def resolve_verilator_profile(name: str, profile_manifests: Iterable[Path] = ()) -> VerilatorProfile:
    profiles = _load_verilator_profile_map(profile_manifests)
    try:
        return profiles[name]
    except KeyError as exc:
        known = ", ".join(sorted(profiles))
        raise SimulationProfileError(f"unknown DAU Verilator profile {name!r}; expected one of: {known}") from exc


def resolve_profile(name: str, profile_manifests: Iterable[Path] = ()) -> VerilatorProfile:
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


def load_verilator_profiles_from_manifest(manifest_path: Path) -> dict[str, VerilatorProfile]:
    try:
        manifest = load_artifact_manifest(manifest_path, validate_paths=False)
    except ArtifactManifestError as exc:
        raise SimulationProfileError(f"{manifest_path.as_posix()}: {exc}") from exc

    artifacts_by_id = _artifacts_by_id(manifest.artifacts)
    profiles: dict[str, VerilatorProfile] = {}
    for artifact in manifest.artifacts:
        if artifact.kind != "metadata" or artifact.role != SIMULATION_PROFILE_ROLE:
            continue
        if artifact.format and artifact.format != SIMULATION_PROFILE_FORMAT:
            raise SimulationProfileError(f"unsupported simulation profile artifact format {artifact.format!r}: {artifact.location}")
        profile_path = _artifact_to_path(artifact, root=manifest_path.parent)
        raw = _load_profile_yaml(profile_path)
        for profile_data in _profile_entries(raw, profile_path=profile_path):
            profile = _verilator_profile_from_mapping(profile_data, artifacts_by_id=artifacts_by_id, root=manifest_path.parent)
            profiles[profile.name] = profile
    return profiles


def _load_verilator_profile_map(profile_manifests: Iterable[Path]) -> dict[str, VerilatorProfile]:
    profiles: dict[str, VerilatorProfile] = {}
    for manifest_path in (*default_profile_manifest_paths(), *(Path(path) for path in profile_manifests)):
        profiles.update(load_verilator_profiles_from_manifest(manifest_path))
    return profiles


def _load_profile_yaml(path: Path) -> Mapping[str, Any]:
    return load_yaml_mapping(path, description="simulation profile", error_type=SimulationProfileError, schema=SIMULATION_PROFILE_SCHEMA)


def _profile_entries(raw: Mapping[str, Any], *, profile_path: Path) -> tuple[Mapping[str, Any], ...]:
    profiles = raw.get("profiles")
    if not isinstance(profiles, list):
        raise SimulationProfileError(f"simulation profile artifact must contain a profiles list: {profile_path.as_posix()}")
    entries: list[Mapping[str, Any]] = []
    for index, profile in enumerate(profiles):
        if not isinstance(profile, Mapping):
            raise SimulationProfileError(f"profile entry {index} must be a mapping: {profile_path.as_posix()}")
        entries.append(profile)
    return tuple(entries)


def _verilator_profile_from_mapping(
    profile: Mapping[str, Any],
    *,
    artifacts_by_id: Mapping[str, Artifact],
    root: Path,
) -> VerilatorProfile:
    name = _required_str(profile, "name")
    simulator = _required_str(profile, "simulator")
    if simulator != "verilator":
        raise SimulationProfileError(f"profile {name!r} uses unsupported simulator {simulator!r}; expected verilator")
    return VerilatorProfile(
        name=name,
        sources=_profile_sources(profile.get("sources", ()), artifacts_by_id=artifacts_by_id, root=root),
        top_module=_required_str(profile, "top_module"),
        expect_stdout=_required_str(profile, "expect_stdout"),
    )


def _profile_sources(value: Any, *, artifacts_by_id: Mapping[str, Artifact], root: Path) -> tuple[Path, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SimulationProfileError("profile sources must be a list")
    sources: list[Path] = []
    for item in value:
        sources.append(_profile_source(item, artifacts_by_id=artifacts_by_id, root=root))
    return tuple(sources)


def _profile_source(value: Any, *, artifacts_by_id: Mapping[str, Artifact], root: Path) -> Path:
    if isinstance(value, str):
        return _artifact_ref_to_path(value, artifacts_by_id=artifacts_by_id, root=root)
    if not isinstance(value, Mapping):
        raise SimulationProfileError("profile source entries must be artifact references or mappings")
    if "artifact" in value:
        return _artifact_ref_to_path(_required_str(value, "artifact"), artifacts_by_id=artifacts_by_id, root=root)
    if "path" in value:
        path = Path(_required_str(value, "path"))
        return path if path.is_absolute() else root / path
    if "uri" in value:
        return _package_uri_to_path(_required_str(value, "uri"))
    raise SimulationProfileError("profile source mappings must contain artifact, path, or uri")


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


def _required_str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise SimulationProfileError(f"missing required string field: {key}")
    return value


def _resource_path(resource) -> Path:
    return Path(str(resource))
