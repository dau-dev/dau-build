from pathlib import Path

import pytest

from dau_build import Module
from dau_build.svparser import (
    Design,
    Interface,
)

_SV_DIR = (Path(__file__).parent / ".." / "sv").resolve()


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
        mod = Module.from_file((_SV_DIR / file).resolve())
        print(mod.parameters)
        assert len(mod.parameters) == parameters
        assert len(mod.inputs) == inputs
        assert len(mod.outputs) == outputs
        assert len(mod.modules) == submodules
        assert len(mod.modports) == submodports
        assert all(len(sm.links) > 0 for sm in mod.modules)
        assert all((len(smp.inputs) + len(smp.outputs)) > 0 for smp in mod.modports)

    @pytest.mark.parametrize(("file",), [("ff.sv",)])
    def test_parse_amaranth(self, file):
        mod = Module.from_file((_SV_DIR / file).resolve())
        print(mod)
        for input in mod.inputs:
            print(input.__amaranth__)
        for output in mod.outputs:
            print(output.__amaranth__)
        print(mod.__amaranth__)

    def test_resolve_submodules(self):
        fl = _SV_DIR / "cam_top.sv"
        mod = Module.from_file(fl)
        mod.resolve(_SV_DIR)
        print(mod)


class TestWires:
    """Test internal signal / wire declaration parsing."""

    @pytest.mark.parametrize(
        ("file", "wire_count"),
        [
            ("cam.sv", 9),  # logic + wire declarations
            ("ff.sv", 2),  # reg declarations
            ("ceff.sv", 2),  # reg + bit
            ("equality_checker.sv", 1),  # logic out
            ("mux.sv", 1),  # logic out
            ("decoder.sv", 0),  # no body declarations
            ("register_.sv", 0),  # no body declarations
            ("cam_ifc.sv", 12),  # interface body declarations
        ],
    )
    def test_wire_count(self, file, wire_count):
        mod = Module.from_file((_SV_DIR / file).resolve())
        assert len(mod.wires) == wire_count

    def test_wire_attributes(self):
        mod = Module.from_file((_SV_DIR / "cam.sv").resolve())
        wire_names = {w.name for w in mod.wires}
        assert "out_value" in wire_names
        assert "out_index" in wire_names
        assert "found" in wire_names
        assert "cam_o" in wire_names
        assert "cam_found" in wire_names

        # Check a wire with specific keyword
        cam_o = next(w for w in mod.wires if w.name == "cam_o")
        assert cam_o.keyword == "wire"

        out_value = next(w for w in mod.wires if w.name == "out_value")
        assert out_value.keyword == "logic"

    def test_wire_dimensions(self):
        mod = Module.from_file((_SV_DIR / "ff.sv").resolve())
        data_wire = next(w for w in mod.wires if w.name == "data")
        assert data_wire.keyword == "reg"
        assert data_wire.dimensions.size() > 0


class TestAssigns:
    """Test continuous assignment parsing."""

    @pytest.mark.parametrize(
        ("file", "assign_count"),
        [
            ("cam.sv", 4),
            ("ff.sv", 2),
            ("ceff.sv", 2),
            ("equality_checker.sv", 1),
            ("mux.sv", 1),
            ("decoder.sv", 0),
            ("cam_top.sv", 0),
        ],
    )
    def test_assign_count(self, file, assign_count):
        mod = Module.from_file((_SV_DIR / file).resolve())
        assert len(mod.assigns) == assign_count

    def test_assign_lhs_rhs(self):
        mod = Module.from_file((_SV_DIR / "ff.sv").resolve())
        lhs_set = {a.lhs for a in mod.assigns}
        assert "data_o" in lhs_set
        assert "valid_o" in lhs_set
        rhs_set = {a.rhs for a in mod.assigns}
        assert "data" in rhs_set
        assert "valid" in rhs_set

    def test_complex_assign_rhs(self):
        mod = Module.from_file((_SV_DIR / "cam.sv").resolve())
        read_valid = next(a for a in mod.assigns if a.lhs == "read_valid_o")
        assert "&&" in read_valid.rhs


