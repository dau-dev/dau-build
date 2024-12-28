from hydra.core.plugins import Plugins
from hydra.plugins.config_source import ConfigSource
from hydra.test_utils.config_source_common_tests import ConfigSourceTestSuite
from pytest import mark

from hydra_plugins.dau import DauSVConfigSourceExample


@mark.parametrize("type_, path", [(DauSVConfigSourceExample, "sv://valid_path")])
class TestCoreConfigSources(ConfigSourceTestSuite):
    pass


def test_discovery() -> None:
    # Test that this config source is discoverable when looking at config sources
    assert DauSVConfigSourceExample.__name__ in [x.__name__ for x in Plugins.instance().discover(ConfigSource)]
