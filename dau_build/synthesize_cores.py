"""Per-core out-of-context characterization through the registry.

``SynthesizeCoresTask`` resolves cores from the ccflow registry
(``/dau-core/<core-name>``, the dau-core lernaplugin's config tree), plans
one out-of-context Vivado synthesis per core (dependency-closed sources,
``-generic`` values from the core's declared ``ParameterSpec`` defaults
merged with task overrides, the platform part, a ``create_clock`` at the
job clock), and writes the tcl + a command-plan runner under
``output_root``. With ``execute=true`` (where vivado is installed — the
synthesis host) it runs the plan and parses each utilization/timing report
into a resource-envelope summary, flagging drift against the envelope
registered in the core registry. Envelope numbers enter the registry only
from this task — never from hand-run synthesis.
"""

# NOTE: no `from __future__ import annotations` — ccflow's Flow.call
# inspects the real annotation objects on __call__ (the build_steps pattern)
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ccflow import BaseModel, Flow, NullContext

from dau_build.build_steps import BuildCallableModel, BuildStepError, BuildStepResult

__all__ = ("CoreEnvelopeReport", "SynthesizeCoresTask")

_REGISTRY_PREFIX = "/dau-core/"


class CoreEnvelopeReport(BaseModel):
    """One core's parsed OOC result, comparable to its registered envelope."""

    name: str
    module: str
    lut: int
    ff: int
    bram36: float
    dsp: int
    wns_ns: float
    met: bool
    registered_matches: bool | None = None  # None: no envelope registered


