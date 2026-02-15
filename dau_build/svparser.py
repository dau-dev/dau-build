from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from amaranth import Instance
from amaranth.lib.wiring import Component, In, Out
from pydantic import BaseModel, Field, model_validator
from pyslang import (
    ContinuousAssignSyntax,
    DataDeclarationSyntax,
    HierarchyInstantiationSyntax,
    IdentifierNameSyntax,
    ImplicitAnsiPortSyntax,
    ImplicitTypeSyntax,
    InterfacePortHeaderSyntax,
    ModportDeclarationSyntax,
    ModportNamedPortSyntax,
    ModportSimplePortListSyntax,
    NamedPortConnectionSyntax,
    OrderedPortConnectionSyntax,
    ProceduralBlockSyntax,
    ScopedNameSyntax,
    SyntaxKind,
    SyntaxNode,
    SyntaxTree,
    TokenKind,
    WildcardPortConnectionSyntax,
)
from typing_extensions import Self

__all__ = (
    "Keyword",
    "Size",
    "Dimensions",
    "Port",
    "Input",
    "Output",
    "Inout",
    "Parameter",
    "Wire",
    "ContinuousAssignment",
    "ProceduralBlock",
    "GenerateBlock",
    "Module",
    "Interface",
    "Design",
)


Keyword = Literal["bit", "wire", "logic", "reg"]


class Size(BaseModel):
    width: int


class Dimensions(BaseModel):
    dimensions: list[int] = Field(default_factory=list)

    @property
    def unresolved(self) -> bool:
        return len(self.dimensions) > 0

    def __str__(self):
        if len(self.dimensions) == 1:
            return f"{self.dimensions[0]}'b"
        elif len(self.dimensions) == 2:
            return f"[{self.dimensions[0]}: {self.dimensions[1]}]"
        else:
            # TODO
            return "?"

    def size(self) -> int:
        if len(self.dimensions) == 1:
            return self.dimensions[0]
        elif len(self.dimensions) == 2:
            return self.dimensions[0] - self.dimensions[1] + 1
        else:
            # TODO
            return 0

    def to_sv(self) -> str:
        """Return SV range syntax e.g. [7:0]."""
        if len(self.dimensions) == 2:
            return f"[{self.dimensions[0]}:{self.dimensions[1]}]"
        elif len(self.dimensions) == 1 and self.dimensions[0] > 1:
            return f"[{self.dimensions[0] - 1}:0]"
        return ""


class _Base(BaseModel):
    name: str
    instance_name: Optional[str] = Field(default="")
    node: object | None = Field(default=None)

    def to_string(self, indent: str = ""):
        return f"{self.__class__.__name__}({self.name})"

    def __str__(self):
        return self.to_string()

    def __repr__(self):
        return self.__str__()

    def _amaranth(self):
        raise NotImplementedError()


class Modport(_Base):
    inputs: list[Input] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    modports: list["Modport"] = Field(default_factory=list)

    def to_string(self, indent: str = ""):
        ret = f"{indent}{self.__class__.__name__}({self.name})"
        for input in self.inputs:
            ret += f"\n{indent}\t{input}"
        for output in self.outputs:
            ret += f"\n{indent}\t{output}"
        for modport in self.modports:
            modport_str = modport.to_string(indent=indent + "\t")
            ret += f"\n{modport_str}"
        return ret


class Port(_Base):
    keyword: Keyword = Field(default="logic")
    dimensions: Dimensions = Field(default_factory=Dimensions)

    def __str__(self):
        return f"{self.__class__.__name__}({self.keyword} {self.dimensions} {self.name})"


class Input(Port):
    @model_validator(mode="after")
    def _set_amaranth(self) -> Self:
        self.__amaranth__ = In(self.dimensions.size())
        return self


class Output(Port):
    @model_validator(mode="after")
    def _set_amaranth(self) -> Self:
        self.__amaranth__ = Out(self.dimensions.size())
        return self


class Inout(Port):
    """Bidirectional port."""

    pass


class Wire(BaseModel):
    """Internal wire/reg/logic/bit declaration within a module body."""

    name: str
    keyword: str = "logic"
    dimensions: Dimensions = Field(default_factory=Dimensions)
    unpacked_dimensions: str = ""
    body: str = ""

    def __str__(self):
        return f"Wire({self.keyword} {self.dimensions} {self.name})"


