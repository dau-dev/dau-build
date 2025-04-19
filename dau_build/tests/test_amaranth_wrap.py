from shutil import which

import pytest

from .example_verilog import Chaser


@pytest.mark.skipif(which("vivado") is None, reason="vivado not found")
def test_amaranth_wrap():
    from amaranth_boards.nitefury import NitefuryIIPlatform

    platform = NitefuryIIPlatform()
    chaser = Chaser()
    platform.build(chaser, do_build=True, do_program=False)
