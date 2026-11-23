"""Run one core's cocotb bench through the registry.

``CoreBenchTask`` resolves a core from the ccflow registry (``/dau-core/<name>``),
builds its dependency-closed sources with Verilator and runs the cocotb module the
core declares as ``bench`` against its top module, optionally keeping the waveform.
"""

# NOTE: no `from __future__ import annotations` — ccflow's Flow.call
# inspects the real annotation objects on __call__ (the build_steps pattern)
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from ccflow import Flow, NullContext
from pydantic import Field, field_serializer, field_validator

from dau_build.build_steps import BuildCallableModel, BuildStepError, BuildStepResult
from dau_build.synthesize_cores import core_generics, core_registries, core_sources, resolve_core_definition

__all__ = ("CoreBenchTask",)

# where dau-sim's cocotb launcher leaves the waveform inside its build_dir
_WAVEFORM_NAME = "dump.vcd"


class CoreBenchTask(BuildCallableModel):
    # registry path (`/dau-core/<core-name>`; a bare core name is accepted)
    core: str
    output_root: Path
    # run only this test function of the bench; None runs all of them
    testcase: str | None = None
    # record a waveform into `<output_root>/<core name>/`
    waves: bool = False
    # module parameter overrides; without them the bench runs at the core's defaults
    parameters: Mapping[str, int | str] = Field(default_factory=lambda: MappingProxyType({}))

    # the config default is null, not {}: hydra merges a dict override into the
    # default instead of replacing it, and cannot add keys to an empty one
    @field_validator("parameters", mode="before")
    @classmethod
    def _read_only(cls, value: Mapping | None) -> Mapping:
        return MappingProxyType(dict(value or {}))

    @field_serializer("parameters")
    def _serialize_parameters(self, value: Mapping) -> dict:
        return dict(value)

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:  # noqa: ARG002 (ccflow requires the name `context`)
        definition = resolve_core_definition(self.core)
        if definition.bench is None:
            raise BuildStepError(f"core {definition.name!r} declares no bench")
        # validates the overrides; only those are passed on, so every other
        # parameter keeps the default the module itself declares
        generics = core_generics(definition, self.parameters)
        root = self.output_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        from .render_cores import render_generated_cores

        render_generated_cores([definition], root=root)
        sources = core_sources(definition, core_registries(), root=root)

        from dau_sim.integrations.cocotb import run_cocotb_testbench

        build_dir = root / definition.name
        results = root / f"{definition.name}-cocotb-results.xml"
        try:
            run_cocotb_testbench(
                sources=sources,
                hdl_toplevel=definition.module,
                test_module=definition.bench,
                build_dir=build_dir,
                results_xml=results,
                parameters={name: generics[name] for name in self.parameters},
                waves=self.waves,
                testcase=self.testcase,
            )
        except RuntimeError as exc:
            raise BuildStepError(f"bench {definition.bench} failed for core {definition.name!r}: {exc}; results {results}") from exc
        suites = list(ET.parse(results).getroot().iter("testsuite"))
        total, failed, skipped = (sum(int(suite.get(key, 0)) for suite in suites) for key in ("tests", "failures", "skipped"))
        # the cocotb runner fails on a test failure only under pytest
        if failed:
            raise BuildStepError(f"bench {definition.bench} failed for core {definition.name!r}: {failed} of {total} tests failed; results {results}")
        if not total:
            raise BuildStepError(
                f"bench {definition.bench} ran no tests for core {definition.name!r} (testcase {self.testcase!r}); results {results}"
            )
        waveform = f" waveform={build_dir / _WAVEFORM_NAME}" if self.waves else ""
        return BuildStepResult(
            step="core-bench",
            message=(
                f"dau-build-core-bench\tcore={definition.name} testcase={self.testcase or 'all'} "
                f"passed={total - failed - skipped} failed={failed}{waveform} status=passed"
            ),
        )
