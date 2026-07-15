import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeVar

from ccflow import BaseModel, CallableModel, Flow, NullContext, ResultBase
from pydantic import Field, ValidationError, field_validator

from dau_build.hardware_plan import (
    HardwarePlan,
    HardwareToolchainConfig,
    execute_plan_steps,
    format_plan_steps,
    stage_shell_plan,
    stage_vivado_overlay_plan,
    stage_vivado_project_plan,
    validate_vivado_artifacts,
    validate_vivado_artifacts_step,
    vivado_overlay_build_step,
)
from dau_build.vivado_backend import (
    VivadoBackendArtifacts,
    VivadoBackendArtifactValidation,
    VivadoBackendRequest,
    VivadoProjectArtifactValidation,
    generate_vivado_backend_artifacts,
)


def _build_spec_api():
    """Deferred: dau_build.build_spec pulls the SV parser stack (amaranth,
    pyslang), which hardware hosts running flash/shell tasks never need."""
    from dau_build import build_spec

    return build_spec


def _resolve_build_config(spec, *, board=None, backend=None, backend_name=None, driver=None, memory=None):
    """Deferred for the same reason: build_config imports build_spec."""
    from dau_build.build_config import ResolvedBuildConfig

    return ResolvedBuildConfig.from_spec(spec, board=board, backend=backend, backend_name=backend_name, driver=driver, memory=memory)


class BuildStepError(ValueError):
    pass


class BuildStepResult(ResultBase):
    step: str
    message: str


BuildCallableModelType = TypeVar("BuildCallableModelType", bound="BuildCallableModel")


class BuildCallableModel(CallableModel):
    _STRINGIFY_SEPARATOR: ClassVar[str] = ","

    @classmethod
    def _stringify_override_value(cls, value) -> str:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, tuple | list):
            return cls._STRINGIFY_SEPARATOR.join(str(item) for item in value)
        return str(value)


class SpecPathModel(BuildCallableModel):
    # `spec` is composed by the Hydra `spec=` group (a BuildSpec); `spec_path`
    # is file input for the CLI/tests. `board`/`backend` are composed by the
    # `board=`/`backend=` groups (BoardConfig/BackendConfig) and win over the
    # spec-derived defaults. Typed Any, not the models, so importing
    # build_steps stays light — build_spec pulls the SV-parser stack (F51).
    spec: Any = None
    spec_path: Path | None = None
    board: Any = None
    backend: Any = None
    driver: Any = None
    memory: Any = None

    def _resolved(self, spec, *, backend_name=None):
        return _resolve_build_config(spec, board=self.board, backend=self.backend, backend_name=backend_name, driver=self.driver, memory=self.memory)

    def load_spec(self):
        build_spec = self.spec
        if build_spec is None:
            if self.spec_path is None:
                raise BuildStepError("a spec is required: pass spec=<name> or spec_path=<file>")
            build_spec = _build_spec_api().BuildSpec.from_file(self.spec_path)
        return build_spec.resolve()

    @property
    def spec_base_dir(self) -> Path:
        return Path(self.spec.base_dir) if self.spec is not None else self.spec_path.parent

    @property
    def spec_label(self) -> str:
        return self.spec.name if self.spec is not None else str(self.spec_path)


class ModuleSelectionModel(SpecPathModel):
    module: str

    def load_spec_and_validate_module(self):
        spec = self.load_spec()
        provided_modules = (spec.top_name, *spec.modules)
        if self.module not in provided_modules:
            expected = ", ".join(provided_modules)
            raise BuildStepError(f"module {self.module!r} is not provided by spec {self.spec_label}; expected one of: {expected}")
        return spec


