from __future__ import annotations

from pathlib import Path

from ccflow import CallableModel
from ccflow.utils.hydra import cfg_run, load_config as base_load_config
from omegaconf import OmegaConf

from dau_build.build_steps import STEP_MODEL_TYPES, TASK_MODEL_TYPES, BuildStepResult, execute_override_request
from dau_build.config import load_config

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_spec_hydra_group_composes_a_buildspec() -> None:
    # the packaged `spec=identity` group composes a BuildSpec into model.spec
    # — the Hydra-native replacement for a hand-passed spec_path
    result = base_load_config(
        root_config_dir=str(_CONFIG_DIR),
        root_config_name="base",
        overrides=["step=steps/inspect", "spec=specs/identity"],
        basepath=str(_CONFIG_DIR),
        debug=False,
    )
    output = cfg_run(result.cfg)
    assert output.step == "inspect"
    assert "name=identity-pipeline" in output.message


def test_packaged_task_and_step_config_targets_follow_callable_registries() -> None:
    assert _config_group_names("task") == tuple(sorted(TASK_MODEL_TYPES))
    assert _config_group_names("step") == tuple(sorted(STEP_MODEL_TYPES))

    for name, model_type in TASK_MODEL_TYPES.items():
        cfg = OmegaConf.load(_CONFIG_DIR / "task" / f"{name}.yaml")
        assert cfg["_target_"] == _target(model_type)

    for name, model_type in STEP_MODEL_TYPES.items():
        cfg = OmegaConf.load(_CONFIG_DIR / "step" / f"{name}.yaml")
        assert cfg["_target_"] == _target(model_type)


def test_packaged_task_and_step_configs_instantiate_registered_callable_models(tmp_path: Path) -> None:
    for name, model_type in TASK_MODEL_TYPES.items():
        registry = load_config([f"task={name}", *_task_overrides(name, tmp_path)], overwrite=True)
        model = registry["model"]
        assert isinstance(model, model_type)
        assert isinstance(model, CallableModel)

    for name, model_type in STEP_MODEL_TYPES.items():
        registry = load_config([f"step={name}", *_step_overrides(name, tmp_path)], overwrite=True)
        model = registry["model"]
        assert isinstance(model, model_type)
        assert isinstance(model, CallableModel)


