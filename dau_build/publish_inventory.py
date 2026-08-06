"""Project a private shell-build manifest into a publishable inventory.

A shell-build manifest is a full build record: it names every contributing
HDL source by absolute path, records the git state of each containing
repository, and keeps the console log. That is the right content for a
private artifact — it is how a build is reproduced and audited — and the
wrong content to hand out, because those paths and repository names
disclose the core library's table of contents and a local filesystem
layout.

This module produces a SEPARATE manifest carrying only what a consumer of
a bitstream needs: the bitstream itself by digest, and the capabilities it
advertises.

The projection **whitelists**. A denylist rots the first time a metadata
key is added upstream — the new key ships by default and nobody notices
until it is public. Here an unlisted key simply does not appear, so the
failure mode of forgetting to update this module is an inventory that is
missing something, not one that discloses something.

Capabilities come from the capability contract as PLAIN DATA. This module
never imports the core package: it takes the already-decoded mapping, so
the public build layer stays independent of the private one.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from artlink.artifact import Artifact, Capability

from .packaging import ArtifactManifest, load_artifact_manifest

__all__ = ("PUBLISHED_METADATA_KEYS", "publish_inventory", "write_published_inventory")

# every metadata key that may cross into a published inventory, and why.
# Anything absent here is withheld — including keys that do not exist yet.
PUBLISHED_METADATA_KEYS: tuple[str, ...] = (
    "platform",  # which board family the bitstream targets
    "part",  # the exact device it was built for
    "top_module",  # the module a loader instantiates
    "lane_count",  # how many lanes the composition runs
    "register_map_version",  # the wire contract a host must speak
)

# withheld deliberately, recorded so the decision is visible rather than
# implied by omission:
#   source_repositories  - local paths + git state of private repositories
#   composition_spec     - the exact datapath recipe; the private cache key
#   spec_hash            - pins the canonical spec version publicly
#   resource_estimate_*  - measured area and timing are competitive data
#   wns_ns, build_status - build-internal outcomes
#
# Artifact roles need no denylist: the projection keeps the one bitstream and
# drops every other artifact by construction.


def _capabilities_from_contract(contract: Mapping[str, Any]) -> tuple[Capability, ...]:
    """The operator inventory a bitstream advertises, as artlink
    capabilities.

    Takes the contract mapping as plain data — the shape
    ``{"lanes": [{"tile": {"core": ..., "unit_kind": ..., "opcodes": [...]}}]}``
    — so this module needs nothing from the package that produced it.
    Identical lane tiles collapse into one capability with a count, which
    is what makes the inventory readable for a wide composition.
    """
    counts: dict[tuple[str, str], int] = {}
    opcodes: dict[tuple[str, str], tuple[str, ...]] = {}
    for lane in contract.get("lanes", ()):
        tile = lane.get("tile") or {}
        core = tile.get("core")
        if not core:
            continue
        key = (core, tile.get("unit_kind") or "")
        counts[key] = counts.get(key, 0) + 1
        opcodes.setdefault(key, tuple(tile.get("opcodes", ())))
    capabilities = []
    for (core, unit_kind), count in sorted(counts.items()):
        metadata: dict[str, Any] = {"count": count}
        if unit_kind:
            metadata["unit_kind"] = unit_kind
        if opcodes[(core, unit_kind)]:
            metadata["opcodes"] = list(opcodes[(core, unit_kind)])
        capabilities.append(Capability(kind="dau-operator", name=core, metadata=metadata))
    return tuple(capabilities)


def publish_inventory(
    manifest: ArtifactManifest,
    *,
    contract: Mapping[str, Any] | None = None,
    name: str | None = None,
) -> ArtifactManifest:
    """Project ``manifest`` into a publishable inventory.

    Keeps exactly one artifact — the bitstream, by digest — carrying the
    capabilities the composition advertises, plus the whitelisted metadata
    keys. Raises when the source manifest has no bitstream, because an
    inventory without one describes nothing, and when the build did not
    close timing, because publishing an image is a claim that it runs.

    The timing refusal is why ``wns_ns`` can stay withheld: a published
    inventory asserts closure by existing, so the margin never has to be
    disclosed to be trusted. A build that missed is a build nobody should
    flash, and the ladder that recovers a wedged board is not something a
    consumer of a published image has.
    """
    bitstreams = [artifact for artifact in manifest.artifacts if artifact.role == "bitstream"]
    if len(bitstreams) != 1:
        raise ValueError(f"a published inventory needs exactly one bitstream artifact, found {len(bitstreams)}")
    source = bitstreams[0]

    status = manifest.metadata.get("build_status")
    if status is not None and status != "built":
        raise ValueError(f"refusing to publish a bitstream whose build_status is {status!r}, not 'built'")
    wns = manifest.metadata.get("wns_ns")
    if wns is not None and float(wns) < 0.0:
        raise ValueError(f"refusing to publish a bitstream that missed timing (wns_ns={wns})")

    metadata = {key: manifest.metadata[key] for key in PUBLISHED_METADATA_KEYS if key in manifest.metadata}
    capabilities = _capabilities_from_contract(contract) if contract else ()

    published = Artifact(
        # the NAME only, never the build-host path it was produced at
        path=Path(source.path).name if source.path else None,
        kind="binary",
        role="bitstream",
        # which image this is: an SRAM load wants the .bit and an SPI flash
        # wants the .mcs, so a consumer that cannot tell them apart cannot
        # pick a load path. Neither says anything about the composition.
        format=source.format,
        media_type=source.media_type,
        digest=source.digest,
        provides=capabilities,
    )
    return ArtifactManifest(
        name=name or manifest.name,
        intent="output",
        artifacts=(published,),
        metadata=metadata,
    )


def write_published_inventory(
    manifest_path: Path,
    destination: Path,
    *,
    contract: Mapping[str, Any] | None = None,
    name: str | None = None,
) -> Path:
    """Read a shell-build manifest, project it, and write the inventory."""
    projected = publish_inventory(load_artifact_manifest(manifest_path), contract=contract, name=name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(projected.to_yaml_text(), encoding="utf-8")
    return destination
