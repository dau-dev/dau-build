import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeVar

from ccflow import CallableModel, Flow, NullContext, ResultBase
from pydantic import Field, ValidationError, field_validator

from dau_build.hardware_plan import (
    HardwareToolchainConfig,
    build_and_program_plan,
    execute_plan_steps,
    flash_plan as hardware_flash_plan,
    format_plan_steps,
    local_build_and_program_plan,
    recovery_plan,
    stage_shell_plan,
    stage_vivado_overlay_plan,
    stage_vivado_project_plan,
    thunderbolt_hold_plan,
    thunderbolt_release_plan,
    validate_bitstream_plan,
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


def _resolve_build_config(spec, overrides):
    """Deferred for the same reason: build_config imports build_spec."""
    from dau_build.build_config import resolve_build_config

    return resolve_build_config(spec, overrides)


class BuildStepError(ValueError):
    pass


class BuildStepResult(ResultBase):
    step: str
    message: str


BuildCallableModelType = TypeVar("BuildCallableModelType", bound="BuildCallableModel")


class BuildCallableModel(CallableModel):
    _STRINGIFY_SEPARATOR: ClassVar[str] = ","

    def override_mapping(self) -> dict[str, str]:
        raw = self.model_dump(mode="python", by_alias=True, exclude_none=True, exclude={"meta", "type_"})
        raw.pop("_target_", None)
        return {key: self._stringify_override_value(value) for key, value in raw.items()}

    @classmethod
    def _stringify_override_value(cls, value) -> str:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, tuple | list):
            return cls._STRINGIFY_SEPARATOR.join(str(item) for item in value)
        return str(value)


class ConfigOverrideModel(BuildCallableModel):
    board_name: str | None = Field(default=None, alias="board.name")
    board_platform: str | None = Field(default=None, alias="board.platform")
    board_shell: str | None = Field(default=None, alias="board.shell")
    backend_name: str | None = Field(default=None, alias="backend.name")
    backend_invocation: str | None = Field(default=None, alias="backend.invocation")
    driver_os: str | None = Field(default=None, alias="driver.os")
    driver_transport: str | None = Field(default=None, alias="driver.transport")
    operator_set: str | None = Field(default=None, alias="operator.set")
    memory_host_staging_bytes: int | None = Field(default=None, alias="memory.host_staging_bytes")
    memory_device_staging_bytes: int | None = Field(default=None, alias="memory.device_staging_bytes")

    def config_overrides(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.override_mapping().items()
            if key
            in {
                "board.name",
                "board.platform",
                "board.shell",
                "backend.name",
                "backend.invocation",
                "driver.os",
                "driver.transport",
                "operator.set",
                "memory.host_staging_bytes",
                "memory.device_staging_bytes",
            }
        }


class SpecPathModel(ConfigOverrideModel):
    spec_path: Path

    def load_spec(self):
        return _build_spec_api().load_dau_build_spec(self.spec_path)


class ModuleSelectionModel(SpecPathModel):
    module: str

    def load_spec_and_validate_module(self):
        spec = self.load_spec()
        provided_modules = (spec.top_name, *spec.modules)
        if self.module not in provided_modules:
            expected = ", ".join(provided_modules)
            raise BuildStepError(f"module {self.module!r} is not provided by spec {self.spec_path}; expected one of: {expected}")
        return spec


