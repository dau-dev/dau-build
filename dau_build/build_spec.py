from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ccflow import BaseModel

from dau_build.artifact_bundle import ArtifactBundle, ArtifactBundleError, is_hdl_source_artifact, load_artifact_bundle, source_language_from_path
from dau_build.packaging import Artifact, ArtifactManifest, ArtifactManifestError, artifact_modules, artifact_with_modules, load_artifact_manifest
from dau_build.svparser import Design

SUPPORTED_BACKENDS = frozenset(("none", "vivado"))

# Generic staging-window defaults for generated register tops. dau-build is
# public and independent of any DAU package; the dau integration suite pins
# these to the DAU stream-job register contract so they cannot drift.
DEFAULT_INPUT_BUFFER_ADDRESS = 0x0000_0000
DEFAULT_INPUT_BUFFER_BYTES = 0x0010_0000
DEFAULT_OUTPUT_BUFFER_ADDRESS = 0x0010_0000
DEFAULT_OUTPUT_BUFFER_BYTES = 0x0010_0000
DEFAULT_RESULT_BYTES = 136

# canonical aggregation operator tokens, in wire-opcode order
_OPERATOR_TOKENS = ("min", "max", "sum", "count")


class DauBuildSpecError(ValueError):
    pass


@dataclass(frozen=True)
class DauBuildSpec:
    name: str
    top_name: str
    platform: str
    shell: str
    artifact_stem: str
    register_map_version: str
    stream_protocol_version: str
    clock: str
    reset: str
    operators: tuple[str, ...]
    sources: tuple[Path, ...]
    modules: tuple[str, ...]
    metadata: tuple[Path, ...] = ()
    binary_assets: tuple[Path, ...] = ()
    artifact_manifests: tuple[Path, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    artifact_bundle: ArtifactBundle = field(default_factory=lambda: ArtifactBundle(name="", entries=()), compare=False)
    backend: str = "none"


@dataclass(frozen=True)
class DauBuildArtifacts:
    manifest_path: Path
    top_sv_path: Path
    artifact_manifest_path: Path
    manifest_text: str
    top_sv_text: str
    artifact_manifest_text: str


class BuildSpec(BaseModel):
    """The user-authored build spec as Hydra-composable config (a `spec=`
    config group entry). Scalar fields plus source/metadata/manifest path
    lists resolved against ``base_dir``. ``resolve()`` performs the domain
    resolution — artifact-bundle loading, source dedup — into the
    ``DauBuildSpec`` the build tasks consume; the two are deliberately
    separate (config vs domain object)."""

    name: str
    top_name: str
    platform: str
    shell: str
    artifact_stem: str
    register_map_version: str
    stream_protocol_version: str
    clock: str
    reset: str
    operators: tuple[str, ...]
    modules: tuple[str, ...]
    sources: tuple[str, ...] = ()
    metadata: tuple[str, ...] = ()
    binary_assets: tuple[str, ...] = ()
    artifact_manifests: tuple[str, ...] = ()
    backend: str = "none"
    base_dir: Path = Path(".")

    def resolve(self) -> DauBuildSpec:
        if self.backend not in SUPPORTED_BACKENDS:
            raise DauBuildSpecError(f"unsupported backend: {self.backend}")
        spec_root = self.base_dir
        artifact_manifests = _checked_paths(tuple(_resolve_spec_path(spec_root, value) for value in self.artifact_manifests), "artifact manifest")
        direct_sources = _checked_paths(tuple(_resolve_spec_path(spec_root, value) for value in self.sources), "source")
        direct_metadata = _checked_paths(tuple(_resolve_spec_path(spec_root, value) for value in self.metadata), "metadata")
        direct_binary_assets = _checked_paths(tuple(_resolve_spec_path(spec_root, value) for value in self.binary_assets), "binary asset")
        direct_artifacts = (
            *(_direct_source_artifact(source) for source in direct_sources),
            *(_direct_metadata_artifact(metadata) for metadata in direct_metadata),
            *(_direct_binary_artifact(binary) for binary in direct_binary_assets),
        )
        try:
            artifact_bundle = load_artifact_bundle(artifact_manifests, direct_artifacts=tuple(direct_artifacts), name=self.name)
        except ArtifactBundleError as exc:
            raise DauBuildSpecError(str(exc)) from exc
        manifest_artifacts = tuple(entry.artifact for entry in artifact_bundle.entries if entry.manifest_path is not None)
        manifest_sources = tuple(artifact.path for artifact in manifest_artifacts if artifact.path is not None and is_hdl_source_artifact(artifact))
        sources = _unique_paths((*direct_sources, *manifest_sources))
        if not sources:
            if artifact_manifests:
                raise DauBuildSpecError(
                    "artifact manifest input(s) do not provide HDL source artifacts: " + ", ".join(path.as_posix() for path in artifact_manifests)
                )
            raise DauBuildSpecError("sources or artifact_manifests must provide at least one HDL source")
        metadata = _unique_paths(
            (*direct_metadata, *(artifact.path for artifact in manifest_artifacts if artifact.path is not None and artifact.kind == "metadata"))
        )
        binary_assets = _unique_paths(
            (*direct_binary_assets, *(artifact.path for artifact in manifest_artifacts if artifact.path is not None and artifact.kind == "binary"))
        )
        return DauBuildSpec(
            name=self.name,
            top_name=self.top_name,
            platform=self.platform,
            shell=self.shell,
            artifact_stem=self.artifact_stem,
            register_map_version=self.register_map_version,
            stream_protocol_version=self.stream_protocol_version,
            clock=self.clock,
            reset=self.reset,
            operators=self.operators,
            sources=sources,
            metadata=metadata,
            binary_assets=binary_assets,
            artifact_manifests=artifact_manifests,
            artifacts=manifest_artifacts,
            artifact_bundle=artifact_bundle,
            modules=self.modules,
            backend=self.backend,
        )


def build_spec_from_mapping(raw: dict[str, Any], *, base_dir: Path) -> BuildSpec:
    """Construct a ``BuildSpec`` from a raw config mapping (a loaded spec
    yaml), pulling the known keys — the config half of the old loader."""
    return BuildSpec(
        name=_required_str(raw, "name"),
        top_name=_required_str(raw, "top_name"),
        platform=_required_str(raw, "platform"),
        shell=_required_str(raw, "shell"),
        artifact_stem=_required_str(raw, "artifact_stem"),
        register_map_version=_required_str(raw, "register_map_version"),
        stream_protocol_version=_required_str(raw, "stream_protocol_version"),
        clock=_required_str(raw, "clock"),
        reset=_required_str(raw, "reset"),
        operators=_required_str_tuple(raw, "operators"),
        modules=_required_str_tuple(raw, "modules"),
        sources=_optional_str_tuple(raw, "sources"),
        metadata=_optional_str_tuple(raw, "metadata"),
        binary_assets=_optional_str_tuple(raw, "binary_assets"),
        artifact_manifests=_optional_str_tuple(raw, "artifact_manifests"),
        backend=str(raw.get("backend", "none")),
        base_dir=base_dir,
    )


def load_dau_build_spec(path: Path) -> DauBuildSpec:
    """Back-compatible loader: parse the spec yaml into a ``BuildSpec`` and
    resolve it. New code composes ``BuildSpec`` through the ``spec`` Hydra
    config group instead of parsing a path."""
    return build_spec_from_mapping(_load_yaml_mapping(path), base_dir=path.parent).resolve()


def generate_dau_build_artifacts(spec: DauBuildSpec, *, output_root: Path) -> DauBuildArtifacts:
    design = Design.from_files(list(spec.sources))
    missing_modules = tuple(module_name for module_name in spec.modules if module_name not in design.modules)
    if missing_modules:
        raise DauBuildSpecError(f"build spec references unknown module(s): {', '.join(missing_modules)}")

    top_sv_path = output_root / "generated" / f"{spec.top_name}.sv"
    manifest_path = output_root / f"{spec.artifact_stem}.manifest"
    artifact_manifest_path = output_root / f"{spec.artifact_stem}.artifacts.yaml"
    top_sv_text = design.generate_dau_top_sv(
        name=spec.top_name,
        module_names=list(spec.modules),
        clk=spec.clock,
        reset=spec.reset,
        register_map_version=_contract_version_to_u32(spec.register_map_version),
        stream_protocol_version=_contract_version_to_u32(spec.stream_protocol_version),
        operator_bitmap=_operator_bitmap(spec.operators),
        input_buffer_address=DEFAULT_INPUT_BUFFER_ADDRESS,
        input_buffer_bytes=DEFAULT_INPUT_BUFFER_BYTES,
        output_buffer_address=DEFAULT_OUTPUT_BUFFER_ADDRESS,
        output_buffer_bytes=DEFAULT_OUTPUT_BUFFER_BYTES,
        result_bytes=DEFAULT_RESULT_BYTES,
    )
    artifact_manifest = dau_artifact_manifest(spec, design=design, top_sv_path=top_sv_path.relative_to(output_root))
    manifest_text = dau_build_manifest_text(
        spec,
        top_sv_path=top_sv_path.relative_to(output_root),
        manifest_path=manifest_path.relative_to(output_root),
        artifact_manifest_path=artifact_manifest_path.relative_to(output_root),
    )
    return DauBuildArtifacts(
        manifest_path=manifest_path,
        top_sv_path=top_sv_path,
        artifact_manifest_path=artifact_manifest_path,
        manifest_text=manifest_text,
        top_sv_text=top_sv_text,
        artifact_manifest_text=artifact_manifest.to_yaml_text(),
    )


def write_dau_build_artifacts(spec: DauBuildSpec, *, output_root: Path) -> DauBuildArtifacts:
    artifacts = generate_dau_build_artifacts(spec, output_root=output_root)
    artifacts.top_sv_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.artifact_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.top_sv_path.write_text(artifacts.top_sv_text, encoding="utf-8")
    artifacts.manifest_path.write_text(artifacts.manifest_text, encoding="utf-8")
    artifacts.artifact_manifest_path.write_text(artifacts.artifact_manifest_text, encoding="utf-8")
    return artifacts


def dau_build_manifest_text(spec: DauBuildSpec, *, top_sv_path: Path, manifest_path: Path, artifact_manifest_path: Path) -> str:
    items: list[tuple[str, str]] = [
        ("builder", "dau_build.build_spec"),
        ("name", spec.name),
        ("platform", spec.platform),
        ("shell", spec.shell),
        ("artifact_stem", spec.artifact_stem),
        ("manifest", manifest_path.as_posix()),
        ("artifact_manifest", artifact_manifest_path.as_posix()),
        ("top_name", spec.top_name),
        ("top_sv", top_sv_path.as_posix()),
        ("register_map_version", spec.register_map_version),
        ("stream_protocol_version", spec.stream_protocol_version),
        ("clock", spec.clock),
        ("reset", spec.reset),
        ("operators", ",".join(spec.operators)),
        *design_manifest_items(spec),
        ("modules", ",".join(spec.modules)),
        ("sources", str(len(spec.sources))),
        ("backend", spec.backend),
    ]
    items.extend((f"source_{index}", source.as_posix()) for index, source in enumerate(spec.sources))
    items.extend((f"metadata_{index}", metadata.as_posix()) for index, metadata in enumerate(spec.metadata))
    items.extend((f"binary_asset_{index}", binary.as_posix()) for index, binary in enumerate(spec.binary_assets))
    return "\n".join(f"{key}={value}" for key, value in items) + "\n"


def dau_artifact_manifest(spec: DauBuildSpec, *, design: Design, top_sv_path: Path) -> ArtifactManifest:
    modules_by_source = _modules_by_source_path(design)
    pathless_artifacts = tuple(_artifact_with_discovered_modules(artifact, modules_by_source) for artifact in spec.artifacts if artifact.path is None)
    artifacts_by_path = {
        _artifact_key(artifact.path): _artifact_with_discovered_modules(artifact, modules_by_source)
        for artifact in spec.artifacts
        if artifact.path is not None
    }
    for source in spec.sources:
        artifacts_by_path.setdefault(
            _artifact_key(source),
            Artifact(
                path=source,
                kind="source",
                role="hdl-source",
                language=_source_language(source),
                provides=(),
            ),
        )
    for metadata in spec.metadata:
        artifacts_by_path.setdefault(
            _artifact_key(metadata), Artifact(path=metadata, kind="metadata", role=_metadata_role(metadata), format=_artifact_format(metadata))
        )
    for binary in spec.binary_assets:
        artifacts_by_path.setdefault(
            _artifact_key(binary), Artifact(path=binary, kind="binary", role=_binary_role(binary), format=_binary_format(binary))
        )
    source_artifacts = tuple(
        artifact_with_modules(artifact, modules_by_source.get(artifact.path.resolve(), ())) if artifact.path is not None else artifact
        for artifact in artifacts_by_path.values()
    )
    artifacts = (
        pathless_artifacts
        + source_artifacts
        + (artifact_with_modules(Artifact(path=top_sv_path, kind="source", role="generated-top", language="systemverilog"), (spec.top_name,)),)
    )
    return ArtifactManifest(name=spec.name, intent="output", artifacts=artifacts)


def main(argv: list[str] | None = None) -> int:
    import sys

    arguments = sys.argv[1:] if argv is None else argv
    if arguments and _looks_like_override_request(arguments[0]):
        from dau_build.build_steps import BuildStepError, execute_override_request

        try:
            result = execute_override_request(arguments)
        except (BuildStepError, DauBuildSpecError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(result.message)
        return 0

    parser = argparse.ArgumentParser(description="Build DAU hardware artifacts from a declarative build spec")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Generate DAU top-level SystemVerilog and manifest artifacts")
    build_parser.add_argument("--spec", required=True, type=Path, help="Path to a DAU build YAML spec")
    build_parser.add_argument("--out", required=True, type=Path, help="Output directory for generated artifacts")

    inspect_parser = subparsers.add_parser("inspect", help="Print the resolved DAU build spec summary")
    inspect_parser.add_argument("--spec", required=True, type=Path, help="Path to a DAU build YAML spec")

    validate_parser = subparsers.add_parser("validate", help="Validate a DAU build spec or generated artifact bundle")
    validate_target = validate_parser.add_mutually_exclusive_group(required=True)
    validate_target.add_argument("--spec", type=Path, help="Path to a DAU build YAML spec")
    validate_target.add_argument("--manifest", type=Path, help="Path to a generated DAU artifact manifest")
    validate_parser.add_argument("--root", type=Path, help="Artifact bundle root; defaults to the manifest parent")

    args = parser.parse_args(arguments)
    try:
        if args.command == "build":
            artifacts = write_dau_build_artifacts(load_dau_build_spec(args.spec), output_root=args.out)
            print(f"dau-build-artifacts\tmanifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path}")
            return 0
        if args.command == "inspect":
            print(dau_build_spec_summary(load_dau_build_spec(args.spec)))
            return 0
        if args.command == "validate" and args.spec is not None:
            load_dau_build_spec(args.spec)
            print(f"dau-build-spec-valid\tspec={args.spec}")
            return 0
        if args.command == "validate" and args.manifest is not None:
            top_sv_path = validate_dau_build_artifact_bundle(args.manifest, root=args.root)
            print(f"dau-build-artifacts-valid\tmanifest={args.manifest} top_sv={top_sv_path}")
            return 0
    except DauBuildSpecError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 1


def main_callable_steps(argv: list[str] | None = None) -> int:
    import sys

    from dau_build.build_steps import BuildStepError, execute_override_step

    arguments = sys.argv[1:] if argv is None else argv
    try:
        result = execute_override_step(arguments)
    except (BuildStepError, DauBuildSpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


def _looks_like_override_request(argument: str) -> bool:
    normalized_argument = argument[1:] if argument.startswith("+") else argument
    return "=" in normalized_argument


def dau_build_spec_summary(spec: DauBuildSpec) -> str:
    summary = (
        "dau-build-spec\t"
        f"name={spec.name} "
        f"platform={spec.platform} "
        f"shell={spec.shell} "
        f"modules={','.join(spec.modules)} "
        f"sources={len(spec.sources)} "
        f"clock={spec.clock} "
        f"reset={spec.reset} "
        f"backend={spec.backend}"
    )
    if not spec.artifact_manifests:
        return summary
    lines = [summary]
    lines.extend(f"manifest\tindex={index} path={path.as_posix()}" for index, path in enumerate(spec.artifact_bundle.manifest_paths))
    for kind in ("source", "metadata", "binary"):
        entries = tuple(entry for entry in spec.artifact_bundle.entries_for_kind(kind) if entry.manifest_path is not None)
        for index, entry in enumerate(entries):
            artifact = entry.artifact
            details = [f"{kind}\tindex={index}", f"path={artifact.path.as_posix()}", f"role={artifact.role}"]
            if artifact.kind == "source":
                details.append(f"language={artifact.language}")
            if artifact.kind in ("metadata", "binary"):
                details.append(f"format={artifact.format}")
            details.append(f"origin={entry.origin}")
            lines.append(" ".join(details))
    return "\n".join(lines)


def validate_dau_build_artifact_bundle(manifest_path: Path, *, root: Path | None = None) -> Path:
    bundle_root = root if root is not None else manifest_path.parent
    if not manifest_path.is_file():
        raise DauBuildSpecError(f"missing manifest: {manifest_path}")
    manifest = _parse_manifest_text(manifest_path.read_text(encoding="utf-8"))
    required_keys = ("builder", "manifest", "artifact_manifest", "top_name", "top_sv", "clock", "reset", "modules", "sources", "backend")
    missing_keys = tuple(key for key in required_keys if not manifest.get(key))
    if missing_keys:
        raise DauBuildSpecError(f"manifest missing required key(s): {', '.join(missing_keys)}")
    if manifest["builder"] != "dau_build.build_spec":
        raise DauBuildSpecError(f"unsupported manifest builder: {manifest['builder']}")
    top_sv_path = _bundle_path(bundle_root, Path(manifest["top_sv"]))
    if not top_sv_path.is_file():
        raise DauBuildSpecError(f"missing generated top SystemVerilog: {top_sv_path}")
    artifact_manifest_path = _bundle_path(bundle_root, Path(manifest["artifact_manifest"]))
    if not artifact_manifest_path.is_file():
        raise DauBuildSpecError(f"missing artifact manifest: {artifact_manifest_path}")
    try:
        artifact_manifest = load_artifact_manifest(artifact_manifest_path, validate_paths=True, root=bundle_root)
    except ArtifactManifestError as exc:
        raise DauBuildSpecError(str(exc)) from exc
    if not _artifact_manifest_includes_generated_top(artifact_manifest, Path(manifest["top_sv"])):
        raise DauBuildSpecError(f"artifact manifest does not include generated top: {manifest['top_sv']}")
    return top_sv_path


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise DauBuildSpecError(f"build spec missing required string field: {key}")
    return value


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    from dau_build.packaging import load_yaml_mapping

    return load_yaml_mapping(path, description="build spec", error_type=DauBuildSpecError)


def _required_str_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise DauBuildSpecError(f"{key} must be a non-empty list of strings")
    if not value:
        raise DauBuildSpecError(f"{key} must contain at least one entry")
    if not all(isinstance(item, str) and item for item in value):
        raise DauBuildSpecError(f"{key} must contain only non-empty strings")
    return tuple(value)


def _optional_str_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise DauBuildSpecError(f"{key} must be a list of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise DauBuildSpecError(f"{key} must contain only non-empty strings")
    return tuple(value)


def _contract_version_to_u32(version: str) -> int:
    parts = version.split(".")
    if len(parts) != 2:
        raise DauBuildSpecError(f"contract version must be '<major>.<minor>': {version}")
    try:
        major, minor = (int(part, 10) for part in parts)
    except ValueError as exc:
        raise DauBuildSpecError(f"contract version must contain decimal integers: {version}") from exc
    if not 0 <= major <= 0xFFFF or not 0 <= minor <= 0xFFFF:
        raise DauBuildSpecError(f"contract version components must fit in 16 bits: {version}")
    return (major << 16) | minor


def design_manifest_items(spec: DauBuildSpec) -> tuple[tuple[str, str], ...]:
    """The operator-unit inventory a build advertises to the planner, derived
    lexically from the spec operator tokens (``kind:count:ops:types`` — the
    consumer-side model lives with the consumer; dau-build stays generic)."""
    tokens = _operator_tokens(spec.operators)
    units = f"aggregation:1:{'|'.join(tokens)}:int32" if tokens else ""
    return (("design_name", spec.name), ("units", units))


def _operator_tokens(operators: tuple[str, ...]) -> tuple[str, ...]:
    tokens: list[str] = []
    for operator in operators:
        normalized = operator.lower().replace("_", "-")
        if "aggregation" in normalized:
            return _OPERATOR_TOKENS
        for token in _OPERATOR_TOKENS:
            if token in normalized and token not in tokens:
                tokens.append(token)
    return tuple(sorted(tokens, key=_OPERATOR_TOKENS.index))


def _operator_bitmap(operators: tuple[str, ...]) -> int:
    # wire opcodes are 1-based in _OPERATOR_TOKENS order
    bitmap = 0
    for token in _operator_tokens(operators):
        bitmap |= 1 << (_OPERATOR_TOKENS.index(token) + 1)
    return bitmap


def _required_paths(raw: dict[str, Any], key: str, spec_root: Path, label: str) -> tuple[Path, ...]:
    return _checked_paths(tuple(_resolve_spec_path(spec_root, value) for value in _required_str_tuple(raw, key)), label)


def _optional_paths(raw: dict[str, Any], key: str, spec_root: Path, label: str) -> tuple[Path, ...]:
    return _checked_paths(tuple(_resolve_spec_path(spec_root, value) for value in _optional_str_tuple(raw, key)), label)


def _checked_paths(paths: tuple[Path, ...], label: str) -> tuple[Path, ...]:
    missing_paths = tuple(path for path in paths if not path.is_file())
    if missing_paths:
        missing_text = ", ".join(path.as_posix() for path in missing_paths)
        raise DauBuildSpecError(f"missing {label} file(s): {missing_text}")
    return paths


def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for path in paths:
        key = _artifact_key(path)
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)
    return tuple(unique_paths)


def _resolve_spec_path(spec_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (spec_root / path).resolve()


def _parse_manifest_text(text: str) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DauBuildSpecError(f"manifest line {line_number} is missing '='")
        key, value = line.split("=", 1)
        if not key:
            raise DauBuildSpecError(f"manifest line {line_number} has an empty key")
        manifest[key] = value
    return manifest


def _modules_by_source_path(design: Design) -> dict[Path, tuple[str, ...]]:
    modules_by_source: dict[Path, list[str]] = {}
    for module_name, module in design.modules.items():
        if module.source_path is None:
            continue
        modules_by_source.setdefault(module.source_path.resolve(), []).append(module_name)
    return {source: tuple(modules) for source, modules in modules_by_source.items()}


def _artifact_with_discovered_modules(artifact: Artifact, modules_by_source: dict[Path, tuple[str, ...]]) -> Artifact:
    if artifact.path is None:
        return artifact
    discovered_modules = modules_by_source.get(artifact.path.resolve(), ())
    if not discovered_modules:
        return artifact
    modules = tuple(dict.fromkeys((*artifact_modules(artifact), *discovered_modules)))
    return artifact_with_modules(artifact, modules)


def _artifact_key(path: Path) -> str:
    return path.as_posix()


def _source_language(path: Path) -> str:
    return source_language_from_path(path)


def _direct_source_artifact(path: Path) -> Artifact:
    language = _source_language(path)
    if language not in ("systemverilog", "verilog"):
        raise DauBuildSpecError(f"unsupported source language for source file: {path.as_posix()}")
    return Artifact(path=path, kind="source", role="hdl-source", language=language)


def _direct_metadata_artifact(path: Path) -> Artifact:
    return Artifact(path=path, kind="metadata", role=_metadata_role(path), format=_artifact_format(path))


def _direct_binary_artifact(path: Path) -> Artifact:
    return Artifact(path=path, kind="binary", role=_binary_role(path), format=_binary_format(path))


def _metadata_role(path: Path) -> str:
    if path.suffix.lower() == ".xdc":
        return "constraints"
    return "metadata"


def _binary_role(path: Path) -> str:
    if path.suffix.lower() == ".bit":
        return "bitstream"
    return "binary-asset"


def _artifact_format(path: Path) -> str:
    return path.suffix.removeprefix(".").lower()


def _binary_format(path: Path) -> str:
    if path.suffix.lower() == ".bit":
        return "xilinx-bitstream"
    return _artifact_format(path)


def _artifact_manifest_includes_generated_top(artifact_manifest: ArtifactManifest, top_sv_path: Path) -> bool:
    return any(artifact.path == top_sv_path and artifact.role == "generated-top" for artifact in artifact_manifest.artifacts)


def _bundle_path(root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return root / path


if __name__ == "__main__":
    raise SystemExit(main())