class ContinuousAssignment(BaseModel):
    """A continuous assign statement."""

    lhs: str
    rhs: str
    body: str = ""

    def __str__(self):
        return f"assign {self.lhs} = {self.rhs}"


class ProceduralBlock(BaseModel):
    """A procedural block (always_comb, always_ff, always_latch, always, initial, final)."""

    kind: str  # "always_comb", "always_ff", "always_latch", "always", "initial", "final"
    sensitivity: str = ""
    body: str = ""

    def __str__(self):
        if self.sensitivity:
            return f"{self.kind} {self.sensitivity} begin...end"
        return f"{self.kind} begin...end"


class GenerateBlock(BaseModel):
    """A generate construct (region, for-loop, if, case)."""

    kind: str = "region"
    body: str = ""
    modules: list["Module"] = Field(default_factory=list)

    def __str__(self):
        return f"Generate({self.kind}, {len(self.modules)} submodules)"


class Link(_Base):
    # for point-to-point links
    # like: submod_in(my_local_out)
    input: str = Field(default="")
    output: str = Field(default="")

    # for positional links
    connection: str = Field(default="")
    position: int = Field(default=-1)

    # for modport links
    modport: Modport | None = Field(default=None)
    # TODO: add validation, either input+output or modport


class Parameter(_Base):
    value: int

    def __str__(self):
        return f"{self.__class__.__name__}({self.name}={self.value})"


