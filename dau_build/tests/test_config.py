from __future__ import annotations

from pathlib import Path

from ccflow import CallableModel
from ccflow.utils.hydra import cfg_run, load_config as base_load_config
from omegaconf import OmegaConf

from dau_build.build_steps import TASK_MODEL_TYPES, BuildStepResult
from dau_build.config import compose_config, load_config
from dau_build.tests.spec_option import write_spec_option

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_spec_hydra_group_composes_a_buildspec() -> None:
    # the packaged `spec=identity` group composes a BuildSpec into model.spec
    result = base_load_config(
        root_config_dir=str(_CONFIG_DIR),
        root_config_name="base",
        overrides=["task=tasks/spec/inspect", "spec=specs/identity"],
        basepath=str(_CONFIG_DIR),
        debug=False,
    )
    output = cfg_run(result.cfg)
    assert output.step == "inspect"
    assert "name=identity-pipeline" in output.message


def test_packaged_task_config_targets_follow_the_callable_registry() -> None:
    assert _config_group_names("task") == tuple(sorted(TASK_MODEL_TYPES))

    for name, model_type in TASK_MODEL_TYPES.items():
        cfg = OmegaConf.load(_CONFIG_DIR / "task" / f"{name}.yaml")
        assert cfg["_target_"] == _target(model_type)


def test_packaged_task_configs_instantiate_registered_callable_models(tmp_path: Path) -> None:
    for name, model_type in TASK_MODEL_TYPES.items():
        registry = load_config([f"task={name}", *_task_overrides(name, tmp_path)], overwrite=True)
        model = registry["model"]
        assert isinstance(model, model_type)
        assert isinstance(model, CallableModel)


def test_packaged_base_config_runs_selected_callable_with_ccflow_cfg_run(tmp_path: Path) -> None:
    config_dir, spec = write_spec_option(tmp_path, _spec_text())
    result = compose_config(["task=tasks/sim/simulate", spec, "model.module=dau_identity_top"], config_dir=config_dir)

    output = cfg_run(result.cfg)

    assert output == BuildStepResult(
        step="simulate",
        message="dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec=identity-pipeline status=validated",
    )


def _config_group_names(kind: str) -> tuple[str, ...]:
    group_dir = _CONFIG_DIR / kind
    return tuple(sorted(path.relative_to(group_dir).with_suffix("").as_posix() for path in group_dir.rglob("*.yaml")))


def _target(model_type: type) -> str:
    return f"{model_type.__module__}.{model_type.__name__}"


def _task_overrides(name: str, tmp_path: Path) -> tuple[str, ...]:
    base = {
        "build-shell-project": (f"model.output_root={tmp_path / 'shell'}",),
        "build-vivado-artifacts": (f"model.work_root={tmp_path / 'work'}",),
        "hardware-plan": ("model.plan=thunderbolt-release", f"model.work_root={tmp_path / 'work'}"),
        "overlay-build": (f"model.work_root={tmp_path / 'work'}",),
        "simulate": ("model.module=dau_identity_top",),
        "inspect": (),
        "build": (f"model.output_root={tmp_path / 'artifacts'}",),
        "validate": (),
        "stage-shell": (f"model.work_root={tmp_path / 'work'}", f"model.source_shell_root={tmp_path / 'shell'}"),
        "stage-vivado-overlay": (f"model.work_root={tmp_path / 'work'}", f"model.dau_core_root={tmp_path / 'dau-core'}"),
        "stage-vivado-project": (
            f"model.work_root={tmp_path / 'work'}",
            f"model.source_shell_root={tmp_path / 'shell'}",
            f"model.dau_core_root={tmp_path / 'dau-core'}",
            f"model.dau_driver_root={tmp_path / 'dau-driver'}",
        ),
        "synthesize": ("model.module=dau_identity_top", f"model.output_root={tmp_path / 'out'}"),
        "synthesize-cores": ("model.cores=[/dau-core/streaming-top-k]", f"model.output_root={tmp_path / 'ooc'}"),
        "render-cores": ("model.cores=[/dau-core/streaming-top-k]", f"model.output_root={tmp_path / 'render'}"),
        "validate-vivado-artifacts": (f"model.work_root={tmp_path / 'work'}",),
    }
    return base[name.split("/")[-1]]


def _spec_text() -> str:
    return "\n".join(
        (
            "name: identity-pipeline",
            "top_name: dau_identity_top",
            "platform: vivado-xdma",
            "shell: xdma-ddr",
            "artifact_stem: dau-identity",
            'register_map_version: "0.1"',
            'stream_protocol_version: "0.1"',
            "clock: clk",
            "reset: reset",
            "operators:",
            "  - identity",
            "sources:",
            f"  - {(_SV_DIR / 'ff.sv').as_posix()}",
            "modules:",
            "  - ff",
            "backend: none",
            "",
        )
    )


