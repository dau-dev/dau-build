from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import toml

from dau_build.svparser import Design


@dataclass(frozen=True)
class DauBuildSpec:
    name: str
    top_name: str
    platform: str
    shell: str
    artifact_stem: str
    register_map_version: str
    stream_protocol_version: str
    operators: tuple[str, ...]
    sources: tuple[Path, ...]
    modules: tuple[str, ...]
    backend: str = "none"


@dataclass(frozen=True)
class DauBuildArtifacts:
    manifest_path: Path
    top_sv_path: Path
    manifest_text: str
    top_sv_text: str


def load_dau_build_spec(path: Path) -> DauBuildSpec:
    spec_root = path.parent
    raw = toml.load(path)
    return DauBuildSpec(
        name=_required_str(raw, "name"),
        top_name=_required_str(raw, "top_name"),
        platform=_required_str(raw, "platform"),
        shell=_required_str(raw, "shell"),
        artifact_stem=_required_str(raw, "artifact_stem"),
        register_map_version=_required_str(raw, "register_map_version"),
        stream_protocol_version=_required_str(raw, "stream_protocol_version"),
        operators=tuple(raw.get("operators", ())),
        sources=tuple(_resolve_spec_path(spec_root, source) for source in raw.get("sources", ())),
        modules=tuple(raw.get("modules", ())),
        backend=str(raw.get("backend", "none")),
    )


def generate_dau_build_artifacts(spec: DauBuildSpec, *, output_root: Path) -> DauBuildArtifacts:
    design = Design.from_files(list(spec.sources))
    missing_modules = tuple(module_name for module_name in spec.modules if module_name not in design.modules)
    if missing_modules:
        raise ValueError(f"build spec references unknown module(s): {', '.join(missing_modules)}")

    top_sv_path = output_root / "generated" / f"{spec.top_name}.sv"
    manifest_path = output_root / f"{spec.artifact_stem}.manifest"
    top_sv_text = design.generate_top_sv(name=spec.top_name, module_names=list(spec.modules))
    manifest_text = dau_build_manifest_text(
        spec, top_sv_path=top_sv_path.relative_to(output_root), manifest_path=manifest_path.relative_to(output_root)
    )
    return DauBuildArtifacts(
        manifest_path=manifest_path,
        top_sv_path=top_sv_path,
        manifest_text=manifest_text,
        top_sv_text=top_sv_text,
    )


def write_dau_build_artifacts(spec: DauBuildSpec, *, output_root: Path) -> DauBuildArtifacts:
    artifacts = generate_dau_build_artifacts(spec, output_root=output_root)
    artifacts.top_sv_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.top_sv_path.write_text(artifacts.top_sv_text, encoding="utf-8")
    artifacts.manifest_path.write_text(artifacts.manifest_text, encoding="utf-8")
    return artifacts


def dau_build_manifest_text(spec: DauBuildSpec, *, top_sv_path: Path, manifest_path: Path) -> str:
    items: list[tuple[str, str]] = [
        ("builder", "dau_build.build_spec"),
        ("name", spec.name),
        ("platform", spec.platform),
        ("shell", spec.shell),
        ("artifact_stem", spec.artifact_stem),
        ("manifest", manifest_path.as_posix()),
        ("top_name", spec.top_name),
        ("top_sv", top_sv_path.as_posix()),
        ("register_map_version", spec.register_map_version),
        ("stream_protocol_version", spec.stream_protocol_version),
        ("operators", ",".join(spec.operators)),
        ("modules", ",".join(spec.modules)),
        ("backend", spec.backend),
    ]
    items.extend((f"source_{index}", source.as_posix()) for index, source in enumerate(spec.sources))
    return "\n".join(f"{key}={value}" for key, value in items) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build DAU hardware artifacts from a declarative build spec")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Generate DAU top-level SystemVerilog and manifest artifacts")
    build_parser.add_argument("--spec", required=True, type=Path, help="Path to a DAU build TOML spec")
    build_parser.add_argument("--out", required=True, type=Path, help="Output directory for generated artifacts")

    args = parser.parse_args(argv)
    if args.command == "build":
        artifacts = write_dau_build_artifacts(load_dau_build_spec(args.spec), output_root=args.out)
        print(f"dau-build-artifacts\tmanifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path}")
        return 0
    return 1


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"build spec missing required string field: {key}")
    return value


def _resolve_spec_path(spec_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (spec_root / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
