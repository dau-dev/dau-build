"""Slang-backed stream+status contract validation.

Every DAU operator tile speaks the same streaming contract (valid/ready row
stream in and out, ``last`` batch delimiter, a trailing status stream, an
optional trailing counter). Until now that contract was a naming convention
enforced by nothing — a drifted port surfaced at Vivado elaboration, deep
into a build. ``validate_stream_tile`` parses the module's real interface
with pyslang and reports every violation at test/codegen time instead.

Port names and directions are extracted straight from the syntax tree (no
dimension evaluation, so parameterized widths — ``$clog2`` localparams,
ternary expressions — do not block validation)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

try:
    from pyslang import SyntaxKind, SyntaxTree
except ImportError:  # pragma: no cover - version-dependent import layout
    from pyslang.syntax import SyntaxKind, SyntaxTree

# the stream+status contract: port name -> required direction
STREAM_TILE_INPUTS = (
    "clk",
    "rst",
    "input_valid",
    "input_data",
    "input_last",
    "output_ready",
    "status_ready",
)
STREAM_TILE_OUTPUTS = (
    "input_ready",
    "output_valid",
    "output_data",
    "output_last",
    "status_valid",
    "status_error",
    "status_error_code",
)


class StreamContractError(ValueError):
    """The module does not conform to the stream+status tile contract."""


def module_ports(sources: Sequence[Path | str], module: str) -> dict[str, str]:
    """Parse ``sources`` with pyslang and return ``{port_name: direction}``
    for ``module``'s ANSI header ports (direction is ``input``/``output``/
    ``inout``). Raises ``StreamContractError`` if the module is not found."""
    for source in sources:
        tree = SyntaxTree.fromFile(str(source))
        for member in tree.root.members:
            if member.kind != SyntaxKind.ModuleDeclaration:
                continue
            if member.header.name.value != module:
                continue
            ports: dict[str, str] = {}
            port_list = member.header.ports
            if port_list is None:
                return ports
            for port in port_list.ports:
                if port.kind != SyntaxKind.ImplicitAnsiPort:
                    continue
                # valueText strips leading trivia (comments/whitespace)
                direction = port.header.direction.valueText
                ports[port.declarator.name.value] = direction
            return ports
    raise StreamContractError(f"module {module!r} not found in {[str(s) for s in sources]}")


def validate_stream_tile(
    sources: Sequence[Path | str],
    module: str,
    *,
    count_port: str | None = None,
) -> list[str]:
    """Check ``module`` against the stream+status tile contract; return the
    list of violations (empty means conforming). Fan-out tiles with vectored
    per-lane ports conform as long as the names and directions match."""
    ports = module_ports(sources, module)
    violations: list[str] = []
    for name in STREAM_TILE_INPUTS:
        if name not in ports:
            violations.append(f"missing input port {name!r}")
        elif ports[name] != "input":
            violations.append(f"port {name!r} must be an input, is {ports[name]!r}")
    for name in STREAM_TILE_OUTPUTS:
        if name not in ports:
            violations.append(f"missing output port {name!r}")
        elif ports[name] != "output":
            violations.append(f"port {name!r} must be an output, is {ports[name]!r}")
    if count_port is not None:
        if count_port not in ports:
            violations.append(f"missing declared count port {count_port!r}")
        elif ports[count_port] != "output":
            violations.append(f"count port {count_port!r} must be an output, is {ports[count_port]!r}")
    return violations
