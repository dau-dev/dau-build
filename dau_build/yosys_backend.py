"""yosys synthesis backend.

Unlike the Vivado backend, which generates a plan for a vendor tool run
elsewhere, yosys is open-source and runnable directly — so this backend
generates a yosys script and executes it, giving a real elaboration/synthesis
check (in CI, not just a plan).

Two SystemVerilog frontends are supported:

- ``verilog`` — yosys's built-in ``read_verilog -sv``. Handles plain
  synthesizable SV (the dau-build sources), needs no plugin.
- ``slang`` — the yosys-slang plugin (``read_slang``), the same slang engine
  as the project's ``pyslang`` parser. Full SV (packages, interfaces); loaded
  with ``yosys -m slang``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal

from ccflow import BaseModel

# yosys stat prints "Number of cells: N" (older) or "N cells" (0.6x+)
_CELL_COUNT_RE = re.compile(r"Number of cells:\s*(\d+)|(\d+)\s+cells\b")


class YosysBackendError(RuntimeError):
    pass


class YosysBackendRequest(BaseModel):
    top_module: str
    sources: tuple[Path, ...]
    output_root: Path
    frontend: Literal["verilog", "slang"] = "verilog"
    # run full generic synthesis (synth -top) vs. elaborate-and-check only
    synth: bool = True
    yosys: str = "yosys"
    script_name: str = "dau_yosys.ys"
    log_name: str = "yosys.log"


class YosysSynthesisResult(BaseModel):
    top_module: str
    frontend: str
    passed: bool
    returncode: int
    cell_count: int | None
    script_path: Path
    log_path: Path


def yosys_script_text(request: YosysBackendRequest) -> str:
    """The yosys script that reads the sources with the selected frontend and
    synthesizes (or elaborates and checks) the top module."""
    files = " ".join(_quote(str(source)) for source in request.sources)
    if request.frontend == "slang":
        read = f"read_slang --top {request.top_module} {files}"
    else:
        read = f"read_verilog -sv {files}"
    lines = [read]
    if request.synth:
        lines.append(f"synth -top {request.top_module}")
    else:
        lines += [f"hierarchy -top {request.top_module}", "proc", "check -assert"]
    lines.append("stat")
    return "\n".join(lines) + "\n"


def write_yosys_backend_artifacts(request: YosysBackendRequest) -> Path:
    """Write the yosys script under ``output_root`` and return its path."""
    request.output_root.mkdir(parents=True, exist_ok=True)
    script_path = request.output_root / request.script_name
    script_path.write_text(yosys_script_text(request), encoding="utf-8")
    return script_path


def run_yosys_synthesis(request: YosysBackendRequest) -> YosysSynthesisResult:
    """Write the script and run yosys. ``passed`` is the process exit status —
    yosys exits non-zero on an elaboration/synthesis error. Raises
    ``YosysBackendError`` if the yosys executable is not found."""
    script_path = write_yosys_backend_artifacts(request)
    argv = [request.yosys]
    if request.frontend == "slang":
        argv += ["-m", "slang"]
    argv += ["-s", str(script_path)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise YosysBackendError(f"yosys executable {request.yosys!r} not found") from exc
    log_path = request.output_root / request.log_name
    log_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return YosysSynthesisResult(
        top_module=request.top_module,
        frontend=request.frontend,
        passed=proc.returncode == 0,
        returncode=proc.returncode,
        cell_count=_parse_cell_count(proc.stdout),
        script_path=script_path,
        log_path=log_path,
    )


def _parse_cell_count(log: str) -> int | None:
    counts = [int(a or b) for a, b in _CELL_COUNT_RE.findall(log)]
    return counts[-1] if counts else None


def _quote(path: str) -> str:
    return f'"{path}"' if " " in path else path
