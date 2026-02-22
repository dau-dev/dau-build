"""
vivado.py - Python wrapper for Vivado TCL build flows.

Provides programmatic access to common Vivado operations by generating
and executing TCL scripts.  Also usable as a CLI::

    python -m dau_build.vivado synth  --project /path/to/project.xpr
    python -m dau_build.vivado impl   --project /path/to/project.xpr
    python -m dau_build.vivado program --bitstream /path/to/file.bit
    python -m dau_build.vivado flash   --mcs /path/to/file.mcs
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

__all__ = ("VivadoRunner",)

# Path to the dau-build TCL utility library
_TCL_LIB = Path(__file__).resolve().parent / "tcl" / "util.tcl"


class VivadoRunner:
    """Execute Vivado TCL commands from Python."""

    def __init__(
        self,
        vivado_bin: Optional[str] = None,
        jobs: int = 4,
        verbose: bool = False,
    ):
        self.vivado_bin = vivado_bin or shutil.which("vivado") or "vivado"
        self.jobs = jobs
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------
    def _run_tcl(self, tcl_body: str, *, cwd: Optional[Path] = None) -> int:
        """Write *tcl_body* to a temp file and execute ``vivado -mode batch``."""
        # Prepend the dau utility library
        full_tcl = f'source "{_TCL_LIB}"\n\n{tcl_body}'

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl", delete=False) as f:
            f.write(full_tcl)
            tcl_path = f.name

        cmd = [self.vivado_bin, "-mode", "batch", "-source", tcl_path]
        if self.verbose:
            print(f"[dau-build] Running: {' '.join(cmd)}")
            print(f"[dau-build] TCL script:\n{full_tcl}")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                capture_output=not self.verbose,
                text=True,
            )
            if result.returncode != 0 and not self.verbose:
                print(result.stdout, file=sys.stderr)
                print(result.stderr, file=sys.stderr)
            return result.returncode
        finally:
            os.unlink(tcl_path)

    # ------------------------------------------------------------------
    # High-level operations
    # ------------------------------------------------------------------
    def synth(self, project: str | Path) -> int:
        """Run synthesis on *project*."""
        project = Path(project).resolve()
        tcl = f"""
open_project "{project}"
::dau::run_synthesis {self.jobs}
close_project
"""
        return self._run_tcl(tcl, cwd=project.parent)

    def impl(self, project: str | Path) -> int:
        """Run implementation (through write_bitstream) on *project*."""
        project = Path(project).resolve()
        tcl = f"""
open_project "{project}"
::dau::run_synthesis {self.jobs}
open_run synth_1
::dau::run_implementation {self.jobs}
close_project
"""
        return self._run_tcl(tcl, cwd=project.parent)

    def build(self, project: str | Path, flash_dir: Optional[str] = None) -> int:
        """Full build: synth → impl → bitstream → flash images."""
        project = Path(project).resolve()
        flash = flash_dir or str(project.parent / "mcs")
        bit = str(project.parent / "project.runs" / "impl_1" / f"{project.stem}_wrapper.bit")
        tcl = f"""
open_project "{project}"
::dau::full_build {self.jobs}
::dau::generate_flash_images "{bit}" "{flash}"
close_project
"""
        return self._run_tcl(tcl, cwd=project.parent)

    def program(self, bitstream: str | Path, target: str = "xc7a200t_0") -> int:
        """Program the FPGA via JTAG."""
        bitstream = Path(bitstream).resolve()
        tcl = f"""
::dau::program_device "{bitstream}" "{target}"
"""
        return self._run_tcl(tcl)

    def flash(self, mcs: str | Path) -> int:
        """Flash-program the SPI configuration memory."""
        mcs = Path(mcs).resolve()
        tcl = f"""
::dau::flash_device "{mcs}"
"""
        return self._run_tcl(tcl)

    def add_sources(
        self,
        project: str | Path,
        sources: Sequence[str | Path],
    ) -> int:
        """Add RTL source files to the project."""
        project = Path(project).resolve()
        src_list = " ".join(f'"{Path(s).resolve()}"' for s in sources)
        tcl = f"""
open_project "{project}"
::dau::add_rtl_sources [list {src_list}]
close_project
"""
        return self._run_tcl(tcl, cwd=project.parent)

    def run_tcl_file(self, tcl_file: str | Path, *, cwd: Optional[Path] = None) -> int:
        """Run an arbitrary TCL file with the dau utility library pre-loaded."""
        tcl_file = Path(tcl_file).resolve()
        tcl = f'source "{tcl_file}"\n'
        return self._run_tcl(tcl, cwd=cwd)


# ======================================================================
# CLI
# ======================================================================
def _cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dau-build vivado",
        description="Vivado build flow CLI (wraps TCL via dau-build utilities)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print TCL and vivado output")
    parser.add_argument("-j", "--jobs", type=int, default=4, help="Parallel jobs (default: 4)")
    parser.add_argument("--vivado", type=str, default=None, help="Path to vivado binary")

    sub = parser.add_subparsers(dest="command", required=True)

    # synth
    p = sub.add_parser("synth", help="Run synthesis")
    p.add_argument("--project", required=True, help="Path to .xpr project file")

    # impl
    p = sub.add_parser("impl", help="Run implementation through write_bitstream")
    p.add_argument("--project", required=True, help="Path to .xpr project file")

    # build
    p = sub.add_parser("build", help="Full build flow (synth + impl + flash images)")
    p.add_argument("--project", required=True, help="Path to .xpr project file")
    p.add_argument("--flash-dir", default=None, help="Output directory for flash images")

    # program
    p = sub.add_parser("program", help="Program FPGA via JTAG")
    p.add_argument("--bitstream", required=True, help="Path to .bit file")
    p.add_argument("--target", default="xc7a200t_0", help="Target device (default: xc7a200t_0)")

    # flash
    p = sub.add_parser("flash", help="Flash-program SPI configuration memory")
    p.add_argument("--mcs", required=True, help="Path to .mcs file")

    # run
    p = sub.add_parser("run", help="Run an arbitrary TCL file with dau-build utils loaded")
    p.add_argument("tcl_file", help="Path to TCL file")
    p.add_argument("--cwd", default=None, help="Working directory")

    args = parser.parse_args(argv)
    runner = VivadoRunner(vivado_bin=args.vivado, jobs=args.jobs, verbose=args.verbose)

    if args.command == "synth":
        return runner.synth(args.project)
    elif args.command == "impl":
        return runner.impl(args.project)
    elif args.command == "build":
        return runner.build(args.project, flash_dir=args.flash_dir)
    elif args.command == "program":
        return runner.program(args.bitstream, target=args.target)
    elif args.command == "flash":
        return runner.flash(args.mcs)
    elif args.command == "run":
        cwd = Path(args.cwd) if args.cwd else None
        return runner.run_tcl_file(args.tcl_file, cwd=cwd)
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