class TestProceduralBlocks:
    """Test procedural block (always_comb, always_ff, initial, etc.) parsing."""

    @pytest.mark.parametrize(
        ("file", "comb", "ff", "initial"),
        [
            ("cam.sv", 1, 0, 1),
            ("ff.sv", 0, 1, 1),
            ("ceff.sv", 0, 1, 0),
            ("decoder.sv", 1, 0, 0),
            ("equality_checker.sv", 1, 0, 0),
            ("mux.sv", 1, 0, 0),
            ("priorityencoder.sv", 1, 0, 0),
            ("cam_top.sv", 0, 0, 0),
            ("register_.sv", 0, 0, 0),
        ],
    )
    def test_block_counts(self, file, comb, ff, initial):
        mod = Module.from_file((_SV_DIR / file).resolve())
        assert len(mod.always_comb_blocks) == comb
        assert len(mod.always_ff_blocks) == ff
        assert len(mod.initial_blocks) == initial

    def test_always_ff_sensitivity(self):
        mod = Module.from_file((_SV_DIR / "ff.sv").resolve())
        assert len(mod.always_ff_blocks) == 1
        block = mod.always_ff_blocks[0]
        assert block.kind == "always_ff"
        assert "posedge" in block.sensitivity
        assert "clk" in block.sensitivity

    def test_always_comb_body(self):
        mod = Module.from_file((_SV_DIR / "decoder.sv").resolve())
        assert len(mod.always_comb_blocks) == 1
        block = mod.always_comb_blocks[0]
        assert block.kind == "always_comb"
        assert "case" in block.body
        assert "always_comb" in block.body

    def test_initial_block_body(self):
        mod = Module.from_file((_SV_DIR / "cam.sv").resolve())
        assert len(mod.initial_blocks) == 1
        block = mod.initial_blocks[0]
        assert block.kind == "initial"
        assert "$dumpfile" in block.body


class TestGenerateBlocks:
    """Test generate construct parsing."""

    def test_cam_generate(self):
        mod = Module.from_file((_SV_DIR / "cam.sv").resolve())
        assert len(mod.generate_blocks) == 1
        gen = mod.generate_blocks[0]
        assert gen.kind == "region"
        assert len(gen.modules) == 1
        assert gen.modules[0].name == "register_"
        assert "generate" in gen.body

    def test_generate_submodule_links(self):
        mod = Module.from_file((_SV_DIR / "cam.sv").resolve())
        gen = mod.generate_blocks[0]
        sub = gen.modules[0]
        assert len(sub.links) > 0

    def test_no_generate(self):
        mod = Module.from_file((_SV_DIR / "ff.sv").resolve())
        assert len(mod.generate_blocks) == 0


class TestSourcePath:
    """Test source_path tracking."""

    def test_from_file_sets_path(self):
        path = (_SV_DIR / "ff.sv").resolve()
        mod = Module.from_file(path)
        assert mod.source_path == path

    def test_from_str_no_path(self):
        mod = Module.from_str("module foo (input bit clk); endmodule")
        assert mod.source_path is None


class TestInterface:
    """Test Interface (subclass of Module) parsing."""

    def test_is_interface(self):
        mod = Module.from_file((_SV_DIR / "cam_ifc.sv").resolve())
        assert isinstance(mod, Interface)

    def test_interface_wires(self):
        mod = Module.from_file((_SV_DIR / "cam_ifc.sv").resolve())
        assert len(mod.wires) == 12


class TestDesign:
    """Test Design class for multi-module composition."""

    def test_from_directory(self):
        design = Design.from_directory(_SV_DIR)
        assert len(design.modules) == 11  # all .sv files
        assert "cam" in design.modules
        assert "ff" in design.modules
        assert "cam_ifc" in design.modules

    def test_from_files(self):
        design = Design.from_files([_SV_DIR / "ff.sv", _SV_DIR / "decoder.sv"])
        assert len(design.modules) == 2
        assert "ff" in design.modules
        assert "decoder" in design.modules

    def test_resolve(self):
        design = Design.from_directory(_SV_DIR)
        design.resolve()
        cam = design.modules["cam"]
        # After resolve, submodules should have parsed inputs/outputs
        for sub in cam.modules:
            if sub.name in design.modules:
                assert len(sub.inputs) > 0 or len(sub.outputs) > 0

    def test_generate_top_sv(self):
        design = Design.from_files([_SV_DIR / "ff.sv"])
        top = design.generate_top_sv(name="test_top", module_names=["ff"])
        assert "module test_top" in top
        assert "endmodule" in top
        assert "ff #(.SIZE(32)) ff_inst" in top
        assert ".clk(clk)" in top
        assert ".reset(reset)" in top
        assert "input logic [31:0] ff_data_i" in top
        assert "output logic [31:0] ff_data_o" in top

    def test_generate_top_sv_multiple_modules(self):
        design = Design.from_files([_SV_DIR / "ff.sv", _SV_DIR / "decoder.sv"])
        top = design.generate_top_sv(name="multi_top", module_names=["ff", "decoder"])
        assert "module multi_top" in top
        assert "ff #(.SIZE(32)) ff_inst" in top
        assert "decoder #(.SIZE(5)) decoder_inst" in top

    def test_str(self):
        design = Design.from_files([_SV_DIR / "ff.sv"])
        s = str(design)
        assert "Design(1 modules)" in s
