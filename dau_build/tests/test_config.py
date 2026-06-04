from __future__ import annotations

from pathlib import Path

from ccflow import CallableModel
from ccflow.utils.hydra import cfg_run, load_config as base_load_config
from omegaconf import OmegaConf

from dau_build.build_steps import STEP_MODEL_TYPES, TASK_MODEL_TYPES, BuildStepResult, execute_override_request
from dau_build.config import load_config

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


def test_packaged_workflow_config_targets_follow_callable_registries() -> None:
    assert _workflow_config_names("task") == tuple(sorted(TASK_MODEL_TYPES))
    assert _workflow_config_names("step") == tuple(sorted(STEP_MODEL_TYPES))

    for name, model_type in TASK_MODEL_TYPES.items():
        cfg = OmegaConf.load(_CONFIG_DIR / "workflow" / "task" / f"{name}.yaml")
        assert cfg["_target_"] == _target(model_type)

    for name, model_type in STEP_MODEL_TYPES.items():
        cfg = OmegaConf.load(_CONFIG_DIR / "workflow" / "step" / f"{name}.yaml")
        assert cfg["_target_"] == _target(model_type)


def test_packaged_workflow_configs_instantiate_registered_callable_models(tmp_path: Path) -> None:
    for name, model_type in TASK_MODEL_TYPES.items():
        registry = load_config([f"workflow=task/{name}", *_task_overrides(name, tmp_path)], overwrite=True)
        model = registry["model"]
        assert isinstance(model, model_type)
        assert isinstance(model, CallableModel)

    for name, model_type in STEP_MODEL_TYPES.items():
        registry = load_config([f"workflow=step/{name}", *_step_overrides(name, tmp_path)], overwrite=True)
        model = registry["model"]
        assert isinstance(model, model_type)
        assert isinstance(model, CallableModel)


def test_packaged_base_config_runs_selected_callable_with_ccflow_cfg_run(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    result = base_load_config(
        root_config_dir=str(_CONFIG_DIR),
        root_config_name="base",
        overrides=[f"model.spec_path={spec_path}", "model.module=dau_identity_top"],
        basepath=str(_CONFIG_DIR),
        debug=False,
    )

    output = cfg_run(result.cfg)

    assert output == BuildStepResult(
        step="simulate",
        message=f"dau-build-simulate\ttask=simulate simulator=svparser module=dau_identity_top spec={spec_path} status=validated",
    )


def test_public_override_dispatch_runs_packaged_workflow_configs(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_workflow_config(workflow: str, *, model_values, **kwargs):
        captured["workflow"] = workflow
        captured["model_values"] = model_values
        captured["kwargs"] = kwargs
        return BuildStepResult(step="simulate", message="configured")

    monkeypatch.setattr("dau_build.config.run_workflow_config", fake_run_workflow_config)

    result = execute_override_request(
        (
            "task=simulate",
            "simulator=svparser",
            "module=dau_identity_top",
            "spec_path=examples/identity/dau-build.yaml",
        )
    )

    assert result == BuildStepResult(step="simulate", message="configured")
    assert captured["workflow"] == "task/simulate"
    assert captured["model_values"] == {
        "board_name": None,
        "board_platform": None,
        "board_shell": None,
        "backend_name": None,
        "backend_invocation": None,
        "driver_os": None,
        "driver_transport": None,
        "operator_set": None,
        "memory_host_staging_bytes": None,
        "memory_device_staging_bytes": None,
        "spec_path": "examples/identity/dau-build.yaml",
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


def _workflow_config_names(kind: str) -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in (_CONFIG_DIR / "workflow" / kind).glob("*.yaml")))


def _target(model_type: type) -> str:
    return f"{model_type.__module__}.{model_type.__name__}"


def _task_overrides(name: str, tmp_path: Path) -> tuple[str, ...]:
    base = {
        "flash": (),
        "hardware-plan": ("model.plan=thunderbolt-release", f"model.work_root={tmp_path / 'work'}"),
        "simulate": ("model.spec_path=placeholder.yaml", "model.module=dau_identity_top"),
        "smoke-test": ("model.test=identity",),
        "synthesize": ("model.spec_path=placeholder.yaml", "model.module=dau_identity_top", f"model.output_root={tmp_path / 'out'}"),
    }
    return base[name]


def _step_overrides(name: str, tmp_path: Path) -> tuple[str, ...]:
    overrides = [("model.spec_path=placeholder.yaml",)]
    if name in {"generate", "synthesis", "write"}:
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