def test_nested_board_and_backend_config_groups_compose() -> None:
    # path-style group selection: board=boards/example/probe backend=backends/vivado.
    # the backend group composes a synthesis engine model.
    from hydra import compose, initialize_config_module
    from hydra.utils import instantiate

    from dau_build.build_config import BoardConfig
    from dau_build.build_steps import VivadoEngine

    with initialize_config_module(config_module="dau_build.config", version_base=None):
        cfg = compose(config_name="base", overrides=["board=boards/example/probe", "backend=backends/vivado"])
    board = instantiate(cfg.board)
    backend = instantiate(cfg.backend)
    assert isinstance(board, BoardConfig) and board.name == "probe" and board.platform == "vivado-xdma"
    assert isinstance(backend, VivadoEngine) and backend.name == "vivado"


def test_programmer_config_group_composes_the_adapter() -> None:
    # programmer=programmers/<name> selects a Programmer model, symmetric to
    # backend=backends/<name>; adding a group yaml is all it takes to select
    # a programming adapter.
    from hydra import compose, initialize_config_module
    from hydra.utils import instantiate

    from dau_build.programmers import OpenFpgaLoaderProgrammer, VivadoHwServerProgrammer

    with initialize_config_module(config_module="dau_build.config", version_base=None):
        jtag = instantiate(compose(config_name="base", overrides=["programmer=programmers/openfpgaloader"]).programmer)
        flash = instantiate(compose(config_name="base", overrides=["programmer=programmers/vivado-hwserver"]).programmer)
    assert isinstance(jtag, OpenFpgaLoaderProgrammer) and jtag.name == "openfpgaloader" and jtag.executable == "openFPGALoader"
    assert isinstance(flash, VivadoHwServerProgrammer) and flash.name == "vivado-hwserver"


def test_dau_build_registers_a_hydra_searchpath_entry_point() -> None:
    # the config tree is on the Hydra search path (lerna bridge) so packages
    # and users can extend it; dau-build must register itself
    from importlib.metadata import entry_points

    registered = {ep.name: ep.value for ep in entry_points(group="hydra.lernaplugins")}
    assert registered.get("dau-build") == "pkg:dau_build.config"


def _resolved(overrides: list[str]):
    """The resolved build config (spec + composed groups) a task sees."""
    model = load_config(overrides, overwrite=True)["model"]
    return model._resolved(model.load_spec())


def test_board_and_backend_groups_override_spec_derived_resolved_config() -> None:
    # board=/backend= compose into the task and win over the spec-derived view
    base = ["task=tasks/spec/inspect", "spec=specs/identity"]
    derived = _resolved(base)
    composed = _resolved([*base, "board=boards/example/probe", "backend=backends/vivado"])
    assert derived.board.name == "vivado-xdma"  # spec-derived: board name = platform
    assert composed.board.name == "probe"  # composed board wins
    assert (composed.backend.name, composed.backend.invocation) == ("vivado", "standard")  # composed backend wins


def test_driver_and_memory_config_groups_compose_and_override() -> None:
    # driver=/memory= compose into the resolved config; fields are overridable
    resolved = _resolved(
        [
            "task=tasks/spec/inspect",
            "spec=specs/identity",
            "driver=drivers/host",
            "memory=memories/default",
            "memory.host_staging_bytes=4096",
        ]
    )
    assert (resolved.driver.os, resolved.driver.transport) == ("host", "xdma")
    assert (resolved.memory.host_staging_bytes, resolved.memory.device_staging_bytes) == (4096, 0)


def test_an_unresolvable_task_is_refused_including_under_explain() -> None:
    """The task group is `optional`, so hydra drops a value it cannot find
    instead of failing. Without a guard a mistyped task reads as success
    under --explain, which is the one command whose job is to show what
    will happen before it happens."""
    import pytest

    from dau_build.build_steps import BuildStepError
    from dau_build.cli import main

    with pytest.raises(BuildStepError, match="did not resolve"):
        main(["task=tasks/does/not/exist", "--explain"])
    with pytest.raises(BuildStepError, match="did not resolve"):
        main(["task=tasks/does/not/exist"])


def test_a_valid_task_and_a_bare_invocation_still_explain() -> None:
    from dau_build.cli import main

    assert main(["task=tasks/build/synthesize-cores", "--explain"]) == 0
    assert main(["--explain"]) == 0