class InspectStep(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        return BuildStepResult(step="inspect", message=_build_spec_api().dau_build_spec_summary(self.load_spec()))


class ValidateStep(SpecPathModel):
    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        self.load_spec()
        return BuildStepResult(step="validate", message=f"dau-build-spec-valid\tspec={self.spec_path}")


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
        resolved = _resolve_build_config(self.load_spec(), self.override_mapping())
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
        _resolve_build_config(spec, self.override_mapping())
        _build_spec_api().generate_dau_build_artifacts(spec, output_root=self.output_root or self.spec_path.parent / ".dau-build-sim")
        if self.simulate_engine == "verilator":
            return self._run_verilator(spec)
        return BuildStepResult(
            step="simulate",
            message=(
                f"dau-build-simulate\tspec={self.spec_path} top={spec.top_name} modules={','.join(spec.modules)} "
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

        work_dir = self.output_root or self.spec_path.parent / ".dau-build-sim" / "verilator"
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
                f"dau-build-simulate\tspec={self.spec_path} top={spec.top_name} modules={','.join(spec.modules)} sources={len(spec.sources)} "
                f"engine=verilator {mode_segment}testbench_top={top_module} work_dir={work_dir} status=passed"
            ),
        )


class SynthesisStep(SpecPathModel):
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        spec = self.load_spec()
        resolved = _resolve_build_config(spec, self.override_mapping())
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
        resolved = _resolve_build_config(spec, self.override_mapping())
        return BuildStepResult(
            step="explain",
            message="\n".join(
                (
                    "dau-build-explain",
                    f"spec\tpath={self.spec_path} name={spec.name} top={spec.top_name}",
                    f"board\tname={resolved.board.name} platform={resolved.board.platform} shell={resolved.board.shell}",
                    "actions\tvalidate=local simulate=local synthesis=local-handoff artifacts=generate-or-write",
                )
            ),
        )


class SimulateTask(ModuleSelectionModel):
    # spec_path/module are optional for profile-only Verilator runs, where a
    # registered profile already carries its sources and top module
    spec_path: Path | None = None
    module: str = ""
    simulator: Literal["cocotb", "svparser", "verilator"] = "svparser"
    output_root: Path | None = None
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

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        if self.simulator == "verilator" and self.profile and self.spec_path is None:
            return self._run_registered_profile()
        if self.spec_path is None or not self.module:
            raise BuildStepError("task=simulate requires spec_path and module (or simulator=verilator profile=<name> for a registered profile)")
        spec = self.load_spec_and_validate_module()
        output_root = self.output_root or self.spec_path.parent / ".dau-build-sim"
        if self.simulator in {"svparser", "cocotb"}:
            _resolve_build_config(spec, self.override_mapping())
            _build_spec_api().generate_dau_build_artifacts(spec, output_root=output_root)
            return BuildStepResult(
                step="simulate",
                message=f"dau-build-simulate\ttask=simulate simulator={self.simulator} module={self.module} spec={self.spec_path} status=validated",
            )
        step_data = self.config_overrides()
        step_data.update(
            {
                "spec_path": str(self.spec_path),
                "output_root": str(output_root),
                "simulate.engine": "verilator",
                "simulate.verilator": self.verilator,
                "simulate.extra_args": self.extra_args,
            }
        )
        if self.profile:
            step_data["simulate.profile"] = self.profile
        if self.profile_manifest:
            step_data["simulate.profile_manifest"] = self._stringify_override_value(self.profile_manifest)
        if self.testbench_path:
            step_data["simulate.testbench_path"] = str(self.testbench_path)
        if self.top_module:
            step_data["simulate.top_module"] = self.top_module
        if self.expect_stdout:
            step_data["simulate.expect_stdout"] = self.expect_stdout
        result = _execute_callable_model(SimulateStep, step_data, request_kind="step", request_name="simulate")
        return BuildStepResult(step="simulate", message=f"{result.message} task=simulate simulator=verilator module={self.module}")

    def _run_registered_profile(self) -> BuildStepResult:
        from dau_sim.integrations.verilator import run_verilator_testbench

        from dau_build.simulation_profiles import resolve_profile

        profile = resolve_profile(self.profile, profile_manifests=self.profile_manifest)
        work_root = self.output_root or Path.cwd() / ".dau-build-sim"
        work_dir = work_root / profile.name
        work_dir.mkdir(parents=True, exist_ok=True)
        result = run_verilator_testbench(sources=profile.sources, top_module=profile.top_module, work_dir=work_dir)
        marker = self.expect_stdout or profile.expect_stdout
        if marker not in result.stdout:
            raise BuildStepError(f"profile {profile.name!r} did not report {marker!r}")
        return BuildStepResult(
            step="simulate",
            message=f"dau-build-simulate	task=simulate simulator=verilator profile={profile.name} status=passed marker={marker}",
        )


class SynthesizeTask(ModuleSelectionModel):
    engine: Literal["vivado"]
    output_root: Path

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        spec = self.load_spec_and_validate_module()
        task_overrides = self.override_mapping()
        task_overrides["backend.name"] = self.engine
        resolved = _resolve_build_config(spec, task_overrides)
        artifacts = _build_spec_api().write_dau_build_artifacts(spec, output_root=self.output_root)
        backend_artifacts = _write_vivado_backend_handoff(
            spec,
            selected_module=self.module,
            output_root=self.output_root,
            dau_artifact_bundle_path=artifacts.artifact_manifest_path,
            platform=resolved.board.platform,
            shell=resolved.board.shell,
            operator_set=resolved.operators.names,
        )
        return BuildStepResult(
            step="synthesize",
            message=(
                f"dau-build-synthesize\ttask=synthesize engine={self.engine} module={self.module} spec={self.spec_path} "
                f"output_root={self.output_root} manifest={artifacts.manifest_path} top_sv={artifacts.top_sv_path} "
                f"backend_manifest={backend_artifacts.manifest_path} command_plan={backend_artifacts.command_plan_path} status=handoff-written"
            ),
        )


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
    if bitstream.digest is not None:
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
                bitstream = bitstream or _manifest_path(self.manifest_path.parent, manifest, "bitstream")
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
            metadata={**self.metadata, **status},
        )
        return BuildStepResult(
            step="build-shell-project",
            message=(
                f"dau-build-shell\ttask=build-shell-project output_root={self.output_root}"
                f" wns={status.get('wns_ns')} manifest={manifest_path} status=built"
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
        return BuildStepResult(step="validate-vivado-artifacts", message=_vivado_artifact_validation_message(validation))

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
    plan: Literal[
        "build-and-program",
        "flash",
        "local-build-and-program",
        "recovery",
        "thunderbolt-hold",
        "thunderbolt-release",
        "validate-bitstream",
    ]
    work_root: Path
    source_shell_root: Path | None = None
    bitstream: Path | None = None
    vivado: str = "vivado"
    vivado_invocation: Literal["standard", "source-only"] = "standard"
    vivado_mount_root: Path | None = None
    openfpgaloader: str = "openFPGALoader"
    jtag_cable: str = "digilent_hs2"
    endpoint_bdf: str = "0000:04:00.0"
    dau_core_root: Path | None = None
    dau_driver_root: Path | None = None
    dau_utils_root: Path | None = None
    overlay_tcl: Path = Path("scripts/dau_overlay.tcl")
    python: str = "python3"
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")
    execute: bool = False

    @Flow.call
    def __call__(self, context: NullContext) -> BuildStepResult:
        plan_result = self._plan_result()
        if self.execute:
            return_code = execute_plan_steps(plan_result)
            if return_code != 0:
                raise BuildStepError(f"hardware plan {self.plan!r} failed with exit code {return_code}")
            return BuildStepResult(
                step="hardware-plan",
                message=f"dau-build-hardware-plan\ttask=hardware-plan plan={self.plan} steps={len(plan_result)} status=executed",
            )
        return BuildStepResult(step="hardware-plan", message=format_plan_steps(plan_result))

    def _plan_result(self):
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
        composers = {
            "build-and-program": lambda: build_and_program_plan(config),
            "local-build-and-program": lambda: local_build_and_program_plan(
                config,
                dau_core_root=self._required_path("dau_core_root"),
                dau_driver_root=self._required_path("dau_driver_root"),
                source_shell_root=self.source_shell_root,
                dau_utils_root=self.dau_utils_root,
                overlay_tcl=self.overlay_tcl,
                python=self.python,
                vivado_settings=self.vivado_settings,
            ),
            "validate-bitstream": lambda: validate_bitstream_plan(
                config,
                dau_core_root=self._required_path("dau_core_root"),
                dau_driver_root=self._required_path("dau_driver_root"),
                dau_utils_root=self.dau_utils_root,
                python=self.python,
            ),
            "flash": lambda: hardware_flash_plan(
                config, dau_utils_root=self.dau_utils_root, python=self.python, vivado_settings=self.vivado_settings
            ),
            "recovery": lambda: recovery_plan(config),
            "thunderbolt-hold": lambda: thunderbolt_hold_plan(config),
            "thunderbolt-release": lambda: thunderbolt_release_plan(config),
        }
        return composers[self.plan]()

    def _required_path(self, field_name: str) -> Path:
        value = getattr(self, field_name)
        if value is None:
            raise BuildStepError(f"task=hardware-plan plan={self.plan} requires {field_name}")
        return value


def _model_types_from_config_group(kind: str) -> Mapping[str, type[BuildCallableModel]]:
    """The hydra config tree is the single registry of steps/tasks: each
    ``config/<kind>/<name>.yaml`` names its model via ``_target_``, and the
    name→class maps are derived from it, never hand-maintained."""
    import importlib

    import yaml

    model_types: dict[str, type[BuildCallableModel]] = {}
    for path in sorted((Path(__file__).parent / "config" / kind).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        target = raw.get("_target_") if isinstance(raw, Mapping) else None
        if not isinstance(target, str) or not target:
            raise BuildStepError(f"config {kind}/{path.name} must declare _target_")
        module_name, _, attribute = target.rpartition(".")
        model_types[path.stem] = getattr(importlib.import_module(module_name), attribute)
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
