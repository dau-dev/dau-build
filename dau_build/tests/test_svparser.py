from pathlib import Path

import pyslang
import pytest


class TestParser:
    @pytest.mark.parametrize(
        ("file", "parameters", "inputs", "outputs", "submodules", "submodports"),
        [
            ("cam_ifc.sv", 2, 1, 0, 0, 2),
            ("cam_tb.sv", 2, 5, 8, 0, 0),
            # ("cam_tb_modport.sv", 0, 0, 0, 0, 0),  # TODO
            ("cam_top.sv", 0, 1, 0, 3, 0),
            ("cam.sv", 2, 9, 4, 5, 0),
            ("ceff.sv", 1, 4, 2, 0, 0),
            ("decoder.sv", 1, 2, 1, 0, 0),
            ("equality_checker.sv", 2, 3, 1, 0, 0),
            ("ff.sv", 1, 4, 2, 0, 0),
            ("mux.sv", 2, 2, 1, 0, 0),
            ("priorityencoder.sv", 1, 1, 2, 0, 0),
            ("register_.sv", 1, 4, 2, 1, 0),
        ],
    )
    def test_parse(self, file, parameters, inputs, outputs, submodules, submodports):
        from dau_build import Module

        fl = (Path(__file__).parent / ".." / "sv" / file).resolve()
        st = pyslang.SyntaxTree.fromText(fl.read_text())
        mod = Module(name=st.root.header.name.value, node=st.root)
        print(mod)
        assert len(mod.parameters) == parameters
        assert len(mod.inputs) == inputs
        assert len(mod.outputs) == outputs
        assert len(mod.submodules) == submodules
        assert len(mod.submodports) == submodports
        assert all(len(sm.links) > 0 for sm in mod.submodules)
        assert all((len(smp.inputs) + len(smp.outputs)) > 0 for smp in mod.submodports)

    @pytest.mark.parametrize(("file",), [("ff.sv",)])
    def test_parse_amaranth(self, file):
        from dau_build import Module

        fl = (Path(__file__).parent / ".." / "sv" / file).resolve()
        st = pyslang.SyntaxTree.fromText(fl.read_text())
        mod = Module(name=st.root.header.name.value, node=st.root)
        print(mod)
        for input in mod.inputs:
            print(input.__amaranth__)
        for output in mod.outputs:
            print(output.__amaranth__)
        print(mod.__amaranth__)
