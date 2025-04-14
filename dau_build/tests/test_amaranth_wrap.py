from .example_verilog import Chaser

def test_amaranth_wrap():
    from amaranth_boards.nitefury import NitefuryIIPlatform

    platform = NitefuryIIPlatform()
    chaser = Chaser()
    platform.build(chaser, do_build=True, do_program=False)