class InspectStep(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        return BuildStepResult(step="inspect", message=_build_spec_api().dau_build_spec_summary(self.load_spec()))


class ValidateStep(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        self.load_spec()
        return BuildStepResult(step="validate", message=f"dau-build-spec-valid\tspec={self.spec_label}")


class GenerateStep(SpecPathModel):
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        artifacts = _build_spec_api().generate_dau_build_artifacts(self.load_spec(), output_root=self.output_root)
        return BuildStepResult(
            step="generate",
            message=f"dau-build-artifacts-generated\tmanifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path}",
        )


class WriteStep(SpecPathModel):
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        artifacts = _build_spec_api().write_dau_build_artifacts(self.load_spec(), output_root=self.output_root)
        return BuildStepResult(step="write", message=f"dau-build-artifacts\tmanifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path}")


class ResolvedConfigStep(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        resolved = self._resolved(self.load_spec())
        return BuildStepResult(step="resolved-config", message=resolved.to_text())


class SimulateStep(SpecPathModel):
    output_root: Path | None = None
    simulate_engine: Literal["svparser", "verilator"] = Field(default="svparser", alias="simulate.engine")
    simulate_profile: str | None = Field(default=None, alias="simulate.profile")
    simulate_profile_manifest: tuple[Path, ...] = Field(default=(), alias="simulate.profile_manifest")
    simulate_testbench_path: Path | None = Field(default=None, alias="simulate.testbench_path")
    simulate_top_module: str | None = Field(default=None, alias="simulate.top_module")
    simulate_expect_stdout: str | None = Field(default=None, alias="simulate.expect_stdout")
    simulate_verilator: str = Field(default="verilator", alias="simulate.verilator")
    simulate_extra_args: str = Field(default="", alias="simulate.extra_args")

    @field_validator("simulate_profile_manifest", mode="before")
    @classmethod
    def _split_profile_manifest_paths(cls, value):
        return _split_path_tuple(value)

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        spec = self.load_spec()
        self._resolved(spec)
        _build_spec_api().generate_dau_build_artifacts(spec, output_root=self.output_root or self.spec_base_dir / ".dau-build-sim")
        if self.simulate_engine == "verilator":
            return self._run_verilator(spec)
        return BuildStepResult(
            step="simulate",
            message=(
                f"dau-build-simulate\tspec={self.spec_label} top={spec.top_name} modules={','.join(spec.modules)} "
                f"sources={len(spec.sources)} engine=svparser status=validated"
            ),
        )

    def _run_verilator(self, spec) -> BuildStepResult:
        try:
            from dau_sim.integrations.verilator import VerilatorExecutionError, VerilatorUnavailableError, run_verilator_testbench
        except ModuleNotFoundError as exc:
            raise BuildStepError("simulate.engine=verilator requires dau-sim to be importable") from exc

        from dau_build.simulation_profiles import SimulationProfileError, resolve_profile

        profile = None
        if self.simulate_profile:
            try:
                profile = resolve_profile(self.simulate_profile, profile_manifests=self.simulate_profile_manifest)
            except SimulationProfileError as exc:
                raise BuildStepError(str(exc)) from exc

        if profile is not None:
            testbench_path = None
            top_module = profile.top_module
            expected_stdout = self.simulate_expect_stdout or profile.expect_stdout
            extra_sources = profile.sources
        else:
            if self.simulate_testbench_path is None:
                raise BuildStepError("missing required override: simulate.testbench_path")
            if not self.simulate_top_module:
                raise BuildStepError("missing required override: simulate.top_module")
            testbench_path = self.simulate_testbench_path
            top_module = self.simulate_top_module
            expected_stdout = self.simulate_expect_stdout
            extra_sources = (testbench_path,)

        work_dir = self.output_root or self.spec_base_dir / ".dau-build-sim" / "verilator"
        extra_args = tuple(shlex.split(self.simulate_extra_args))
        all_sources = _unique_paths((*spec.sources, *extra_sources))
        try:
            result = run_verilator_testbench(
                sources=all_sources,
                top_module=top_module,
                work_dir=work_dir,
                verilator=self.simulate_verilator,
                extra_args=extra_args,
            )
        except (FileNotFoundError, ValueError, VerilatorExecutionError, VerilatorUnavailableError) as exc:
            raise BuildStepError(str(exc)) from exc

        if expected_stdout and expected_stdout not in result.stdout:
            raise BuildStepError(f"verilator stdout did not contain expected text {expected_stdout!r}")

        mode_segment = f"profile={self.simulate_profile} " if self.simulate_profile else f"testbench={testbench_path} "
        return BuildStepResult(
            step="simulate",
            message=(
                f"dau-build-simulate\tspec={self.spec_label} top={spec.top_name} modules={','.join(spec.modules)} sources={len(spec.sources)} "
                f"engine=verilator {mode_segment}testbench_top={top_module} work_dir={work_dir} status=passed"
            ),
        )


class SynthesisStep(SpecPathModel):
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        spec = self.load_spec()
        resolved = self._resolved(spec)
        artifacts = _build_spec_api().write_dau_build_artifacts(spec, output_root=self.output_root)
        return BuildStepResult(
            step="synthesis",
            message=(
                f"dau-build-synthesis\tbackend={resolved.backend.name} platform={resolved.board.platform} shell={resolved.board.shell} "
                f"output_root={self.output_root} manifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path} vivado=not-invoked"
            ),
        )


class ExplainStep(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        spec = self.load_spec()
        resolved = self._resolved(spec)
        return BuildStepResult(
            step="explain",
            message="\n".join(
                (
                    "dau-build-explain",
                    f"spec\tpath={self.spec_label} name={spec.name} top={spec.top_name}",
                    f"board\tname={resolved.board.name} platform={resolved.board.platform} shell={resolved.board.shell}",
                    "actions\tvalidate=local simulate=local synthesis=local-handoff artifacts=generate-or-write",
                )
            ),
        )


class InspectTask(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        return BuildStepResult(step="inspect", message=_build_spec_api().dau_build_spec_summary(self.load_spec()))


class BuildArtifactsTask(SpecPathModel):
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        artifacts = _build_spec_api().write_dau_build_artifacts(self.load_spec(), output_root=self.output_root)
        return BuildStepResult(step="build", message=f"dau-build-artifacts\tmanifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path}")


class ValidateTask(SpecPathModel):
    # validates a generated artifact bundle when manifest_path is given,
    # otherwise validates the spec
    manifest_path: Path | None = None
    root: Path | None = None

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        if self.manifest_path is not None:
            top_sv = _build_spec_api().validate_dau_build_artifact_bundle(self.manifest_path, root=self.root)
            return BuildStepResult(step="validate", message=f"dau-build-artifacts-valid\tmanifest={self.manifest_path} top_sv={top_sv}")
        self.load_spec()
        return BuildStepResult(step="validate", message=f"dau-build-spec-valid\tspec={self.spec_label}")


class Simulator(BaseModel):
    """A simulator selected from the ``simulator`` config group. Each is a
    polymorphic, hydra-configurable model (``simulator=simulators/verilator
    simulator.profile=...``); ``SimulateTask`` delegates to ``simulate`` —
    there is no simulator ``Literal`` or dispatch ``if`` on the task."""

    name: str

    def simulate(self, *, task: "SimulateTask") -> BuildStepResult:
        raise NotImplementedError


class SvparserSimulator(Simulator):
    name: str = "svparser"

    def simulate(self, *, task: "SimulateTask") -> BuildStepResult:
        return task._validate_and_generate(self.name)


class CocotbSimulator(Simulator):
    name: str = "cocotb"
    profile: str | None = None
    profile_manifest: tuple[Path, ...] = ()

    @field_validator("profile_manifest", mode="before")
    @classmethod
    def _split_profile_manifest_paths(cls, value):
        return _split_path_tuple(value)

    def simulate(self, *, task: "SimulateTask") -> BuildStepResult:
        if self.profile and task._no_spec():
            from dau_sim.integrations.cocotb import run_cocotb_testbench

            from dau_build.simulation_profiles import resolve_profile

            profile = resolve_profile(self.profile, profile_manifests=self.profile_manifest)
            # the cocotb runner raises on any failing test
            run_cocotb_testbench(
                sources=profile.sources,
                hdl_toplevel=profile.hdl_toplevel,
                test_module=profile.test_module,
                build_dir=task._profile_work_dir(profile.name),
            )
            return BuildStepResult(
                step="simulate",
                message=f"dau-build-simulate\ttask=simulate simulator=cocotb profile={profile.name} status=passed",
            )
        return task._validate_and_generate(self.name)


class VerilatorSimulator(Simulator):
    name: str = "verilator"
    profile: str | None = None
    profile_manifest: tuple[Path, ...] = ()
    testbench_path: Path | None = None
    top_module: str | None = None
    expect_stdout: str | None = None
    verilator: str = "verilator"
    extra_args: str = ""

    @field_validator("profile_manifest", mode="before")
    @classmethod
    def _split_profile_manifest_paths(cls, value):
        return _split_path_tuple(value)

    def simulate(self, *, task: "SimulateTask") -> BuildStepResult:
        if self.profile and task._no_spec():
            return self._run_registered_profile(task)
        spec = task.require_spec_and_module()
        if self.testbench_path is None:
            raise BuildStepError("missing required override: simulator.testbench_path")
        if not self.top_module:
            raise BuildStepError("missing required override: simulator.top_module")
        work_dir = task.sim_output_root() / "verilator"
        _run_verilator_bench(
            spec,
            extra_sources=(self.testbench_path,),
            top_module=self.top_module,
            expect_stdout=self.expect_stdout,
            verilator=self.verilator,
            extra_args=self.extra_args,
            work_dir=work_dir,
        )
        return BuildStepResult(
            step="simulate",
            message=(
                f"dau-build-simulate\ttask=simulate simulator=verilator module={task.module} spec={task.spec_label} "
                f"testbench_top={self.top_module} work_dir={work_dir} status=passed"
            ),
        )

    def _run_registered_profile(self, task: "SimulateTask") -> BuildStepResult:
        from dau_sim.integrations.verilator import run_verilator_testbench

        from dau_build.simulation_profiles import resolve_profile

        profile = resolve_profile(self.profile, profile_manifests=self.profile_manifest)
        result = run_verilator_testbench(sources=profile.sources, top_module=profile.top_module, work_dir=task._profile_work_dir(profile.name))
        marker = self.expect_stdout or profile.expect_stdout
        if marker not in result.stdout:
            raise BuildStepError(f"profile {profile.name!r} did not report {marker!r}")
        return BuildStepResult(
            step="simulate",
            message=f"dau-build-simulate\ttask=simulate simulator=verilator profile={profile.name} status=passed marker={marker}",
        )


class SimulateTask(ModuleSelectionModel):
    # spec_path/module are optional for profile-only Verilator runs, where a
    # registered profile already carries its sources and top module
    spec_path: Path | None = None
    module: str = ""
    output_root: Path | None = None
    # the simulator is the composed `simulator` group option; default svparser
    simulator: Any = None

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        return self._simulator().simulate(task=self)

    def _simulator(self) -> Simulator:
        if isinstance(self.simulator, Simulator):
            return self.simulator
        if self.simulator is None:
            return SvparserSimulator()
        raise BuildStepError(f"simulator {getattr(self.simulator, 'name', self.simulator)!r} is not a simulator")

    def _no_spec(self) -> bool:
        return self.spec is None and self.spec_path is None

    def sim_output_root(self) -> Path:
        return self.output_root or self.spec_base_dir / ".dau-build-sim"

    def _profile_work_dir(self, profile_name: str) -> Path:
        work_dir = (self.output_root or Path.cwd() / ".dau-build-sim") / profile_name
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def require_spec_and_module(self):
        if self._no_spec() or not self.module:
            raise BuildStepError(
                "task=simulate requires a spec (spec=<name> or spec_path=<file>) and module "
                "(or simulator=simulators/verilator with simulator.profile=<name> for a registered profile)"
            )
        return self.load_spec_and_validate_module()

    def _validate_and_generate(self, simulator_name: str) -> BuildStepResult:
        spec = self.require_spec_and_module()
        self._resolved(spec)
        _build_spec_api().generate_dau_build_artifacts(spec, output_root=self.sim_output_root())
        return BuildStepResult(
            step="simulate",
            message=f"dau-build-simulate\ttask=simulate simulator={simulator_name} module={self.module} spec={self.spec_label} status=validated",
        )


class SynthesisEngine(BaseModel):
    """A synthesis engine selected from the ``backend`` config group. Each
    engine is a polymorphic, fully hydra-configurable model
    (``backend=backends/yosys backend.frontend=slang``); ``SynthesizeTask``
    delegates to ``synthesize`` — there is no engine ``Literal`` or dispatch
    ``if``."""

    name: str
    invocation: str = "standard"

    def synthesize(self, *, task: "SynthesizeTask", spec, artifacts, resolved) -> BuildStepResult:
        raise NotImplementedError


class VivadoEngine(SynthesisEngine):
    name: str = "vivado"

    def synthesize(self, *, task: "SynthesizeTask", spec, artifacts, resolved) -> BuildStepResult:
        backend_artifacts = _write_vivado_backend_handoff(
            spec,
            selected_module=task.module,
            output_root=task.output_root,
            dau_artifact_bundle_path=artifacts.artifact_manifest_path,
            platform=resolved.board.platform,
            shell=resolved.board.shell,
            operator_set=resolved.operators.names,
        )
        return BuildStepResult(
            step="synthesize",
            message=(
                f"dau-build-synthesize\ttask=synthesize engine={self.name} module={task.module} spec={task.spec_label} "
                f"output_root={task.output_root} manifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path} "
                f"backend_manifest={backend_artifacts.manifest_path} command_plan={backend_artifacts.command_plan_path} status=handoff-written"
            ),
        )


class YosysEngine(SynthesisEngine):
    name: str = "yosys"
    frontend: Literal["verilog", "slang"] = "verilog"
    yosys: str = "yosys"

    def synthesize(self, *, task: "SynthesizeTask", spec, artifacts, resolved) -> BuildStepResult:
        # yosys is runnable (unlike the Vivado plan), so this actually
        # elaborates and synthesizes the generated top — a real check
        from dau_build.yosys_backend import YosysBackendError, YosysBackendRequest, run_yosys_synthesis

        request = YosysBackendRequest(
            top_module=spec.top_name,
            sources=(artifacts.top_sv_path, *spec.sources),
            output_root=task.output_root,
            frontend=self.frontend,
            yosys=self.yosys,
        )
        try:
            result = run_yosys_synthesis(request)
        except YosysBackendError as exc:
            raise BuildStepError(str(exc)) from exc
        if not result.passed:
            raise BuildStepError(f"yosys synthesis failed for {spec.top_name} (frontend={self.frontend}); see {result.log_path}")
        return BuildStepResult(
            step="synthesize",
            message=(
                f"dau-build-synthesize\ttask=synthesize engine={self.name} frontend={self.frontend} module={task.module} "
                f"spec={task.spec_label} top={spec.top_name} output_root={task.output_root} "
                f"script={result.script_path} cells={result.cell_count} status=synthesized"
            ),
        )


class SynthesizeTask(ModuleSelectionModel):
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        spec = self.load_spec_and_validate_module()
        engine = self._engine()
        resolved = self._resolved(spec, backend_name=engine.name)
        artifacts = _build_spec_api().write_dau_build_artifacts(spec, output_root=self.output_root)
        return engine.synthesize(task=self, spec=spec, artifacts=artifacts, resolved=resolved)

    def _engine(self) -> SynthesisEngine:
        # the engine is the composed `backend` group option; default to Vivado
        if isinstance(self.backend, SynthesisEngine):
            return self.backend
        if self.backend is None:
            return VivadoEngine()
        raise BuildStepError(f"backend {getattr(self.backend, 'name', self.backend)!r} is not a synthesis engine")


def _bitstream_from_shell_build_manifest(manifest_path: Path) -> Path:
    """Resolve and verify the bitstream from an artlink shell-build
    manifest: build_status must be built and the file must match its
    recorded digest — a flashed bitstream is identified by provenance,
    never by filename."""
    import hashlib

    from dau_build.packaging import load_artifact_manifest

    manifest = load_artifact_manifest(manifest_path)
    if manifest.metadata.get("build_status") != "built":
        raise BuildStepError(f"shell-build manifest is not built: {manifest_path.as_posix()}")
    bitstreams = [artifact for artifact in manifest.artifacts if artifact.role == "bitstream"]
    if len(bitstreams) != 1:
        raise BuildStepError(f"shell-build manifest must carry exactly one bitstream artifact: {manifest_path.as_posix()}")
    bitstream = bitstreams[0]
    if bitstream.path is None:
        raise BuildStepError(f"bitstream artifact has no path: {manifest_path.as_posix()}")
    bitstream_path = bitstream.path if bitstream.path.is_absolute() else manifest_path.parent / bitstream.path
    if not bitstream_path.is_file():
        raise BuildStepError(f"bitstream does not exist: {bitstream_path.as_posix()}")
    if bitstream.digest is None:
        # digest is optional in the artlink model, but flash provenance is
        # the digest: a manifest without one proves nothing about the file
        raise BuildStepError(f"bitstream artifact carries no digest: {manifest_path.as_posix()}; flash requires digested provenance")
    actual = hashlib.new(bitstream.digest.algorithm, bitstream_path.read_bytes()).hexdigest()
    if actual != bitstream.digest.value:
        raise BuildStepError(
            f"bitstream digest mismatch (manifest {bitstream.digest.value[:12]}..., file {actual[:12]}...): {bitstream_path.as_posix()}"
        )
    return bitstream_path


class FlashTask(BuildCallableModel):
    tool: str = "openFPGAloader"
    bitstream: Path | None = None
    manifest_path: Path | None = None
    mode: Literal["volatile", "persistent"] = "volatile"

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        if self.tool.lower() != "openfpgaloader":
            raise BuildStepError(f"unknown flash tool {self.tool!r}; expected openFPGAloader")
        bitstream = self.bitstream
        manifest_segment = ""
        if self.manifest_path is not None:
            if self.manifest_path.suffix in (".yaml", ".yml"):
                bitstream = bitstream or _bitstream_from_shell_build_manifest(self.manifest_path)
            else:
                manifest = _read_key_value_manifest(self.manifest_path)
                _require_built_manifest(self.manifest_path, manifest)
                # a key=value backend manifest carries no digests: flash
                # provenance comes from the packaged artlink manifest the
                # validate step writes beside it (digest-verified below)
                packaged = self.manifest_path.with_suffix(".artifacts.yaml")
                if not packaged.is_file():
                    raise BuildStepError(
                        f"backend manifest has no packaged artlink manifest: {packaged.as_posix()}; "
                        "run the validate step (execute=True) to package digested provenance before flashing"
                    )
                bitstream = bitstream or _bitstream_from_shell_build_manifest(packaged)
            manifest_segment = f" manifest={self.manifest_path}"
        if bitstream is None:
            raise BuildStepError("flash requires bitstream or manifest_path")
        if not bitstream.is_file():
            raise BuildStepError(f"bitstream does not exist: {bitstream}")
        return BuildStepResult(
            step="flash",
            message=f"dau-build-flash\ttask=flash tool={self.tool} bitstream={bitstream}{manifest_segment} mode={self.mode} status=planned",
        )


class SmokeTestTask(BuildCallableModel):
    test: Literal["identity", "dma-loopback", "aggregation"]
    manifest_path: Path | None = None
    device: str | None = None

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        device_segment = f" device={self.device}" if self.device else ""
        manifest_segment = ""
        if self.manifest_path is not None:
            manifest = _read_key_value_manifest(self.manifest_path)
            _require_built_manifest(self.manifest_path, manifest)
            manifest_segment = (
                f" manifest={self.manifest_path}"
                f" register_window_offset={_manifest_required(manifest, 'register_window_offset')}"
                f" input_buffer={_manifest_required(manifest, 'input_buffer_address')}"
                f" output_buffer={_manifest_required(manifest, 'output_buffer_address')}"
            )
        return BuildStepResult(
            step="smoke-test",
            message=f"dau-build-smoke-test\ttask=smoke-test test={self.test}{device_segment}{manifest_segment} status=planned",
        )


class BuildShellProjectTask(BuildCallableModel):
    """Run a generated shell project script through Vivado and package the
    outputs as an artlink shell-build manifest (bitstream digest, reports,
    log, generated inputs, contributing sources with repository state).
    Plan-only unless ``execute=true``."""

    output_root: Path
    script: str = "build_mm_job.tcl"
    vivado: str = "vivado"
    manifest_name: str = "dau-shell"
    source_paths: tuple[Path, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    execute: bool = False

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        from dau_build.shell_build import run_shell_project_build, write_shell_build_manifest

        script_path = self.output_root / self.script
        if not script_path.is_file():
            raise BuildStepError(f"shell project script does not exist: {script_path.as_posix()}")
        command = shlex.join([self.vivado, "-mode", "batch", "-source", self.script])
        if not self.execute:
            return BuildStepResult(
                step="build-shell-project",
                message=f"dau-build-shell\ttask=build-shell-project output_root={self.output_root} command={command!r} status=planned",
            )
        status = run_shell_project_build(self.output_root, script=self.script, vivado_executable=self.vivado)
        manifest_path = write_shell_build_manifest(
            self.output_root,
            name=self.manifest_name,
            source_paths=self.source_paths,
            metadata={**self.metadata, **status.model_dump(exclude_none=True)},
        )
        return BuildStepResult(
            step="build-shell-project",
            message=(
                f"dau-build-shell\ttask=build-shell-project output_root={self.output_root} wns={status.wns_ns} manifest={manifest_path} status=built"
            ),
        )


class StageTask(BuildCallableModel):
    task_name: ClassVar[str]
    execute: bool = False

    def stage_steps(self):
        raise NotImplementedError("StageTask subclasses must provide stage steps")

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        steps = self.stage_steps()
        if not self.execute:
            return BuildStepResult(step=self.task_name, message=format_plan_steps(steps))
        return_code = execute_plan_steps(steps)
        if return_code != 0:
            raise BuildStepError(f"stage task {self.task_name!r} failed with exit code {return_code}")
        backend_segment = f" backend={self.backend}" if getattr(self, "backend", None) else ""
        return BuildStepResult(
            step=self.task_name,
            message=f"dau-build-stage\ttask={self.task_name}{backend_segment} steps={len(steps)} status=executed",
        )


class ShellStageTask(StageTask):
    task_name: ClassVar[str] = "stage-shell"
    work_root: Path
    source_shell_root: Path

    def stage_steps(self):
        return stage_shell_plan(self._toolchain_config(), source_shell_root=self.source_shell_root)

    def _toolchain_config(self) -> HardwareToolchainConfig:
        return HardwareToolchainConfig(work_root=self.work_root)


class OverlayStageTask(StageTask):
    backend: str


class VivadoOverlayStageTask(OverlayStageTask):
    task_name: ClassVar[str] = "stage-vivado-overlay"
    backend: Literal["vivado"] = "vivado"
    work_root: Path
    source_shell_root: Path | None = None
    bitstream: Path | None = None
    vivado: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    dau_core_root: Path
    dau_artifact_bundle: Path | None = None
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    artifact_stem: str = "dau-vivado"
    backend_platform: str = "vivado-xdma"
    backend_shell: str = "xdma-shell"
    operator: str | None = None
    register_map_version: str = "0.1"
    stream_protocol_version: str = "0.1"
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    resource_summary_path: Path = Path("reports/dau_utilization.rpt")
    timing_summary_path: Path = Path("reports/dau_timing_summary.rpt")
    vivado_log_path: Path = Path("vivado.log")
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")

    def stage_steps(self):
        return stage_vivado_overlay_plan(
            self._toolchain_config(),
            dau_core_root=self.dau_core_root,
            source_shell_root=self.source_shell_root,
            dau_artifact_bundle=self.dau_artifact_bundle,
            artifact_stem=self.artifact_stem,
            platform=self.backend_platform,
            shell=self.backend_shell,
            operator_set=self._operator_set(),
            register_map_version=self.register_map_version,
            stream_protocol_version=self.stream_protocol_version,
            overlay_tcl=self.overlay_tcl,
            manifest_path=self.manifest_path,
            command_plan_path=self.command_plan_path,
            resource_summary_path=self.resource_summary_path,
            timing_summary_path=self.timing_summary_path,
            vivado_log_path=self.vivado_log_path,
            vivado_settings=self.vivado_settings,
        )

    def _toolchain_config(self) -> HardwareToolchainConfig:
        return HardwareToolchainConfig(
            work_root=self.work_root,
            vivado_executable=self.vivado,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
            bitstream_path=self.bitstream,
        )

    def _operator_set(self) -> tuple[str, ...]:
        if self.operator is None:
            return ("identity",)
        return tuple(operator for operator in self.operator.split(",") if operator) or ("identity",)


class VivadoProjectStageTask(VivadoOverlayStageTask):
    task_name: ClassVar[str] = "stage-vivado-project"
    source_shell_root: Path
    dau_driver_root: Path
    dau_utils_root: Path | None = None
    project_manifest_path: Path | None = None

    def stage_steps(self):
        return stage_vivado_project_plan(
            self._toolchain_config(),
            source_shell_root=self.source_shell_root,
            dau_core_root=self.dau_core_root,
            dau_driver_root=self.dau_driver_root,
            dau_utils_root=self.dau_utils_root,
            dau_artifact_bundle=self.dau_artifact_bundle,
            artifact_stem=self.artifact_stem,
            platform=self.backend_platform,
            shell=self.backend_shell,
            operator_set=self._operator_set(),
            register_map_version=self.register_map_version,
            stream_protocol_version=self.stream_protocol_version,
            overlay_tcl=self.overlay_tcl,
            manifest_path=self.manifest_path,
            command_plan_path=self.command_plan_path,
            project_manifest_path=self.project_manifest_path,
            resource_summary_path=self.resource_summary_path,
            timing_summary_path=self.timing_summary_path,
            vivado_log_path=self.vivado_log_path,
            vivado_settings=self.vivado_settings,
        )


class OverlayBuildTask(BuildCallableModel):
    backend: str
    execute: bool = False

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        raise NotImplementedError("OverlayBuildTask subclasses must implement __call__")


class VivadoOverlayBuildTask(OverlayBuildTask):
    backend: Literal["vivado"] = "vivado"
    work_root: Path
    bitstream: Path | None = None
    vivado: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    build_tcl: Path = Path("scripts/dau_build.tcl")
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        step = vivado_overlay_build_step(
            self._toolchain_config(),
            overlay_tcl=self.overlay_tcl,
            build_tcl=self.build_tcl,
            vivado_settings=self.vivado_settings,
        )
        if not self.execute:
            return BuildStepResult(step="overlay-build", message=format_plan_steps((step,)))
        return_code = execute_plan_steps((step,))
        if return_code != 0:
            raise BuildStepError(f"{self.backend} overlay build failed with exit code {return_code}")
        return BuildStepResult(
            step="overlay-build",
            message=f"dau-build-overlay-build\ttask=overlay-build backend={self.backend} steps=1 status=executed",
        )

    def _toolchain_config(self) -> HardwareToolchainConfig:
        return HardwareToolchainConfig(
            work_root=self.work_root,
            vivado_executable=self.vivado,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
            bitstream_path=self.bitstream,
        )


class OverlayArtifactValidationTask(BuildCallableModel):
    backend: str
    execute: bool = False

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        raise NotImplementedError("OverlayArtifactValidationTask subclasses must implement __call__")


class ValidateVivadoArtifactsTask(OverlayArtifactValidationTask):
    backend: Literal["vivado"] = "vivado"
    work_root: Path
    bitstream: Path | None = None
    vivado: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    artifact_stem: str = "dau-vivado"
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    project_manifest_path: Path | None = None

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        manifest_path = self.manifest_path or Path(f"{self.artifact_stem}.manifest")
        command_plan_path = self.command_plan_path or Path(f"{self.artifact_stem}.plan")
        if not self.execute:
            step = validate_vivado_artifacts_step(
                self._toolchain_config(),
                manifest_path=manifest_path,
                command_plan_path=command_plan_path,
                project_manifest_path=self.project_manifest_path,
            )
            return BuildStepResult(step="validate-vivado-artifacts", message=format_plan_steps((step,)))
        validation = validate_vivado_artifacts(
            self._toolchain_config(),
            manifest_path=manifest_path,
            command_plan_path=command_plan_path,
            project_manifest_path=self.project_manifest_path,
        )
        if not validation.ok:
            raise BuildStepError(_vivado_artifact_validation_message(validation))
        from dau_build.shell_build import write_overlay_build_manifest

        resolved_manifest = manifest_path if manifest_path.is_absolute() else self.work_root / manifest_path
        packaged = write_overlay_build_manifest(self.work_root, resolved_manifest, name=self.artifact_stem) if resolved_manifest.is_file() else None
        packaged_segment = f" artlink={packaged}" if packaged else ""
        return BuildStepResult(step="validate-vivado-artifacts", message=_vivado_artifact_validation_message(validation) + packaged_segment)

    def _toolchain_config(self) -> HardwareToolchainConfig:
        return HardwareToolchainConfig(
            work_root=self.work_root,
            vivado_executable=self.vivado,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
            bitstream_path=self.bitstream,
        )


class BuildOverlayArtifactsTask(BuildCallableModel):
    backend: str

    def overlay_build_model(self) -> OverlayBuildTask:
        raise NotImplementedError("BuildOverlayArtifactsTask subclasses must provide an overlay build model")

    def artifact_validation_model(self) -> OverlayArtifactValidationTask:
        raise NotImplementedError("BuildOverlayArtifactsTask subclasses must provide an artifact validation model")

    @Flow.deps
    def __deps__(self, context: NullContext):
        return [(self.overlay_build_model(), [context])]

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        build_result = self.overlay_build_model()(context)
        validation_result = self.artifact_validation_model()(context)
        if build_result.message:
            message = f"{build_result.message}\n{validation_result.message}"
        else:
            message = validation_result.message
        return BuildStepResult(step="build-overlay-artifacts", message=message)


class BuildVivadoArtifactsTask(BuildOverlayArtifactsTask):
    backend: Literal["vivado"] = "vivado"
    work_root: Path
    bitstream: Path | None = None
    vivado: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    build_tcl: Path = Path("scripts/dau_build.tcl")
    artifact_stem: str = "dau-vivado"
    manifest_path: Path | None = None
    command_plan_path: Path | None = None
    project_manifest_path: Path | None = None
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    execute: bool = False

    def overlay_build_model(self) -> VivadoOverlayBuildTask:
        return VivadoOverlayBuildTask(
            work_root=self.work_root,
            bitstream=self.bitstream,
            vivado=self.vivado,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
            overlay_tcl=self.overlay_tcl,
            build_tcl=self.build_tcl,
            vivado_settings=self.vivado_settings,
            execute=self.execute,
        )

    def artifact_validation_model(self) -> ValidateVivadoArtifactsTask:
        return ValidateVivadoArtifactsTask(
            work_root=self.work_root,
            bitstream=self.bitstream,
            vivado=self.vivado,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
            artifact_stem=self.artifact_stem,
            manifest_path=self.manifest_path,
            command_plan_path=self.command_plan_path,
            project_manifest_path=self.project_manifest_path,
            execute=self.execute,
        )

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        result = super().__call__(context)
        if not self.execute:
            return BuildStepResult(step="build-vivado-artifacts", message=result.message)
        return BuildStepResult(
            step="build-vivado-artifacts",
            message=(f"dau-build-artifacts\ttask=build-vivado-artifacts backend={self.backend} steps=2 status=executed\n{result.message}"),
        )


class HardwarePlanTask(BuildCallableModel):
    # the plan is the composed `plan` group option (a HardwarePlan model that
    # owns its own required fields); the task holds the shared toolchain config
    plan: Any = None
    work_root: Path
    bitstream: Path | None = None
    vivado: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    openfpgaloader: str = "openFPGALoader"
    jtag_cable: str = "digilent_hs2"
    endpoint_bdf: str = "0000:04:00.0"
    execute: bool = False

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        plan = self._plan()
        config = HardwareToolchainConfig(
            work_root=self.work_root,
            vivado_executable=self.vivado,
            vivado_invocation=self.vivado_invocation,
            vivado_mount_root=self.vivado_mount_root,
            bitstream_path=self.bitstream,
            openfpgaloader_executable=self.openfpgaloader,
            jtag_cable=self.jtag_cable,
            endpoint_bdf=self.endpoint_bdf,
        )
        plan_result = plan.compose(config)
        if self.execute:
            return_code = execute_plan_steps(plan_result)
            if return_code != 0:
                raise BuildStepError(f"hardware plan {plan.name!r} failed with exit code {return_code}")
            return BuildStepResult(
                step="hardware-plan",
                message=f"dau-build-hardware-plan\ttask=hardware-plan plan={plan.name} steps={len(plan_result)} status=executed",
            )
        return BuildStepResult(step="hardware-plan", message=format_plan_steps(plan_result))

    def _plan(self) -> HardwarePlan:
        if isinstance(self.plan, HardwarePlan):
            return self.plan
        raise BuildStepError("task=hardware-plan requires plan=plans/<name> (see dau_build/config/plan)")


def _model_types_from_config_group(kind: str) -> Mapping[str, type[BuildCallableModel]]:
    """The hydra config tree is the single registry of steps/tasks: each
    ``config/<kind>/<name>.yaml`` names its model via ``_target_``, and the
    name→class maps are derived from it, never hand-maintained."""
    import importlib

    import yaml

    model_types: dict[str, type[BuildCallableModel]] = {}
    group_dir = Path(__file__).parent / "config" / kind
    for path in sorted(group_dir.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        target = raw.get("_target_") if isinstance(raw, Mapping) else None
        if not isinstance(target, str) or not target:
            raise BuildStepError(f"config {kind}/{path.relative_to(group_dir)} must declare _target_")
        module_name, _, attribute = target.rpartition(".")
        # path-style key: config/task/tasks/build/synthesize.yaml -> tasks/build/synthesize
        key = path.relative_to(group_dir).with_suffix("").as_posix()
        model_types[key] = getattr(importlib.import_module(module_name), attribute)
    return MappingProxyType(model_types)


STEP_MODEL_TYPES: Mapping[str, type[BuildCallableModel]] = _model_types_from_config_group("step")

TASK_MODEL_TYPES: Mapping[str, type[BuildCallableModel]] = _model_types_from_config_group("task")


def available_step_names() -> tuple[str, ...]:
    return tuple(sorted(STEP_MODEL_TYPES))


def available_task_names() -> tuple[str, ...]:
    return tuple(sorted(TASK_MODEL_TYPES))


def parse_override_dict(arguments: Iterable[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for argument in arguments:
        normalized_argument = argument[1:] if argument.startswith("+") else argument
        if "=" not in normalized_argument:
            raise BuildStepError(f"expected key=value override, got {argument!r}")
        key, value = normalized_argument.split("=", 1)
        if not key:
            raise BuildStepError(f"expected non-empty override key, got {argument!r}")
        if key in overrides:
            raise BuildStepError(f"duplicate override {key!r}")
        overrides[key] = value
    return overrides


def execute_override_step(arguments: Iterable[str]) -> BuildStepResult:
    overrides = parse_override_dict(arguments)
    step_name = overrides.pop("step", None)
    if not step_name:
        raise BuildStepError("missing required override: step")
    return _execute_named_callable(STEP_MODEL_TYPES, step_name, overrides, request_kind="step")


def execute_override_task(arguments: Iterable[str]) -> BuildStepResult:
    overrides = parse_override_dict(arguments)
    task_name = overrides.pop("task", None)
    if not task_name:
        raise BuildStepError("missing required override: task")
    if "step" in overrides:
        raise BuildStepError("task requests cannot also provide step")
    return _execute_named_callable(TASK_MODEL_TYPES, task_name, overrides, request_kind="task")


def execute_override_request(arguments: Iterable[str]) -> BuildStepResult:
    overrides = parse_override_dict(arguments)
    task_name = overrides.pop("task", None)
    step_name = overrides.pop("step", None)
    if task_name and step_name:
        raise BuildStepError("provide either task or step, not both")
    if task_name:
        return _execute_named_callable(TASK_MODEL_TYPES, task_name, overrides, request_kind="task")
    if step_name:
        return _execute_named_callable(STEP_MODEL_TYPES, step_name, overrides, request_kind="step")
    raise BuildStepError("missing required override: task")


def _execute_named_callable(
    model_types: Mapping[str, type[BuildCallableModel]], request_name: str, overrides: Mapping[str, str], *, request_kind: str
) -> BuildStepResult:
    try:
        model_type = model_types[request_name]
    except KeyError as exc:
        known_names = ", ".join(sorted(model_types))
        raise BuildStepError(f"unknown build {request_kind} {request_name!r}; expected one of: {known_names}") from exc
    try:
        model = model_type.model_validate(dict(overrides))
    except ValidationError as exc:
        raise BuildStepError(_validation_error_message(request_kind, request_name, exc)) from exc

    from dau_build.config import run_request_config

    result = run_request_config(request_kind, request_name, model_values=_model_config_values(model))
    if not isinstance(result, BuildStepResult):
        raise BuildStepError(f"build {request_kind} {request_name!r} returned unsupported result {type(result).__name__}")
    return result


def _execute_callable_model(
    model_type: type[BuildCallableModel], overrides: Mapping[str, str], *, request_kind: str, request_name: str
) -> BuildStepResult:
    try:
        model = model_type.model_validate(dict(overrides))
    except ValidationError as exc:
        raise BuildStepError(_validation_error_message(request_kind, request_name, exc)) from exc
    return model(NullContext())


def _model_config_values(model: BuildCallableModel) -> dict[str, Any]:
    raw = model.model_dump(mode="python", by_alias=False, exclude={"meta", "type_"})
    return {key: _request_config_value(value) for key, value in raw.items()}


def _request_config_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return [_request_config_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _request_config_value(item) for key, item in value.items()}
    return value


def _validation_error_message(request_kind: str, request_name: str, exc: ValidationError) -> str:
    errors = exc.errors()
    missing = tuple(_error_location(error) for error in errors if error.get("type") == "missing")
    if missing:
        return f"missing required override(s) for {request_kind}={request_name}: {', '.join(missing)}"
    details = "; ".join(f"{_error_location(error)}: {error.get('msg', 'invalid value')}" for error in errors)
    return f"invalid override(s) for {request_kind}={request_name}: {details}"


def _error_location(error: Mapping[str, object]) -> str:
    loc = error.get("loc", ())
    if not isinstance(loc, tuple):
        return str(loc)
    return ".".join(str(part) for part in loc)


def _unique_paths(paths: Iterable[Path | str]) -> tuple[Path, ...]:
    unique: dict[Path, None] = {}
    for path in paths:
        unique[Path(path)] = None
    return tuple(unique.keys())


def _run_verilator_bench(spec, *, extra_sources, top_module, expect_stdout, verilator, extra_args, work_dir):
    """Run a Verilator testbench over the spec sources plus extra sources;
    raise BuildStepError on failure or missing expected stdout."""
    try:
        from dau_sim.integrations.verilator import VerilatorExecutionError, VerilatorUnavailableError, run_verilator_testbench
    except ModuleNotFoundError as exc:
        raise BuildStepError("verilator simulation requires dau-sim to be importable") from exc
    all_sources = _unique_paths((*spec.sources, *extra_sources))
    try:
        result = run_verilator_testbench(
            sources=all_sources,
            top_module=top_module,
            work_dir=work_dir,
            verilator=verilator,
            extra_args=tuple(shlex.split(extra_args)),
        )
    except (FileNotFoundError, ValueError, VerilatorExecutionError, VerilatorUnavailableError) as exc:
        raise BuildStepError(str(exc)) from exc
    if expect_stdout and expect_stdout not in result.stdout:
        raise BuildStepError(f"verilator stdout did not contain expected text {expect_stdout!r}")
    return result


def _split_path_tuple(value) -> tuple[Path, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, Path):
        return (value,)
    if isinstance(value, str):
        return tuple(Path(item) for item in value.split(",") if item)
    if isinstance(value, Iterable):
        return tuple(Path(item) for item in value)
    return value


def _vivado_artifact_validation_message(validation: VivadoBackendArtifactValidation | VivadoProjectArtifactValidation) -> str:
    project = f"project={validation.project_manifest_path} " if isinstance(validation, VivadoProjectArtifactValidation) else ""
    if validation.ok:
        build_status = f" build_status={validation.build_status}" if validation.build_status else ""
        resource_summary = f" resource_summary={validation.resource_summary_path}" if validation.resource_summary_path else ""
        timing_summary = f" timing_summary={validation.timing_summary_path}" if validation.timing_summary_path else ""
        vivado_log = f" vivado_log={validation.vivado_log_path}" if validation.vivado_log_path else ""
        return (
            "vivado-artifacts-valid\t"
            f"{project}"
            f"manifest={validation.manifest_path} "
            f"overlay={validation.overlay_tcl_path} "
            f"command_plan={validation.command_plan_path} "
            f"bitstream={validation.bitstream_path}"
            f"{build_status}"
            f"{resource_summary}"
            f"{timing_summary}"
            f"{vivado_log}"
        )
    return "\n".join(
        (
            f"vivado-artifacts-invalid\t{project}manifest={validation.manifest_path} command_plan={validation.command_plan_path}",
            *(f"error\t{error}" for error in validation.errors),
        )
    )


def _write_vivado_backend_handoff(
    spec,
    *,
    selected_module: str,
    output_root: Path,
    dau_artifact_bundle_path: Path,
    platform: str,
    shell: str,
    operator_set: tuple[str, ...],
) -> VivadoBackendArtifacts:
    try:
        backend_artifacts = generate_vivado_backend_artifacts(
            VivadoBackendRequest(
                dau_core_hdl_root=_spec_hdl_root(spec),
                build_root=output_root / "vivado",
                dau_artifact_bundle_path=dau_artifact_bundle_path.resolve(),
                artifact_stem=spec.artifact_stem,
                platform=platform,
                shell=shell,
                operator_set=operator_set,
                selected_module=selected_module,
                register_map_version=spec.register_map_version,
                stream_protocol_version=spec.stream_protocol_version,
            )
        )
    except ValueError as exc:
        raise BuildStepError(str(exc)) from exc
    _write_vivado_backend_artifacts(backend_artifacts)
    return backend_artifacts


def _write_vivado_backend_artifacts(artifacts: VivadoBackendArtifacts) -> None:
    outputs: list[tuple[Path, str | None]] = [
        (artifacts.overlay_tcl_path, artifacts.overlay_tcl_text),
        (artifacts.build_tcl_path, artifacts.build_tcl_text),
        (artifacts.manifest_path, artifacts.manifest_text),
        (artifacts.command_plan_path, artifacts.command_plan_text),
        (artifacts.overlay_driver_tcl_path, artifacts.overlay_driver_tcl_text),
        (artifacts.build_driver_tcl_path, artifacts.build_driver_tcl_text),
    ]
    for path, text in outputs:
        if path is None or text is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _spec_hdl_root(spec) -> Path:
    # the overlay HDL root is wherever the spec's sources live — dau-build
    # stays independent of any particular HDL package
    if not spec.sources:
        raise BuildStepError("spec provides no HDL sources to derive the HDL root from")
    return Path(spec.sources[0]).parent


def _read_key_value_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise BuildStepError(f"manifest does not exist: {path}")
    manifest: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BuildStepError(f"manifest line {line_number} is missing '=': {path}")
        key, value = line.split("=", 1)
        if not key:
            raise BuildStepError(f"manifest line {line_number} has an empty key: {path}")
        manifest[key] = value
    return manifest


def _manifest_required(manifest: Mapping[str, str], key: str) -> str:
    value = manifest.get(key)
    if not value:
        raise BuildStepError(f"manifest missing required key: {key}")
    return value


def _manifest_path(root: Path, manifest: Mapping[str, str], key: str) -> Path:
    value = Path(_manifest_required(manifest, key))
    if value.is_absolute():
        return value
    return root / value


def _require_built_manifest(manifest_path: Path, manifest: Mapping[str, str]) -> None:
    build_status = manifest.get("build_status") or "<missing>"
    if build_status != "built":
        raise BuildStepError(f"manifest {manifest_path} is not built: build_status={build_status}; expected built")

    missing: list[str] = []
    for key in ("bitstream", "resource_summary", "timing_summary", "vivado_log"):
        try:
            artifact_path = _manifest_path(manifest_path.parent, manifest, key)
        except BuildStepError as exc:
            missing.append(str(exc))
            continue
        if not artifact_path.is_file():
            missing.append(f"missing {key}: {artifact_path}")
    if missing:
        raise BuildStepError(f"manifest {manifest_path} is built but incomplete: {'; '.join(missing)}")
