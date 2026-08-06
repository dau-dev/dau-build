from __future__ import annotations

from pathlib import Path

import pytest

from dau_build.packaging import load_artifact_manifest
from dau_build.publish_inventory import PUBLISHED_METADATA_KEYS, publish_inventory, write_published_inventory
from dau_build.shell_build import shell_build_manifest

_CONTRACT = {
    "lanes": [
        {"tile": {"core": "int32-example-sort", "unit_kind": "sort", "opcodes": []}},
        {"tile": {"core": "int32-example-sort", "unit_kind": "sort", "opcodes": []}},
        {"tile": {"core": "int32-example-sum", "unit_kind": "aggregation", "opcodes": ["sum", "count"]}},
    ]
}


def _build_root(tmp_path: Path) -> Path:
    (tmp_path / "dau_mm_job.bit").write_bytes(b"bitstream")
    (tmp_path / "console.log").write_text("build chatter")
    (tmp_path / "build_mm_job.tcl").write_text("# tcl")
    (tmp_path / "utilization_mm.rpt").write_text("util")
    sources = tmp_path / "hdl"
    sources.mkdir()
    (sources / "dau_int32_private_tile.sv").write_text("module dau_int32_private_tile; endmodule")
    return tmp_path


def _private_manifest(tmp_path: Path):
    root = _build_root(tmp_path)
    return shell_build_manifest(
        root,
        name="example-shell",
        source_paths=(root / "hdl" / "dau_int32_private_tile.sv",),
        metadata={
            "platform": "example-board",
            "part": "xc7a200tfbg484-2",
            "top_module": "dau_mm_job",
            "spec_hash": "5:deadbeefcafe",
            "composition_spec": '{"lanes":[{"tile":{"core":"int32-example-sort"}}]}',
            "resource_estimate_lut": "3116",
            "wns_ns": 0.161,
            "build_status": "built",
        },
    )


def test_the_private_manifest_carries_what_a_build_record_needs(tmp_path: Path) -> None:
    """The projection exists because the source manifest is a full build
    record — this pins what it holds, so a change there is visible here."""
    manifest = _private_manifest(tmp_path)
    assert {artifact.role for artifact in manifest.artifacts} >= {"bitstream", "hdl-source", "build-log"}
    assert "source_repositories" in manifest.metadata
    assert "composition_spec" in manifest.metadata


def test_publish_inventory_keeps_only_the_bitstream_and_its_capabilities(tmp_path: Path) -> None:
    published = publish_inventory(_private_manifest(tmp_path), contract=_CONTRACT)

    (artifact,) = published.artifacts
    assert artifact.role == "bitstream"
    # the NAME, never the build-host path it was produced at
    assert Path(artifact.path).name == "dau_mm_job.bit" and Path(artifact.path).parent == Path(".")
    assert artifact.digest is not None

    advertised = {capability.name: dict(capability.metadata) for capability in artifact.provides}
    assert advertised == {
        "int32-example-sort": {"count": 2, "unit_kind": "sort"},
        "int32-example-sum": {"count": 1, "unit_kind": "aggregation", "opcodes": ["sum", "count"]},
    }


def test_publish_inventory_withholds_everything_not_whitelisted(tmp_path: Path) -> None:
    """A denylist rots the first time a metadata key is added upstream. The
    projection whitelists, so forgetting to update it yields an inventory
    that is missing something rather than one that discloses something."""
    published = publish_inventory(_private_manifest(tmp_path), contract=_CONTRACT)
    text = published.to_yaml_text()

    assert set(published.metadata) <= set(PUBLISHED_METADATA_KEYS)
    for withheld in (
        "dau_int32_private_tile",  # the core library's table of contents
        "source_repositories",  # local paths + git state of private repos
        "composition_spec",  # the exact datapath recipe
        "spec_hash",
        "resource_estimate_lut",
        "wns_ns",
        "console.log",
        str(tmp_path),  # any build-host path at all
    ):
        assert withheld not in text, f"published inventory disclosed {withheld!r}"


def test_an_unknown_metadata_key_is_withheld_by_default(tmp_path: Path) -> None:
    """The property that makes the whitelist worth having."""
    manifest = _private_manifest(tmp_path)
    manifest = manifest.model_copy(update={"metadata": {**manifest.metadata, "a_key_added_later": "secret"}})
    published = publish_inventory(manifest, contract=_CONTRACT)
    assert "a_key_added_later" not in published.metadata
    assert "secret" not in published.to_yaml_text()


def test_publish_inventory_refuses_a_manifest_without_exactly_one_bitstream(tmp_path: Path) -> None:
    manifest = _private_manifest(tmp_path)
    without = manifest.model_copy(update={"artifacts": tuple(a for a in manifest.artifacts if a.role != "bitstream")})
    with pytest.raises(ValueError, match="exactly one bitstream"):
        publish_inventory(without)


def test_publish_inventory_refuses_a_bitstream_that_missed_timing(tmp_path: Path) -> None:
    """Publishing an image claims it runs. The margin itself stays withheld,
    so this refusal is what makes the claim worth anything."""
    manifest = _private_manifest(tmp_path)
    missed = manifest.model_copy(update={"metadata": {**manifest.metadata, "wns_ns": -0.348}})
    with pytest.raises(ValueError, match="missed timing"):
        publish_inventory(missed, contract=_CONTRACT)


def test_publish_inventory_refuses_an_unfinished_build(tmp_path: Path) -> None:
    manifest = _private_manifest(tmp_path)
    unfinished = manifest.model_copy(update={"metadata": {**manifest.metadata, "build_status": "planned"}})
    with pytest.raises(ValueError, match="build_status"):
        publish_inventory(unfinished, contract=_CONTRACT)


def test_the_published_bitstream_says_which_image_kind_it_is(tmp_path: Path) -> None:
    """An SRAM load wants the .bit and an SPI flash wants the .mcs."""
    published = publish_inventory(_private_manifest(tmp_path), contract=_CONTRACT)
    (artifact,) = published.artifacts
    assert artifact.format == "bit"


def test_write_published_inventory_round_trips(tmp_path: Path) -> None:
    build_root = tmp_path / "build"
    build_root.mkdir()
    manifest = _private_manifest(build_root)
    manifest_path = tmp_path / "shell-build.artifacts.yaml"
    manifest_path.write_text(manifest.to_yaml_text(), encoding="utf-8")

    destination = write_published_inventory(manifest_path, tmp_path / "published" / "inventory.yaml", contract=_CONTRACT)

    reloaded = load_artifact_manifest(destination)
    assert len(reloaded.artifacts) == 1
    assert {capability.name for capability in reloaded.artifacts[0].provides} == {"int32-example-sort", "int32-example-sum"}
