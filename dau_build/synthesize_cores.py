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
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ccflow import BaseModel, Flow, NullContext

from dau_build.build_steps import BuildCallableModel, BuildStepError, BuildStepResult

__all__ = ("CoreEnvelopeReport", "SynthesizeCoresTask")

_REGISTRY_PREFIX = "/dau-core/"
_REGISTRY_GROUP = "dau-core"


class CoreEnvelopeReport(BaseModel):
    """One core's parsed OOC result, comparable to its registered envelope."""

    name: str
    module: str
    lut: int
    ff: int
    bram36: float
    dsp: int
    wns_ns: float | None = None  # None: characterized unclocked
    met: bool | None = None
    registered_matches: bool | None = None  # None: no envelope registered, or overrides changed the shape


def core_registry():
    """The composed ``/dau-core`` subregistry.

    Resolved through the ccflow ``ModelRegistry``, never by importing
    the core package: dau-build is public and must not depend on a
    private one. A core provider registers its config tree on the
    shared hydra searchpath (``hydra.lernaplugins``), and this task
    reads whatever the application composed — including overrides.

    When the subregistry is absent the group is composed by name off
    that searchpath. If no provider is installed, that composition
    fails and the task refuses; it never falls back to a direct import.
    """
    from ccflow import ModelRegistry

    registry = ModelRegistry.root()
    subregistry = registry.models.get(_REGISTRY_GROUP)
    if subregistry is not None:
        return subregistry
    from .config import compose_config

    try:
        composed = compose_config([f"+{_REGISTRY_GROUP}=cores"])
        # load into a DETACHED registry, never the root. Registering the
        # group on the root would make a later full compose by the core
        # package collide on a name this task put there, so whether an
        # application's own composition works would depend on whether it
        # ran a build task first.
        from omegaconf import OmegaConf

        scratch = ModelRegistry(name=f"{_REGISTRY_GROUP}-synthesize-cores")
        scratch.load_config(OmegaConf.create({_REGISTRY_GROUP: composed.cfg[_REGISTRY_GROUP]}))
        return scratch.models[_REGISTRY_GROUP]
    except Exception as exc:
        raise BuildStepError(
            f"no {_REGISTRY_PREFIX} registry is composed and the {_REGISTRY_GROUP!r} config group "
            f"could not be composed off the hydra searchpath ({type(exc).__name__}: {exc}); "
            "install a core provider or compose its group"
        ) from exc
    subregistry = registry.models.get(_REGISTRY_GROUP)
    if subregistry is None:
        raise BuildStepError(f"the {_REGISTRY_GROUP!r} config group composed but registered no cores")
    return subregistry


def resolve_core_definition(entry: str):
    name = entry.removeprefix(_REGISTRY_PREFIX)
    if "/" in name:
        raise BuildStepError(f"core entry {entry!r} is not a /dau-core/<name> registry path")
    definition = core_registry().models.get(name)
    if definition is None:
        raise BuildStepError(f"unknown core {entry!r}")
    if definition.kind.value == "package":
        # a SystemVerilog package is not a synthesizable top; it rides
        # along as a dependency of the tiles that import it
        raise BuildStepError(f"core {entry!r} is a package, not a synthesizable top; select the tiles that depend on it")
    return definition