class SynthesizeCoresTask(BuildCallableModel):
    # registry paths (`/dau-core/<core-name>`; bare core names accepted)
    cores: tuple[str, ...]
    output_root: Path
    # part source: explicit `part` wins, else the composed `platform=` group
    platform: Any = None
    part: str | None = None
    clock_period_ns: float = 8.0
    # per-core `-generic` overrides, keyed by core name then parameter name;
    # an override of a parameter the core does not declare is rejected
    parameters: Mapping[str, Mapping[str, int]] = {}
    # run the plan (the synthesis host has vivado); false = handoff only
    execute: bool = False
    vivado: str = "vivado"

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:  # noqa: ARG002 (ccflow requires the name `context`)
        definitions = [self._resolve_core(entry) for entry in self.cores]
        if not definitions:
            raise BuildStepError("no cores selected; pass model.cores=[/dau-core/<name>,...]")
        part = self._part()
        self.output_root.mkdir(parents=True, exist_ok=True)
        scripts = [self._stage_core(definition, part=part) for definition in definitions]
        plan_path = self._write_plan(scripts)
        if not self.execute:
            return BuildStepResult(
                step="synthesize-cores",
                message=(
                    f"dau-build-synthesize-cores\tcores={','.join(d.name for d in definitions)} part={part} "
                    f"clock_ns={self.clock_period_ns} output_root={self.output_root} plan={plan_path} status=handoff-written"
                ),
            )
        reports = [self._run_and_parse(definition, script) for definition, script in zip(definitions, scripts)]
        drift = [report.name for report in reports if report.registered_matches is False]
        summary = " ".join(
            f"{r.name}:lut={r.lut},ff={r.ff},bram36={r.bram36},dsp={r.dsp},wns={r.wns_ns:+.3f},{'met' if r.met else 'VIOLATED'}" for r in reports
        )
        return BuildStepResult(
            step="synthesize-cores",
            message=(
                f"dau-build-synthesize-cores\tcores={','.join(d.name for d in definitions)} part={part} "
                f"clock_ns={self.clock_period_ns} output_root={self.output_root} {summary} "
                f"envelope_drift={','.join(drift) if drift else 'none'} status=synthesized"
            ),
        )

    def _resolve_core(self, entry: str):
        name = entry.removeprefix(_REGISTRY_PREFIX)
        if "/" in name:
            raise BuildStepError(f"core entry {entry!r} is not a /dau-core/<name> registry path")
        try:
            from dau_core.cores import UnknownCoreError, loaded_cores
        except ImportError as exc:
            raise BuildStepError("dau-core is not installed; the core registry is unavailable") from exc
        try:
            definitions = loaded_cores()
            if name not in definitions:
                raise UnknownCoreError(name)
            return definitions[name]
        except UnknownCoreError as exc:
            raise BuildStepError(f"unknown core {entry!r}") from exc

    def _part(self) -> str:
        if self.part is not None:
            return self.part
        part = getattr(self.platform, "part", None)
        if part:
            return part
        raise BuildStepError("no part selected; pass model.part=... or compose a platform= group")

    def _generics(self, definition) -> dict[str, int]:
        values = {name: spec.default for name, spec in definition.parameters.items()}
        overrides = self.parameters.get(definition.name, {})
        unknown = set(overrides) - set(values)
        if unknown:
            raise BuildStepError(f"core {definition.name!r} declares no parameter {sorted(unknown)}; declared: {sorted(values) or 'none'}")
        values.update(overrides)
        return values

    def _stage_core(self, definition, *, part: str) -> Path:
        from dau_core.cores import sources_for

        sources = sources_for(definition.name)
        generics = self._generics(definition)
        generic_args = "".join(f" -generic {name}={value}" for name, value in generics.items())
        reads = "\n".join(f"read_verilog -sv {path}" for path in sources)
        util_rpt = self.output_root / f"{definition.module}.util.rpt"
        timing_rpt = self.output_root / f"{definition.module}.timing.rpt"
        tcl = (
            f"{reads}\n"
            f"synth_design -top {definition.module} -part {part} -mode out_of_context{generic_args}\n"
            f"create_clock -period {self.clock_period_ns:.3f} -name clk [get_ports clk]\n"
            f"report_utilization -file {util_rpt}\n"
            f"report_timing_summary -max_paths 1 -delay_type max -file {timing_rpt}\n"
        )
        script = self.output_root / f"{definition.module}.ooc.tcl"
        script.write_text(tcl)
        return script

    def _write_plan(self, scripts: list[Path]) -> Path:
        lines = ["#!/bin/sh", "set -e"]
        for script in scripts:
            stem = script.stem.removesuffix(".ooc")
            lines.append(f"{self.vivado} -mode batch -source {script} -log {self.output_root / stem}.log -journal {self.output_root / stem}.jou")
        plan = self.output_root / "synthesize-cores.sh"
        plan.write_text("\n".join(lines) + "\n")
        plan.chmod(0o755)
        return plan

    def _run_and_parse(self, definition, script: Path) -> CoreEnvelopeReport:
        stem = script.stem.removesuffix(".ooc")
        completed = subprocess.run(
            [
                self.vivado,
                "-mode",
                "batch",
                "-source",
                str(script),
                "-log",
                f"{self.output_root / stem}.log",
                "-journal",
                f"{self.output_root / stem}.jou",
            ],
            cwd=self.output_root,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise BuildStepError(f"vivado failed for {definition.name} (exit {completed.returncode}); see {self.output_root / stem}.log")
        return self.parse_reports(definition, output_root=self.output_root)

    @staticmethod
    def parse_reports(definition, *, output_root: Path) -> CoreEnvelopeReport:
        """Parse a core's utilization/timing reports into an envelope report,
        comparing against the envelope registered in the core registry."""
        util = (output_root / f"{definition.module}.util.rpt").read_text()
        timing = (output_root / f"{definition.module}.timing.rpt").read_text()
        lut = _util_value(util, r"Slice LUTs\*?")
        ff = _util_value(util, r"Slice Registers")
        bram = _util_float(util, r"Block RAM Tile")
        dsp = _util_value(util, r"DSPs")
        slack = re.search(r"Slack \((MET|VIOLATED)\)\s*:\s*(-?[\d.]+)ns", timing)
        if slack is None:
            raise BuildStepError(f"no slack line in {definition.module}.timing.rpt")
        wns = float(slack.group(2)) if slack.group(1) == "MET" else -abs(float(slack.group(2)))
        registered = definition.resources
        matches = None
        if registered is not None:
            matches = (registered.lut, registered.ff, registered.bram36, registered.dsp) == (lut, ff, bram, dsp)
        return CoreEnvelopeReport(
            name=definition.name,
            module=definition.module,
            lut=lut,
            ff=ff,
            bram36=bram,
            dsp=dsp,
            wns_ns=wns,
            met=slack.group(1) == "MET",
            registered_matches=matches,
        )


def _util_value(report: str, label: str) -> int:
    return int(_util_cell(report, label))


def _util_float(report: str, label: str) -> float:
    return float(_util_cell(report, label))


def _util_cell(report: str, label: str) -> str:
    match = re.search(rf"^\|\s*{label}\s*\|\s*([\d.]+)\s*\|", report, re.MULTILINE)
    if match is None:
        raise BuildStepError(f"no '{label}' row in utilization report")
    return match.group(1)
