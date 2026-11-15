from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ccflow import ModelRegistry
from ccflow.utils.hydra import ConfigLoadResult, cfg_run, load_config as base_load_config
from omegaconf import OmegaConf

__all__ = ("CONTRIB_GROUP", "compose_config", "contributed_options", "load_config", "request_config", "resolve_platform", "run_request_config")

# where a package or a searchpath directory contributes tiles (dau-core's CONTRIB_GROUP)
CONTRIB_GROUP = "dau-core-contrib"


def contributed_options(overrides: Sequence[str] = (), *, config_dir: str | None = None) -> tuple[str, ...]:
    """The ``dau-core-contrib`` options on the searchpath a composition with
    these overrides and ``config_dir`` would see: installed packages'
    ``hydra.lernaplugins`` entry points, a ``--config-dir`` overlay, and any
    ``hydra.searchpath=[...]`` override. Discovered, so a tile registered
    from any of them composes without being named."""
    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    searchpath = [override for override in overrides if override.lstrip("+").partition("=")[0] == "hydra.searchpath"][-1:]
    if config_dir:
        entry = f"file://{Path(config_dir).resolve()}"
        given = searchpath[0].partition("=")[2].strip().removeprefix("[").removesuffix("]") if searchpath else ""
        searchpath = [f"hydra.searchpath=[{entry}{',' + given if given else ''}]"]
    instance = GlobalHydra.instance()
    if instance.is_initialized():
        return tuple(sorted(instance.config_loader().get_group_options(CONTRIB_GROUP, overrides=searchpath)))
    # initializing hydra runs the searchpath plugin, which can load provider
    # configs into the root registry; asking what is installed must not
    # change what is installed
    root = ModelRegistry.root()
    before = set(root.models)
    try:
        with initialize_config_dir(config_dir=str(Path(__file__).resolve().parent), version_base=None):
            return tuple(sorted(GlobalHydra.instance().config_loader().get_group_options(CONTRIB_GROUP, overrides=searchpath)))
    finally:
        for name in set(root.models) - before:
            root.remove(name)


def resolve_platform(name: str, *, config_dir: str | None = None, version_base: str | None = None):
    """Resolve a platform from the ``platform`` hydra config group to a
    ``dau_build.platforms.PlatformDefinition`` (user ``--config-dir``
    overlays may add boards -- dau-build packages only the fictional
    ``platforms/example/probe``). ``resolve_platform("platforms/example/probe")``
    returns the reconciled example platform."""
    from hydra.utils import instantiate

    result = _load_base_config((f"platform={name}",), config_dir=config_dir, version_base=version_base)
    if "platform" not in result.cfg or result.cfg.platform is None:
        raise KeyError(f"unknown platform {name!r}")
    return instantiate(result.cfg.platform)


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


def request_config(
    request_kind: str,
    request_name: str,
    *,
    overrides: Sequence[str] | None = None,
    config_dir: str | None = None,
    version_base: str | None = None,
) -> ConfigLoadResult:
    """Compose ``<request_kind>=<request_name>`` with Hydra overrides. Task
    fields are set the Hydra way (``model.<field>=<value>``, see
    ``model_overrides``), so a request composed here is the same request a
    user types on the command line."""
    return _load_base_config(
        (f"{request_kind}={request_name}", *(overrides or ())),
        config_dir=config_dir,
        version_base=version_base,
    )


def model_overrides(values: Mapping[str, Any]) -> list[str]:
    """``model.<field>=<value>`` overrides for task fields, in Hydra's
    override grammar: strings and paths quoted, ``None`` as ``null``,
    mappings as ``{key:value,...}``, sequences as ``[...]``."""
    return [f"model.{key}={_override_value(value)}" for key, value in values.items()]


def _override_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, Path | str):
        text = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{text}'"
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{key}:{_override_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, tuple | list):
        return "[" + ",".join(_override_value(item) for item in value) + "]"
    raise TypeError(f"cannot express {type(value).__name__} as a Hydra override")


def run_request_config(
    request_kind: str,
    request_name: str,
    *,
    overrides: Sequence[str] | None = None,
    config_dir: str | None = None,
    version_base: str | None = None,
):
    return cfg_run(request_config(request_kind, request_name, overrides=overrides, config_dir=config_dir, version_base=version_base).cfg)


def compose_config(
    overrides: Sequence[str] | None = None,
    *,
    config_dir: str | None = None,
    version_base: str | None = None,
) -> ConfigLoadResult:
    """Compose the dau-build hydra config with raw overrides (group selection,
    field overrides, user --config-dir overlays). The public entry for the
    dau-build CLI surface."""
    return _load_base_config(overrides, config_dir=config_dir, version_base=version_base)


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
        config_dir=config_dir or "",  # ccflow treats an empty directory like an absent one
        overrides=list(overrides or ()),
        version_base=version_base,
        basepath=parent_dir,
        debug=False,
    )
