from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

from ccflow import ModelRegistry
from ccflow.utils.hydra import ConfigLoadResult, cfg_run, load_config as base_load_config
from omegaconf import OmegaConf

__all__ = ("load_config", "run_workflow_config", "workflow_config")


def load_config(
    overrides: Sequence[str] | None = None,
    *,
    overwrite: bool = False,
    config_dir: str | None = None,
    config_key: str | None = None,
    version_base: str | None = None,
) -> ModelRegistry:
    result = _load_base_config(overrides, config_dir=config_dir, version_base=version_base)
    cfg = result.cfg
    if config_key is not None:
        cfg = cfg[config_key]
    registry = ModelRegistry.root()
    registry.load_config(cfg, overwrite=overwrite)
    return registry


def workflow_config(
    workflow: str,
    *,
    model_values: Mapping[str, Any] | None = None,
    overrides: Sequence[str] | None = None,
    config_dir: str | None = None,
    version_base: str | None = None,
) -> ConfigLoadResult:
    result = _load_base_config(
        (f"workflow={workflow}", *(overrides or ())),
        config_dir=config_dir,
        version_base=version_base,
    )
    for key, value in (model_values or {}).items():
        OmegaConf.update(result.cfg, f"model.{key}", _config_value(value), merge=False, force_add=True)
    return result


def run_workflow_config(
    workflow: str,
    *,
    model_values: Mapping[str, Any] | None = None,
    overrides: Sequence[str] | None = None,
    config_dir: str | None = None,
    version_base: str | None = None,
):
    return cfg_run(
        workflow_config(
            workflow,
            model_values=model_values,
            overrides=overrides,
            config_dir=config_dir,
            version_base=version_base,
        ).cfg
    )


def _load_base_config(
    overrides: Sequence[str] | None = None,
    *,
    config_dir: str | None = None,
    version_base: str | None = None,
) -> ConfigLoadResult:
    parent_dir = str(Path(__file__).resolve().parent)
    return base_load_config(
        root_config_dir=parent_dir,
        root_config_name="base",
        config_dir=config_dir,
        overrides=list(overrides or ()),
        version_base=version_base,
        basepath=parent_dir,
        debug=False,
    )


def _config_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return [_config_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _config_value(item) for key, item in value.items()}
    return value