class Module(_Base):
    parameters: list[Parameter] = Field(default_factory=list)
    inputs: list[Input] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    inouts: list[Inout] = Field(default_factory=list)

    modports: list[Modport] = Field(default_factory=list, description="Modport inputs/outputs")
    modules: list["Module"] = Field(default_factory=list, description="Sub module instantiations")

    links: list[Link] = Field(default_factory=list)

    # Extended structure fields
    wires: list[Wire] = Field(default_factory=list, description="Internal signal declarations")
    assigns: list[ContinuousAssignment] = Field(default_factory=list, description="Continuous assignments")
    always_comb_blocks: list[ProceduralBlock] = Field(default_factory=list)
    always_ff_blocks: list[ProceduralBlock] = Field(default_factory=list)
    always_latch_blocks: list[ProceduralBlock] = Field(default_factory=list)
    always_blocks: list[ProceduralBlock] = Field(default_factory=list)
    initial_blocks: list[ProceduralBlock] = Field(default_factory=list)
    final_blocks: list[ProceduralBlock] = Field(default_factory=list)
    generate_blocks: list[GenerateBlock] = Field(default_factory=list, description="Generate constructs")

    source_path: Optional[Path] = Field(default=None, description="Path to source SV file")

    def instance(self) -> Instance:
        """Return an Amaranth `Instance` type correctly specified for the underlying systemverilog code

        See: https://amaranth-lang.org/docs/amaranth/latest/guide.html#instances
        """
        # TODO pass in inputs,outputs and bind
        # the below isnt correct
        return Instance(
            self.name,
            # attributes
            # parameters
            # inputs
            *(("i", i.name, i.__amaranth__) for i in self.inputs),
            # outputs
            *(("o", o.name, o.__amaranth__) for o in self.outputs),
            # inouts
        )

    @classmethod
    def from_module(cls, name: str, *, root: Path = Path("."), extension: str = "sv") -> Module:
        # TODO: override root, extension
        file = root / f"{name}.{extension}"
        file.resolve()
        if not file.exists():
            raise FileNotFoundError(file)
        return cls.from_file(file)

    @classmethod
    def from_file(cls, path: Path) -> Module:
        st = path.read_text()
        mod = cls.from_str(st)
        mod.source_path = path
        return mod

    @classmethod
    def from_str(cls, st: str) -> Module:
        tree = SyntaxTree.fromText(st)
        if tree.root.kind == SyntaxKind.InterfaceDeclaration:
            return Interface(name=tree.root.header.name.value, node=tree.root)
        return Module(name=tree.root.header.name.value, node=tree.root)

    def to_string(self, indent=""):
        ret = f"\n{indent}{self.__class__.__name__}({self.name})"
        for param in self.parameters:
            ret += f"\n{indent}\t{param}"
        for input in self.inputs:
            ret += f"\n{indent}\t{input}"
        for output in self.outputs:
            ret += f"\n{indent}\t{output}"
        for inout in self.inouts:
            ret += f"\n{indent}\tInout({inout.name})"
        for wire in self.wires:
            ret += f"\n{indent}\t{wire}"
        for assign in self.assigns:
            ret += f"\n{indent}\t{assign}"
        for block in self.always_comb_blocks:
            ret += f"\n{indent}\t{block}"
        for block in self.always_ff_blocks:
            ret += f"\n{indent}\t{block}"
        for block in self.always_latch_blocks:
            ret += f"\n{indent}\t{block}"
        for block in self.always_blocks:
            ret += f"\n{indent}\t{block}"
        for block in self.initial_blocks:
            ret += f"\n{indent}\t{block}"
        for block in self.final_blocks:
            ret += f"\n{indent}\t{block}"
        for gen in self.generate_blocks:
            ret += f"\n{indent}\t{gen}"
        for modport in self.modports:
            modport_str = modport.to_string(indent=indent + "\t")
            ret += f"\n{modport_str}"
        for module in self.modules:
            module_str = module.to_string(indent=indent + "\t")
            ret += f"\n{module_str}"
        return ret

    def __str__(self):
        return self.to_string()

    def __repr__(self):
        return self.__str__()

    def resolve(self, root: Path = Path(".")) -> Module:
        for i, module in enumerate(self.modules):
            self.modules[i] = Module.from_module(module.name, root=root).resolve(root=root)
        return self

    @model_validator(mode="after")
    def _parse_structure(self) -> Self:
        # Skip parsing if not parseable
        if not self.node:
            return self

        self._parse_params()
        self._parse_ports()
        self._parse_modules()
        self._parse_modports()
        self._parse_wires()
        self._parse_assigns()
        self._parse_procedural_blocks()
        self._parse_generates()

        self.__amaranth__ = type(self.name, (Component,), {})
        for input in self.inputs:
            self.__amaranth__.__annotations__[input.name] = input.__amaranth__
        for output in self.outputs:
            self.__amaranth__.__annotations__[output.name] = output.__amaranth__
        return self

    def _parse_params(self):
        for paramlist in self.node.header.parameters or []:
            if isinstance(paramlist, SyntaxNode):
                for param in paramlist:
                    if param.kind == TokenKind.Comma:
                        # Comma
                        continue
                    for declaration in param.declarators:
                        self.parameters.append(
                            Parameter(
                                name=declaration.name.value,
                                value=declaration.initializer[1].literal.value,
                                node=declaration,
                            )
                        )

    def _eval_dim_expr(self, expr) -> int:
        """Evaluate a dimension expression using known parameters."""
        return int(eval(str(expr), {p.name: p.value for p in self.parameters}))

    def _parse_dimensions(self, type_node) -> list[int]:
        """Parse packed dimensions from a type node."""
        if type_node.dimensions:
            if len(type_node.dimensions) == 1:
                left = type_node.dimensions[0].specifier[0][0]
                right = type_node.dimensions[0].specifier[0][2]
                return [self._eval_dim_expr(left), self._eval_dim_expr(right)]
            else:
                # TODO: multi-dimensional
                return [1]
        return [1]

    @staticmethod
    def _keyword_from_kind(kind) -> str:
        """Map a SyntaxKind type to a keyword string."""
        mapping = {
            SyntaxKind.LogicType: "logic",
            SyntaxKind.RegType: "reg",
            SyntaxKind.BitType: "bit",
        }
        return mapping.get(kind, "wire")

    def _parse_ports(self):
        if len(self.node.header.ports) == 3:
            for port in self.node.header.ports[1]:
                if isinstance(port, ImplicitAnsiPortSyntax):
                    if isinstance(port.header, InterfacePortHeaderSyntax):
                        # TODO
                        raise NotImplementedError("Modports coming soon")
                    else:
                        direction = port.header.direction.valueText
                        if isinstance(port.header.dataType, ImplicitTypeSyntax):
                            keyword = "bit"
                        else:
                            keyword = port.header.dataType.keyword.valueText
                        declarator = port.declarator.name.value
                        dimensions = self._parse_dimensions(port.header.dataType)
                        if direction == "input":
                            self.inputs.append(
                                Input(
                                    name=declarator,
                                    keyword=keyword,
                                    dimensions=Dimensions(dimensions=dimensions),
                                    node=port,
                                )
                            )
                        elif direction == "output":
                            self.outputs.append(
                                Output(
                                    name=declarator,
                                    keyword=keyword,
                                    dimensions=Dimensions(dimensions=dimensions),
                                    node=port,
                                )
                            )
                        elif direction == "inout":
                            self.inouts.append(
                                Inout(
                                    name=declarator,
                                    keyword=keyword,
                                    dimensions=Dimensions(dimensions=dimensions),
                                    node=port,
                                )
                            )
                        else:
                            # TODO: ref ports, etc.
                            assert False
                elif port.kind == TokenKind.Comma:
                    continue
                else:
                    assert False
        else:
            # TODO
            assert False

    def _parse_modules(self):
        for member in self.node.members:
            if isinstance(member, HierarchyInstantiationSyntax):
                self._parse_hierarchy_instantiation(member, self.modules)

    def _parse_hierarchy_instantiation(self, member, target_list):
        """Parse a single HierarchyInstantiationSyntax into a Module and add to target_list."""
        instance_name = member.instances[0].decl.name.value
        module_type = member.type.value

        mod = Module(instance_name=instance_name, name=module_type)

        for i, connection in enumerate(member.instances[0].connections):
            if isinstance(connection, (OrderedPortConnectionSyntax, NamedPortConnectionSyntax)):
                if isinstance(connection.expr[0][0], ScopedNameSyntax):
                    val = connection.expr[0][0].__str__().strip()
                    link = Link(name=val, connection=val, position=i)
                    mod.links.append(link)
                elif isinstance(connection.expr[0][0], IdentifierNameSyntax):
                    val = connection.expr[0][0].identifier.value
                    link = Link(name=val, connection=val, position=i)
                    mod.links.append(link)
                else:
                    # TODO
                    raise NotImplementedError
            elif isinstance(connection, WildcardPortConnectionSyntax):
                # connection like conn(.*)
                # TODO
                raise NotImplementedError
        target_list.append(mod)

    def _parse_wires(self):
        """Parse internal wire/reg/logic/bit declarations from module body."""
        for member in self.node.members:
            if isinstance(member, DataDeclarationSyntax):
                if member.type.kind == SyntaxKind.NamedType:
                    continue
                keyword = self._keyword_from_kind(member.type.kind)
                dimensions = self._parse_dimensions(member.type)
                for decl in member.declarators:
                    name = decl.name.value
                    unpacked = ""
                    if decl.dimensions:
                        unpacked = str(decl.dimensions).strip()
                    self.wires.append(
                        Wire(
                            name=name,
                            keyword=keyword,
                            dimensions=Dimensions(dimensions=dimensions),
                            unpacked_dimensions=unpacked,
                            body=str(member).strip(),
                        )
                    )
            elif hasattr(member, "kind") and member.kind == SyntaxKind.NetDeclaration:
                keyword = "wire"
                try:
                    dimensions = self._parse_dimensions(member.type)
                except Exception:
                    dimensions = [1]
                for decl in member.declarators:
                    name = decl.name.value
                    unpacked = ""
                    if decl.dimensions:
                        unpacked = str(decl.dimensions).strip()
                    self.wires.append(
                        Wire(
                            name=name,
                            keyword=keyword,
                            dimensions=Dimensions(dimensions=dimensions),
                            unpacked_dimensions=unpacked,
                            body=str(member).strip(),
                        )
                    )

    def _parse_assigns(self):
        """Parse continuous assignment statements."""
        for member in self.node.members:
            if isinstance(member, ContinuousAssignSyntax):
                for assignment in member.assignments:
                    lhs = str(assignment.left).strip()
                    rhs = str(assignment.right).strip()
                    self.assigns.append(
                        ContinuousAssignment(
                            lhs=lhs,
                            rhs=rhs,
                            body=str(member).strip(),
                        )
                    )

    def _parse_procedural_blocks(self):
        """Parse procedural blocks (always_comb, always_ff, always_latch, always, initial, final)."""
        kind_map = {
            SyntaxKind.AlwaysCombBlock: "always_comb",
            SyntaxKind.AlwaysFFBlock: "always_ff",
            SyntaxKind.AlwaysLatchBlock: "always_latch",
            SyntaxKind.AlwaysBlock: "always",
            SyntaxKind.InitialBlock: "initial",
            SyntaxKind.FinalBlock: "final",
        }
        for member in self.node.members:
            if isinstance(member, ProceduralBlockSyntax):
                kind = kind_map.get(member.kind, str(member.kind))
                sensitivity = ""
                if member.kind == SyntaxKind.AlwaysFFBlock:
                    if hasattr(member.statement, "timingControl") and member.statement.timingControl:
                        sensitivity = str(member.statement.timingControl).strip()
                body = str(member).strip()
                block = ProceduralBlock(kind=kind, sensitivity=sensitivity, body=body)
                if kind == "always_comb":
                    self.always_comb_blocks.append(block)
                elif kind == "always_ff":
                    self.always_ff_blocks.append(block)
                elif kind == "always_latch":
                    self.always_latch_blocks.append(block)
                elif kind == "always":
                    self.always_blocks.append(block)
                elif kind == "initial":
                    self.initial_blocks.append(block)
                elif kind == "final":
                    self.final_blocks.append(block)

    def _parse_generates(self):
        """Parse generate constructs."""
        for member in self.node.members:
            if hasattr(member, "kind") and member.kind == SyntaxKind.GenerateRegion:
                gen = GenerateBlock(kind="region", body=str(member).strip())
                self._find_instantiations_in_generate(member, gen)
                self.generate_blocks.append(gen)
            elif hasattr(member, "kind") and member.kind == SyntaxKind.LoopGenerate:
                gen = GenerateBlock(kind="for", body=str(member).strip())
                self._find_instantiations_in_generate(member, gen)
                self.generate_blocks.append(gen)

    def _find_instantiations_in_generate(self, node, gen_block):
        """Recursively find HierarchyInstantiationSyntax inside generate constructs."""
        if isinstance(node, HierarchyInstantiationSyntax):
            instance_name = node.instances[0].decl.name.value
            module_type = node.type.value
            mod = Module(instance_name=instance_name, name=module_type)
            for i, connection in enumerate(node.instances[0].connections):
                if isinstance(connection, (OrderedPortConnectionSyntax, NamedPortConnectionSyntax)):
                    try:
                        if isinstance(connection.expr[0][0], ScopedNameSyntax):
                            val = str(connection.expr[0][0]).strip()
                        elif isinstance(connection.expr[0][0], IdentifierNameSyntax):
                            val = connection.expr[0][0].identifier.value
                        else:
                            val = str(connection.expr[0][0]).strip()
                        link = Link(name=val, connection=val, position=i)
                        mod.links.append(link)
                    except Exception:
                        pass
            gen_block.modules.append(mod)
            return
        try:
            for child in node:
                if isinstance(child, SyntaxNode):
                    self._find_instantiations_in_generate(child, gen_block)
        except TypeError:
            pass

    def _parse_modports(self):
        data_declarations = {}
        for member in self.node.members:
            if isinstance(member, DataDeclarationSyntax):
                name = member.declarators[0].name.value
                if member.type.kind == SyntaxKind.NamedType:
                    continue
                keyword = self._keyword_from_kind(member.type.kind)
                dimensions = self._parse_dimensions(member.type)
                data_declarations[name] = (keyword, Dimensions(dimensions=dimensions))

        for member in self.node.members:
            if isinstance(member, ModportDeclarationSyntax):
                name = member.items[0].name.valueText
                mp = Modport(name=name)
                for in_out in member.items[0].ports[1]:
                    if isinstance(in_out, ModportSimplePortListSyntax):
                        direction = in_out.direction.valueText
                        for port in in_out.ports:
                            if isinstance(port, ModportNamedPortSyntax):
                                port_name = port.name.valueText

                                # try to resolve modport size and type
                                resolved = False

                                # first, check prior data declarations
                                if port_name in data_declarations:
                                    keyword, dimensions = data_declarations[port_name]
                                    resolved = True

                                # if not resolved, look in inputs
                                if not resolved:
                                    for input in self.inputs:
                                        if port_name == input.name:
                                            keyword = input.keyword
                                            dimensions = input.dimensions
                                            resolved = True
                                            break

                                # if still not resolved, look in outputs
                                if not resolved:
                                    for output in self.outputs:
                                        if port_name == output.name:
                                            keyword = output.keyword
                                            dimensions = output.dimensions
                                            resolved = True
                                            break

                                # if still not resolved, give up and just default to
                                # input and empty dimensions
                                if not resolved:
                                    keyword = "input"
                                    dimensions = Dimensions()

                                # now attach input/output to modport
                                if direction == "input":
                                    mp.inputs.append(Input(name=port_name, keyword=keyword, dimensions=dimensions))
                                elif direction == "output":
                                    mp.outputs.append(Output(name=port_name, keyword=keyword, dimensions=dimensions))
                self.modports.append(mp)