def resolve_core_definitions(entries) -> list:
    """Registry definitions for a list of `/dau-core/<name>` entries."""
    return [resolve_core_definition(entry) for entry in entries]


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
    # per-core clock port when it is not the tile contract's `clk`
    # (identity-axil's s_axi_aclk); empty string = combinational, no clock
    # constraint and no timing report
    clock_ports: Mapping[str, str] = {}
    # run the plan (the synthesis host has vivado); false = handoff only
    execute: bool = False
    vivado: str = "vivado"

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:  # noqa: ARG002 (ccflow requires the name `context`)
        definitions = [self._resolve_core(entry) for entry in self.cores]
        if not definitions:
            raise BuildStepError("no cores selected; pass model.cores=[/dau-core/<name>,...]")
        part = self._part()
        # resolve once: relative output_root (./ooc) plus a subprocess cwd
        # would otherwise double the prefix inside vivado
        root = self.output_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        # A generated core has no checked-in file and refuses its source path
        # until rendered. Render here rather than making the caller remember:
        # synthesizing one from a stale render, or from parameters nobody
        # recorded, is exactly what the refusal exists to prevent.
        from .render_cores import render_generated_cores

        render_generated_cores(definitions, root=root)
        scripts = [self._stage_core(definition, part=part, root=root) for definition in definitions]
        plan_path = self._write_plan(scripts, root=root)
        if not self.execute:
            return BuildStepResult(
                step="synthesize-cores",
                message=(
                    f"dau-build-synthesize-cores\tcores={','.join(d.name for d in definitions)} part={part} "
                    f"clock_ns={self.clock_period_ns} output_root={root} plan={plan_path} status=handoff-written"
                ),
            )
        reports = [self._run_and_parse(definition, script, root=root) for definition, script in zip(definitions, scripts)]
        drift = [report.name for report in reports if report.registered_matches is False]
        summary = " ".join(
            f"{r.name}:lut={r.lut},ff={r.ff},bram36={r.bram36},dsp={r.dsp},"
            + (f"wns={r.wns_ns:+.3f},{'met' if r.met else 'VIOLATED'}" if r.wns_ns is not None else "unclocked")
            for r in reports
        )
        return BuildStepResult(
            step="synthesize-cores",
            message=(
                f"dau-build-synthesize-cores\tcores={','.join(d.name for d in definitions)} part={part} "
                f"clock_ns={self.clock_period_ns} output_root={root} {summary} "
                f"envelope_drift={','.join(drift) if drift else 'none'} status=synthesized"
            ),
        )

    def _core_registry(self):
        return core_registry()

    def _resolve_core(self, entry: str):
        return resolve_core_definition(entry)

    def _part(self) -> str:
        if self.part is not None:
            return self.part
        part = self.platform.part if self.platform is not None else None
        if part:
            return part
        raise BuildStepError("no part selected; pass model.part=... or compose a platform= group")

    def _generics(self, definition) -> dict[str, int]:
        values = {name: spec.default for name, spec in definition.parameters.items()}
        overrides = self.parameters.get(definition.name, {})
        unknown = set(overrides) - set(values)
        if unknown:
            raise BuildStepError(f"core {definition.name!r} declares no parameter {sorted(unknown)}; declared: {sorted(values) or 'none'}")
        for name, value in overrides.items():
            _validate_override(definition.name, name, value, definition.parameters[name])
        values.update(overrides)
        return values

    def _sources_for(self, definition) -> tuple[Path, ...]:
        """Dependency-closed source paths for one core, in registry order.

        Walks the model's own ``depends`` and ``source_path()`` surface
        rather than calling into the provider package, for the same reason
        :meth:`_core_registry` does.
        """
        subregistry = self._core_registry()
        selected: dict[str, object] = {}

        def visit(name: str) -> None:
            model = subregistry.models.get(name)
            if model is None:
                raise BuildStepError(f"core {name!r} is a declared dependency but is not in the registry")
            for dependency in model.depends:
                visit(dependency)
            selected.setdefault(name, model)

        visit(definition.name)
        ordered = [model for name, model in subregistry.models.items() if name in selected]
        return tuple(model.source_path() for model in ordered)

    def _stage_core(self, definition, *, part: str, root: Path) -> Path:
        sources = self._sources_for(definition)
        generics = self._generics(definition)
        generic_args = "".join(f" -generic {name}={value}" for name, value in generics.items())
        reads = "\n".join(f"read_verilog -sv {_tcl_path(path)}" for path in sources)
        # the clock rides an XDC read BEFORE synth_design so synthesis itself
        # is clock-constrained (a create_clock after synth_design would leave
        # synthesis unconstrained and only time the report); a core whose
        # clock_ports entry is "" is combinational — no constraint, no timing
        clock_port = self.clock_ports.get(definition.name, "clk")
        util_rpt = root / f"{definition.module}.util.rpt"
        lines = [reads]
        if clock_port:
            xdc = root / f"{definition.module}.ooc.xdc"
            xdc.write_text(f"create_clock -period {self.clock_period_ns:.3f} -name clk [get_ports {clock_port}]\n")
            lines.append(f"read_xdc -mode out_of_context {_tcl_path(xdc)}")
        lines.append(f"synth_design -top {definition.module} -part {part} -mode out_of_context{generic_args}")
        lines.append(f"report_utilization -file {_tcl_path(util_rpt)}")
        if clock_port:
            timing_rpt = root / f"{definition.module}.timing.rpt"
            lines.append(f"report_timing_summary -max_paths 1 -delay_type max -file {_tcl_path(timing_rpt)}")
        tcl = "\n".join(lines) + "\n"
        script = root / f"{definition.module}.ooc.tcl"
        script.write_text(tcl)
        return script

    def _write_plan(self, scripts: list[Path], *, root: Path) -> Path:
        lines = ["#!/bin/sh", "set -e"]
        for script in scripts:
            lines.append(shlex.join(self._vivado_argv(script, root=root)))
        plan = root / "synthesize-cores.sh"
        plan.write_text("\n".join(lines) + "\n")
        plan.chmod(0o755)
        return plan

    def _vivado_argv(self, script: Path, *, root: Path) -> list[str]:
        stem = script.stem.removesuffix(".ooc")
        return [
            self.vivado,
            "-mode",
            "batch",
            "-source",
            str(script),
            "-log",
            f"{root / stem}.log",
            "-journal",
            f"{root / stem}.jou",
        ]

    def _run_and_parse(self, definition, script: Path, *, root: Path) -> CoreEnvelopeReport:
        completed = subprocess.run(
            self._vivado_argv(script, root=root),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise BuildStepError(
                f"vivado failed for {definition.name} (exit {completed.returncode}); see {root / script.stem.removesuffix('.ooc')}.log"
            )
        return self.parse_reports(
            definition,
            output_root=root,
            clocked=bool(self.clock_ports.get(definition.name, "clk")),
            compare=True,
            part=self._part(),
            clock_period_ns=self.clock_period_ns,
            params=self._generics(definition),
        )

    @staticmethod
    def parse_reports(
        definition,
        *,
        output_root: Path,
        clocked: bool = True,
        compare: bool = True,
        part: str | None = None,
        clock_period_ns: float | None = None,
        params: Mapping[str, int] | None = None,
    ) -> CoreEnvelopeReport:
        """Parse a core's utilization (and, when clocked, timing) reports into
        an envelope report and compare it against the registered one.

        ``params`` are the module parameters the build actually used. A core
        carrying MEASUREMENT POINTS is keyed by (part, clock, params), so the
        comparison must match all three — a surface with several points at one
        part and clock would otherwise compare against whichever happened to
        be first, and report drift against a different parameter's numbers.
        Without ``params`` such a core is not compared at all, because there
        is no way to tell which point the build corresponds to."""
        util = (output_root / f"{definition.module}.util.rpt").read_text()
        lut = _util_value(util, r"Slice LUTs\*?")
        ff = _util_value(util, r"Slice Registers")
        bram = _util_float(util, r"Block RAM Tile")
        dsp = _util_value(util, r"DSPs")
        wns = None
        met = None
        if clocked:
            timing = (output_root / f"{definition.module}.timing.rpt").read_text()
            slack = re.search(r"Slack \((MET|VIOLATED)\)\s*:\s*(-?[\d.]+)ns", timing)
            if slack is None:
                raise BuildStepError(f"no slack line in {definition.module}.timing.rpt")
            wns = float(slack.group(2)) if slack.group(1) == "MET" else -abs(float(slack.group(2)))
            met = slack.group(1) == "MET"
        # Drift is checked against the point measured for THIS part and clock.
        # A core may carry several measurement points; comparing against
        # another part's numbers is the cross-part fallback the registry
        # exists to prevent, so an unmeasured coordinate reports "no
        # comparison" rather than a mismatch against the wrong board.
        registered = definition.resources
        matches = None
        if compare and registered is not None:
            if isinstance(registered, (list, tuple)):
                # every axis must match. A missing clock is NOT a wildcard:
                # once a tile carries both an 8 ns and a 10 ns point for the
                # same part and params, a wildcard silently picks whichever
                # was written first and reports drift against the wrong one.
                point = (
                    None
                    if params is None or clock_period_ns is None
                    else next(
                        (m for m in registered if m.part == part and m.clock_ns == clock_period_ns and dict(m.params) == dict(params)),
                        None,
                    )
                )
            else:
                # a scalar envelope declares one shape: it is comparable only
                # against a build that used the declared defaults
                point = registered if params is None or dict(params) == {name: spec.default for name, spec in definition.parameters.items()} else None
            if point is not None:
                matches = (point.lut, point.ff, point.bram36, point.dsp) == (lut, ff, bram, dsp)
        return CoreEnvelopeReport(
            name=definition.name,
            module=definition.module,
            lut=lut,
            ff=ff,
            bram36=bram,
            dsp=dsp,
            wns_ns=wns,
            met=met,
            registered_matches=matches,
        )


def _tcl_path(path: Path) -> str:
    """Brace-quote a filesystem path for generated tcl (spaces survive)."""
    return "{" + str(path) + "}"


def _validate_override(core_name: str, name: str, value: int, spec) -> None:
    """Enforce the registry's declared parameter constraints on an override —
    the same rules dau-core's composition validates (ParameterSpec: choices,
    positive, minimum/maximum, multiple_of, power_of_two) so an invalid value
    is rejected here, never as an HDL elaboration failure."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise BuildStepError(f"core {core_name!r} parameter {name!r} must be a positive int, got {value!r}")
    if spec.choices is not None:
        if value not in spec.choices:
            allowed = ", ".join(str(choice) for choice in spec.choices)
            raise BuildStepError(f"core {core_name!r} parameter {name!r} must be one of {allowed}, got {value!r}")
        return  # an enumerated choice set is the whole constraint
    if value <= 0:
        raise BuildStepError(f"core {core_name!r} parameter {name!r} must be a positive int, got {value!r}")
    if value < spec.minimum:
        raise BuildStepError(f"core {core_name!r} parameter {name!r} must be >= {spec.minimum}, got {value!r}")
    if spec.maximum is not None and value > spec.maximum:
        raise BuildStepError(f"core {core_name!r} parameter {name!r} must be <= {spec.maximum}, got {value!r}")
    if value % spec.multiple_of != 0:
        raise BuildStepError(f"core {core_name!r} parameter {name!r} must be a positive multiple of {spec.multiple_of}, got {value!r}")
    if spec.power_of_two and value & (value - 1):
        raise BuildStepError(f"core {core_name!r} parameter {name!r} must be a power of two, got {value!r}")


def _util_value(report: str, label: str) -> int:
    return int(_util_cell(report, label))


def _util_float(report: str, label: str) -> float:
    return float(_util_cell(report, label))


def _util_cell(report: str, label: str) -> str:
    match = re.search(rf"^\|\s*{label}\s*\|\s*([\d.]+)\s*\|", report, re.MULTILINE)
    if match is None:
        raise BuildStepError(f"no '{label}' row in utilization report")
    return match.group(1)
