from pathlib import Path

import pyslang


class TestParser:
    def test_ff(self):
        from dau_build import Module

        ff = (Path(__file__).parent / "sv" / "ff.sv").resolve()
        st = pyslang.SyntaxTree.fromText(ff.read_text())
        mod = Module(name=st.root.header.name.value, node=st.root)
        print(mod)
        assert len(mod.parameters) == 1
        assert len(mod.inputs) == 4
        assert len(mod.outputs) == 2

    def test_amaranth(self):
        from dau_build import Module

        ff = (Path(__file__).parent / "sv" / "ff.sv").resolve()
        st = pyslang.SyntaxTree.fromText(ff.read_text())
        mod = Module(name=st.root.header.name.value, node=st.root)
        print(mod)
        for input in mod.inputs:
            print(input.__amaranth__)
        for output in mod.outputs:
            print(output.__amaranth__)
        print(mod.__amaranth__)