class Interface(Module):
    pass


class Design(BaseModel):
    """A collection of parsed SV modules that can be composed into a top-level design."""

    modules: dict[str, Module] = Field(default_factory=dict)

    @classmethod
    def from_directory(cls, path: Path, extension: str = "sv") -> "Design":
        """Parse all SV files in a directory."""
        design = cls()
        for f in sorted(path.glob(f"*.{extension}")):
            try:
                mod = Module.from_file(f)
                design.modules[mod.name] = mod
            except Exception:
                pass
        return design

    @classmethod
    def from_files(cls, paths: list[Path]) -> "Design":
        """Parse specific SV files."""
        design = cls()
        for f in paths:
            try:
                mod = Module.from_file(f)
                design.modules[mod.name] = mod
            except Exception:
                pass
        return design

    def resolve(self) -> "Design":
        """Resolve all submodule references within the design."""
        for name, mod in self.modules.items():
            for i, sub in enumerate(mod.modules):
                if sub.name in self.modules:
                    resolved = self.modules[sub.name]
                    resolved_copy = resolved.model_copy()
                    resolved_copy.instance_name = sub.instance_name
                    resolved_copy.links = sub.links
                    mod.modules[i] = resolved_copy
        return self

    def generate_top_sv(
        self,
        name: str = "top",
        module_names: list[str] | None = None,
        clk: str = "clk",
        reset: str = "reset",
    ) -> str:
        """Generate a top-level SV module that instantiates and wires together the specified modules.

        Args:
            name: Name for the generated top module
            module_names: List of module names to include (None = all non-interface/testbench modules)
            clk: Clock signal name
            reset: Reset signal name

        Returns:
            Generated SystemVerilog source code as a string
        """
        if module_names is None:
            module_names = [n for n in self.modules if not isinstance(self.modules[n], Interface)]
        selected = {n: self.modules[n] for n in module_names if n in self.modules}

        lines = ["`timescale 1ns/1ns", "", f"module {name} ("]

        port_lines = []
        port_lines.append(f"  input bit {clk}")
        port_lines.append(f"  input bit {reset}")

        for mod_name, mod in selected.items():
            for inp in mod.inputs:
                if inp.name in (clk, reset):
                    continue
                dim_str = inp.dimensions.to_sv()
                space = f" {dim_str} " if dim_str else " "
                port_lines.append(f"  input {inp.keyword}{space}{mod_name}_{inp.name}")
            for out in mod.outputs:
                dim_str = out.dimensions.to_sv()
                space = f" {dim_str} " if dim_str else " "
                port_lines.append(f"  output {out.keyword}{space}{mod_name}_{out.name}")

        lines.append(",\n".join(port_lines))
        lines.append(");")
        lines.append("")

        for mod_name, mod in selected.items():
            params_str = ""
            if mod.parameters:
                param_parts = [f".{p.name}({p.value})" for p in mod.parameters]
                params_str = f" #({', '.join(param_parts)})"
            lines.append(f"  {mod_name}{params_str} {mod_name}_inst (")
            conn_lines = []
            for inp in mod.inputs:
                if inp.name in (clk, reset):
                    conn_lines.append(f"    .{inp.name}({inp.name})")
                else:
                    conn_lines.append(f"    .{inp.name}({mod_name}_{inp.name})")
            for out in mod.outputs:
                conn_lines.append(f"    .{out.name}({mod_name}_{out.name})")
            lines.append(",\n".join(conn_lines))
            lines.append("  );")
            lines.append("")

        lines.append("endmodule")
        lines.append("")
        return "\n".join(lines)

    def __str__(self):
        parts = [f"Design({len(self.modules)} modules):"]
        for name, mod in self.modules.items():
            parts.append(f"  {mod.to_string('  ')}")
        return "\n".join(parts)
