from typing import Any, Dict, List, Optional

from hydra.core.object_type import ObjectType
from hydra.plugins.config_source import ConfigLoadError, ConfigResult, ConfigSource
from omegaconf import OmegaConf


class DauSVConfigSourceExample(ConfigSource):
    def __init__(self, provider: str, path: str):
        super().__init__(provider=provider, path=path)
        self.headers: Dict[str, Dict[str, str]] = {
            "package_test/explicit.yaml": {"package": "a.b"},
            "package_test/global.yaml": {"package": "_global_"},
            "package_test/group.yaml": {"package": "_group_"},
            "package_test/group_name.yaml": {"package": "foo._group_._name_"},
            "package_test/name.yaml": {"package": "_name_"},
            "package_test/none.yaml": {},
            "primary_config_with_non_global_package.yaml": {"package": "foo"},
            "configs_with_defaults_list.yaml": {"package": "_global_"},
            "configs_with_defaults_list/global_package.yaml": {"package": "_global_"},
            "configs_with_defaults_list/group_package.yaml": {"package": "_group_"},
        }
        self.configs: Dict[str, Dict[str, Any]] = {
            "primary_config.yaml": {"primary": True},
            "primary_config_with_non_global_package.yaml": {"primary": True},
            "config_without_group.yaml": {"group": False},
            "config_with_unicode.yaml": {"group": "数据库"},
            "dataset/imagenet.yaml": {"name": "imagenet", "path": "/datasets/imagenet"},
            "dataset/cifar10.yaml": {"name": "cifar10", "path": "/datasets/cifar10"},
            "level1/level2/nested1.yaml": {"l1_l2_n1": True},
            "level1/level2/nested2.yaml": {"l1_l2_n2": True},
            "package_test/explicit.yaml": {"foo": "bar"},
            "package_test/global.yaml": {"foo": "bar"},
            "package_test/group.yaml": {"foo": "bar"},
            "package_test/group_name.yaml": {"foo": "bar"},
            "package_test/name.yaml": {"foo": "bar"},
            "package_test/none.yaml": {"foo": "bar"},
            "config_with_defaults_list.yaml": {
                "defaults": [{"dataset": "imagenet"}],
                "key": "value",
            },
            "configs_with_defaults_list/global_package.yaml": {
                "defaults": [{"foo": "bar"}],
                "x": 10,
            },
            "configs_with_defaults_list/group_package.yaml": {
                "defaults": [{"foo": "bar"}],
                "x": 10,
            },
        }

    @staticmethod
    def scheme() -> str:
        return "sv"

    def load_config(
        self, config_path: str, package_override: Optional[str] = None
    ) -> ConfigResult:
        name = self._normalize_file_name(config_path)

        if name not in self.configs:
            raise ConfigLoadError("Config not found : " + config_path)

        res_header: Dict[str, Optional[str]] = {"package": None}
        if name in self.headers:
            header = self.headers[name]
            res_header["package"] = header["package"] if "package" in header else None

        cfg = OmegaConf.create(self.configs[name])
        return ConfigResult(
            config=cfg,
            path=f"{self.scheme()}://{self.path}",
            provider=self.provider,
            header=res_header,
        )

    def available(self) -> bool:
        """
        :return: True is this config source is pointing to a valid location
        """
        return self.path == "valid_path"

    def is_group(self, config_path: str) -> bool:
        groups = {
            "",
            "dataset",
            "optimizer",
            "level1",
            "level1/level2",
            "configs_with_defaults_list",
        }
        return config_path in groups

    def is_config(self, config_path: str) -> bool:
        base = {
            "dataset",
            "dataset/imagenet",
            "level1/level2/nested1",
            "level1/level2/nested2",
            "configs_with_defaults_list/global_package",
            "configs_with_defaults_list/group_package",
        }
        configs = set([x for x in base] + [f"{x}.yaml" for x in base])
        return config_path in configs

    def list(self, config_path: str, results_filter: Optional[ObjectType]) -> List[str]:
        groups: Dict[str, List[str]] = {
            "": ["dataset", "level1", "optimizer"],
            "dataset": [],
            "optimizer": [],
            "level1": ["level2"],
            "level1/level2": [],
        }
        configs: Dict[str, List[str]] = {
            "": ["config_without_group", "dataset"],
            "dataset": ["cifar10", "imagenet"],
            "optimizer": ["adam", "nesterov"],
            "level1": [],
            "level1/level2": ["nested1", "nested2"],
            "configs_with_defaults_list": ["global_package", "group_package"],
        }
        if results_filter is None:
            return sorted(set(groups[config_path] + configs[config_path]))
        elif results_filter == ObjectType.GROUP:
            return groups[config_path]
        elif results_filter == ObjectType.CONFIG:
            return configs[config_path]
        else:
            raise ValueError()