def test_packaged_base_config_runs_selected_callable_with_ccflow_cfg_run(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    result = base_load_config(
        root_config_dir=str(_CONFIG_DIR),
        root_config_name="base",
        overrides=["task=tasks/sim/simulate", f"model.spec_path={spec_path}", "model.module=dau_identity_top"],
        basepath=str(_CONFIG_DIR),
        debug=False,
    )

    output = cfg_run(result.cfg)

    assert output == BuildStepResult(
        step="simulate",
        message=f"dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec={spec_path} status=validated",
    )


def test_public_override_dispatch_runs_packaged_task_configs(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_request_config(request_kind: str, request_name: str, *, model_values, **kwargs):
        captured["request_kind"] = request_kind
        captured["request_name"] = request_name
        captured["model_values"] = model_values
        captured["kwargs"] = kwargs
        return BuildStepResult(step="simulate", message="configured")

    monkeypatch.setattr("dau_build.config.run_request_config", fake_run_request_config)

    result = execute_override_request(
        (
            "task=tasks/sim/simulate",
            "simulator=svparser",
            "module=dau_identity_top",
            "spec_path=examples/identity/dau-build.yaml",
        )
    )

    assert result == BuildStepResult(step="simulate", message="configured")
    assert captured["request_kind"] == "task"
    assert captured["request_name"] == "tasks/sim/simulate"
    assert captured["model_values"] == {
        "spec": None,
        "spec_path": "examples/identity/dau-build.yaml",
        "board": None,
        "backend": None,
        "module": "dau_identity_top",
        "simulator": "svparser",
        "output_root": None,
        "profile": None,
        "profile_manifest": [],
        "testbench_path": None,
        "top_module": None,
        "expect_stdout": None,
        "verilator": "verilator",
        "extra_args": "",
    }


def _config_group_names(kind: str) -> tuple[str, ...]:
    group_dir = _CONFIG_DIR / kind
    return tuple(sorted(path.relative_to(group_dir).with_suffix("").as_posix() for path in group_dir.rglob("*.yaml")))


def _target(model_type: type) -> str:
    return f"{model_type.__module__}.{model_type.__name__}"


def _task_overrides(name: str, tmp_path: Path) -> tuple[str, ...]:
    base = {
        "build-shell-project": (f"model.output_root={tmp_path / 'shell'}",),
        "build-vivado-artifacts": (f"model.work_root={tmp_path / 'work'}",),
        "flash": (),
        "hardware-plan": ("model.plan=thunderbolt-release", f"model.work_root={tmp_path / 'work'}"),
        "overlay-build": (f"model.work_root={tmp_path / 'work'}",),
        "simulate": ("model.spec_path=placeholder.yaml", "model.module=dau_identity_top"),
        "smoke-test": ("model.test=identity",),
        "stage-shell": (f"model.work_root={tmp_path / 'work'}", f"model.source_shell_root={tmp_path / 'shell'}"),
        "stage-vivado-overlay": (f"model.work_root={tmp_path / 'work'}", f"model.dau_core_root={tmp_path / 'dau-core'}"),
        "stage-vivado-project": (
            f"model.work_root={tmp_path / 'work'}",
            f"model.source_shell_root={tmp_path / 'shell'}",
            f"model.dau_core_root={tmp_path / 'dau-core'}",
            f"model.dau_driver_root={tmp_path / 'dau-driver'}",
        ),
        "synthesize": ("model.spec_path=placeholder.yaml", "model.module=dau_identity_top", f"model.output_root={tmp_path / 'out'}"),
        "validate-vivado-artifacts": (f"model.work_root={tmp_path / 'work'}",),
    }
    return base[name.split("/")[-1]]


def _step_overrides(name: str, tmp_path: Path) -> tuple[str, ...]:
    overrides = [("model.spec_path=placeholder.yaml",)]
    if name.split("/")[-1] in {"generate", "synthesis", "write"}:
        overrides.append((f"model.output_root={tmp_path / name}",))
    return tuple(item for group in overrides for item in group)


def _write_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "dau-build.yaml"
    spec_path.write_text(
        "\n".join(
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
        ),
        encoding="utf-8",
    )
    return spec_path


def test_nested_board_and_backend_config_groups_compose() -> None:
    # path-style group selection: board=boards/dau/dpv1 backend=backends/vivado
    from hydra import compose, initialize_config_module
    from hydra.utils import instantiate

    from dau_build.build_config import BackendConfig, BoardConfig

    with initialize_config_module(config_module="dau_build.config", version_base=None):
        cfg = compose(config_name="base", overrides=["board=boards/dau/dpv1", "backend=backends/vivado"])
    board = instantiate(cfg.board)
    backend = instantiate(cfg.backend)
    assert isinstance(board, BoardConfig) and board.name == "dpv1" and board.platform == "vivado-xdma"
    assert isinstance(backend, BackendConfig) and backend.name == "vivado"


def test_dau_build_registers_a_hydra_searchpath_entry_point() -> None:
    # the config tree is on the Hydra search path (lerna bridge) so packages
    # and users can extend it; dau-build must register itself
    from importlib.metadata import entry_points

    registered = {ep.name: ep.value for ep in entry_points(group="hydra.lernaplugins")}
    assert registered.get("dau-build") == "pkg:dau_build.config"


def test_board_and_backend_groups_override_spec_derived_resolved_config() -> None:
    # board=/backend= compose into the task and win over the spec-derived view
    spec = "examples/identity/dau-build.yaml"
    base = ["step=steps/resolved-config", f"model.spec_path={spec}"]
    derived = cfg_run(
        base_load_config(root_config_dir=str(_CONFIG_DIR), root_config_name="base", overrides=base, basepath=str(_CONFIG_DIR), debug=False).cfg
    )
    composed = cfg_run(
        base_load_config(
            root_config_dir=str(_CONFIG_DIR),
            root_config_name="base",
            overrides=[*base, "board=boards/dau/dpv1", "backend=backends/vivado"],
            basepath=str(_CONFIG_DIR),
            debug=False,
        ).cfg
    )
    assert "board\tname=vivado-xdma" in derived.message  # spec-derived: board name = platform
    assert "board\tname=dpv1" in composed.message  # composed board wins
    assert "backend\tname=vivado invocation=standard" in composed.message  # composed backend wins
