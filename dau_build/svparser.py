from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

from amaranth import Instance
from amaranth.lib.wiring import Component, In, Out
from pydantic import BaseModel, Field, model_validator

try:
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
except ImportError:
    from pyslang.parsing import TokenKind
    from pyslang.syntax import (
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


def _sv_clog2(value) -> int:
    """SystemVerilog $clog2: $clog2(1) == 0, $clog2(2) == 1, $clog2(16) == 4."""
    value = int(value)
    if value <= 1:
        return 0
    return (value - 1).bit_length()


def _translate_sv_expr(text: str) -> str:
    """Translate SV constant-expression constructs into python-evaluatable text."""
    text = text.replace("$clog2", "clog2")
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return _translate_ternaries(text)


def _translate_ternaries(text: str) -> str:
    """Rewrite SV ternaries `a ? b : c` as python `(b) if (a) else (c)`, recursing into parentheses."""
    depth = 0
    question = -1
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "?" and depth == 0:
            question = i
            break
    if question >= 0:
        depth = 0
        pending = 0
        for j in range(question + 1, len(text)):
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "?" and depth == 0:
                pending += 1
            elif ch == ":" and depth == 0:
                if pending == 0:
                    condition = _translate_ternaries(text[:question])
                    if_true = _translate_ternaries(text[question + 1 : j])
                    if_false = _translate_ternaries(text[j + 1 :])
                    return f"(({if_true}) if ({condition}) else ({if_false}))"
                pending -= 1
        return text
    # no top-level ternary: recurse into parenthesized subexpressions
    parts = []
    i = 0
    while i < len(text):
        if text[i] == "(":
            depth = 1
            j = i + 1
            while j < len(text) and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            parts.append("(" + _translate_ternaries(text[i + 1 : j - 1]) + ")")
            i = j
        else:
            parts.append(text[i])
            i += 1
    return "".join(parts)


class Size(BaseModel):
    width: int


class Dimensions(BaseModel):
    dimensions: list[int] = Field(default_factory=list)
    resolved: bool = Field(default=True, description="False when a dimension expression could not be evaluated; size() falls back to 1")

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
        if not self.resolved:
            # unresolvable dimension expression: fall back to a single bit so
            # amaranth In/Out sizing stays legal
            return 1
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

    def to_string(self, indent: str = ""):  # noqa: ARG002 (node to_string interface)
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
        root = tree.root
        if root.kind == SyntaxKind.CompilationUnit:
            # multi-module file: take the last module/interface declaration (the top)
            declarations = [member for member in root.members if member.kind in (SyntaxKind.ModuleDeclaration, SyntaxKind.InterfaceDeclaration)]
            if not declarations:
                raise ValueError("no module or interface declaration found")
            root = declarations[-1]
        if root.kind == SyntaxKind.InterfaceDeclaration:
            return Interface(name=root.header.name.value, node=root)
        return Module(name=root.header.name.value, node=root)

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
        self._parse_localparams()
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
        for param in self._syntax_declaration_items(self.node.header.parameters or []):
            for declaration in param.declarators:
                self.parameters.append(
                    Parameter(
                        name=declaration.name.value,
                        value=declaration.initializer[1].literal.value,
                        node=declaration,
                    )
                )

    @staticmethod
    def _syntax_declaration_items(items):
        for item in items:
            if getattr(item, "kind", None) == TokenKind.Comma:
                continue
            if hasattr(item, "declarators"):
                yield item
                continue
            if not isinstance(item, SyntaxNode):
                continue
            for child in item:
                if getattr(child, "kind", None) == TokenKind.Comma:
                    continue
                if hasattr(child, "declarators"):
                    yield child

    def _eval_dim_expr(self, expr) -> Optional[int]:
        """Evaluate a dimension expression using known parameters and localparams, None if unresolvable."""
        namespace = {"clog2": _sv_clog2}
        namespace.update({p.name: p.value for p in self.parameters})
        namespace.update(getattr(self, "__localparams__", {}))
        try:
            return int(eval(_translate_sv_expr(str(expr)), {"__builtins__": {}}, namespace))
        except Exception:
            return None

    def _parse_localparams(self):
        """Collect body localparam/parameter values for use in dimension expressions.

        Evaluated in declaration order so later localparams can reference earlier
        ones (and header parameters, e.g. `localparam IDXW = $clog2(CAPACITY)`).
        Declarations that cannot be evaluated (non-constant initializers,
        unsupported syntax) are skipped; dimensions depending on them fall back
        to unresolved.
        """
        self.__localparams__ = {}
        for member in self.node.members:
            if getattr(member, "kind", None) != SyntaxKind.ParameterDeclarationStatement:
                continue
            for declarator in getattr(member.parameter, "declarators", []):
                if declarator.initializer is None:
                    continue
                value = self._eval_dim_expr(declarator.initializer[1])
                if value is not None:
                    self.__localparams__[declarator.name.value] = value

    def _parse_dimensions(self, type_node) -> Dimensions:
        """Parse packed dimensions from a type node."""
        if type_node.dimensions:
            if len(type_node.dimensions) == 1:
                left = self._eval_dim_expr(type_node.dimensions[0].specifier[0][0])
                right = self._eval_dim_expr(type_node.dimensions[0].specifier[0][2])
                if left is None or right is None:
                    return Dimensions(resolved=False)
                return Dimensions(dimensions=[left, right])
            else:
                # TODO: multi-dimensional
                return Dimensions(dimensions=[1])
        return Dimensions(dimensions=[1])

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
        port_items = self.node.header.ports
        if len(port_items) == 3 and not any(isinstance(port, ImplicitAnsiPortSyntax) for port in port_items):
            port_items = port_items[1]
        if port_items:
            for port in port_items:
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
                                    dimensions=dimensions,
                                    node=port,
                                )
                            )
                        elif direction == "output":
                            self.outputs.append(
                                Output(
                                    name=declarator,
                                    keyword=keyword,
                                    dimensions=dimensions,
                                    node=port,
                                )
                            )
                        elif direction == "inout":
                            self.inouts.append(
                                Inout(
                                    name=declarator,
                                    keyword=keyword,
                                    dimensions=dimensions,
                                    node=port,
                                )
                            )
                        else:
                            # TODO: ref ports, etc.
                            assert False
                elif port.kind == TokenKind.Comma:
                    continue
                elif port.kind in (TokenKind.OpenParenthesis, TokenKind.CloseParenthesis):
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
                            dimensions=dimensions,
                            unpacked_dimensions=unpacked,
                            body=str(member).strip(),
                        )
                    )
            elif hasattr(member, "kind") and member.kind == SyntaxKind.NetDeclaration:
                keyword = "wire"
                try:
                    dimensions = self._parse_dimensions(member.type)
                except Exception:
                    dimensions = Dimensions(dimensions=[1])
                for decl in member.declarators:
                    name = decl.name.value
                    unpacked = ""
                    if decl.dimensions:
                        unpacked = str(decl.dimensions).strip()
                    self.wires.append(
                        Wire(
                            name=name,
                            keyword=keyword,
                            dimensions=dimensions,
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
                data_declarations[name] = (keyword, dimensions)

        for member in self.node.members:
            if isinstance(member, ModportDeclarationSyntax):
                name = member.items[0].name.valueText
                mp = Modport(name=name)
                port_items = member.items[0].ports
                if len(port_items) == 3 and not any(isinstance(port, ModportSimplePortListSyntax) for port in port_items):
                    port_items = port_items[1]
                for in_out in port_items:
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
            mod = Module.from_file(f)
            design.modules[mod.name] = mod
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

    def generate_dau_top_sv(
        self,
        name: str,
        module_names: list[str],
        *,
        clk: str,
        reset: str,
        register_map_version: int,
        stream_protocol_version: int,
        operator_bitmap: int,
        input_buffer_address: int,
        input_buffer_bytes: int,
        output_buffer_address: int,
        output_buffer_bytes: int,
        result_bytes: int,
    ) -> str:
        selected = {module_name: self.modules[module_name] for module_name in module_names if module_name in self.modules}
        stream_module_name = next((module_name for module_name, module in selected.items() if _is_dau_stream_module(module)), None)

        lines = ["`timescale 1ns/1ns", "", f"module {name} ("]
        port_lines = [
            f"  input wire logic {clk}",
            f"  input wire logic {reset}",
            "  input wire logic register_read_enable",
            "  input wire logic register_write_enable",
            "  input wire logic [15:0] register_address",
            "  input wire logic [31:0] register_write_data",
            "  output logic [31:0] register_read_data",
            "  output logic register_ready",
            "  input wire logic stream_input_valid",
            "  output logic stream_input_ready",
            "  input wire logic [63:0] stream_input_data",
            "  input wire logic stream_input_last",
            "  output logic stream_output_valid",
            "  input wire logic stream_output_ready",
            "  output logic [63:0] stream_output_data",
            "  output logic stream_output_last",
            "  output logic stream_status_valid",
            "  input wire logic stream_status_ready",
            "  output logic stream_status_error",
            "  output logic [7:0] stream_status_error_code",
            "  output logic [63:0] dma_input_address",
            "  output logic [31:0] dma_input_length",
            "  output logic [63:0] dma_output_address",
            "  output logic [31:0] dma_output_length",
            "  output logic dma_start",
            "  input wire logic dma_done",
            "  input wire logic dma_error",
            "  output logic [31:0] capability_magic",
            "  output logic [31:0] capability_register_map_version",
            "  output logic [31:0] capability_stream_protocol_version",
            "  output logic [31:0] capability_operator_bitmap",
        ]

        for module_name, module in selected.items():
            is_stream_module = module_name == stream_module_name
            for input_port in module.inputs:
                if _dau_input_connection(input_port.name, module_name, clk=clk, reset=reset, is_stream_module=is_stream_module) is not None:
                    continue
                port_lines.append(_top_port_declaration("input", module_name, input_port))
            for output_port in module.outputs:
                if _dau_output_connection(output_port.name, module_name, is_stream_module=is_stream_module) is not None:
                    continue
                port_lines.append(_top_port_declaration("output", module_name, output_port))

        lines.append(",\n".join(port_lines))
        lines.append(");")
        lines.append("")
        lines.extend(
            (
                "  localparam logic [31:0] DAU_MAGIC = 32'h44415531;",
                f"  localparam logic [31:0] DAU_REGISTER_MAP_VERSION = 32'h{register_map_version:08x};",
                f"  localparam logic [31:0] DAU_STREAM_PROTOCOL_VERSION = 32'h{stream_protocol_version:08x};",
                f"  localparam logic [31:0] DAU_OPERATOR_BITMAP = 32'h{operator_bitmap:08x};",
                "  localparam logic [15:0] DAU_REGISTER_MAGIC_OFFSET = 16'h0000;",
                "  localparam logic [15:0] DAU_REGISTER_MAP_VERSION_OFFSET = 16'h0004;",
                "  localparam logic [15:0] DAU_STREAM_PROTOCOL_VERSION_OFFSET = 16'h0008;",
                "  localparam logic [15:0] DAU_REGISTER_OPERATOR_BITMAP_OFFSET = 16'h0028;",
                "  localparam logic [15:0] DAU_REGISTER_LAST_ERROR_OFFSET = 16'h002c;",
                "  localparam logic [15:0] DAU_REGISTER_JOB_CONTROL_OFFSET = 16'h0050;",
                "  localparam logic [15:0] DAU_REGISTER_JOB_STATUS_OFFSET = 16'h0054;",
                "  localparam logic [15:0] DAU_REGISTER_INPUT_ADDRESS_LOW_OFFSET = 16'h0058;",
                "  localparam logic [15:0] DAU_REGISTER_INPUT_ADDRESS_HIGH_OFFSET = 16'h005c;",
                "  localparam logic [15:0] DAU_REGISTER_INPUT_LENGTH_LOW_OFFSET = 16'h0060;",
                "  localparam logic [15:0] DAU_REGISTER_INPUT_LENGTH_HIGH_OFFSET = 16'h0064;",
                "  localparam logic [15:0] DAU_REGISTER_OUTPUT_ADDRESS_LOW_OFFSET = 16'h0068;",
                "  localparam logic [15:0] DAU_REGISTER_OUTPUT_ADDRESS_HIGH_OFFSET = 16'h006c;",
                "  localparam logic [15:0] DAU_REGISTER_OUTPUT_LENGTH_LOW_OFFSET = 16'h0070;",
                "  localparam logic [15:0] DAU_REGISTER_OUTPUT_LENGTH_HIGH_OFFSET = 16'h0074;",
                "  localparam logic [15:0] DAU_REGISTER_OPERATION_OFFSET = 16'h0078;",
                "  localparam logic [15:0] DAU_REGISTER_RESULT_LENGTH_LOW_OFFSET = 16'h007c;",
                "  localparam logic [15:0] DAU_REGISTER_RESULT_LENGTH_HIGH_OFFSET = 16'h0080;",
                f"  localparam logic [63:0] DAU_DEFAULT_INPUT_ADDRESS = 64'h{input_buffer_address:016x};",
                f"  localparam logic [63:0] DAU_DEFAULT_INPUT_LENGTH = 64'h{input_buffer_bytes:016x};",
                f"  localparam logic [63:0] DAU_DEFAULT_OUTPUT_ADDRESS = 64'h{output_buffer_address:016x};",
                f"  localparam logic [63:0] DAU_DEFAULT_OUTPUT_LENGTH = 64'h{output_buffer_bytes:016x};",
                f"  localparam logic [63:0] DAU_DEFAULT_RESULT_BYTES = 64'h{result_bytes:016x};",
                "",
                "  logic [63:0] job_input_address;",
                "  logic [63:0] job_input_length;",
                "  logic [63:0] job_output_address;",
                "  logic [63:0] job_output_length;",
                "  logic [63:0] job_result_length;",
                "  logic [31:0] job_operation;",
                "  logic [31:0] job_last_error;",
                "  logic job_busy;",
                "  logic job_done;",
                "  logic job_error;",
                "  logic stream_job_start_pulse;",
                "  logic [31:0] job_status_value;",
                "",
                "  assign register_ready = register_read_enable || register_write_enable;",
                "  assign stream_job_start_pulse = register_write_enable && (register_address == DAU_REGISTER_JOB_CONTROL_OFFSET) && register_write_data[0];",
                "  assign job_status_value = {28'd0, job_error, job_done, job_busy, !job_busy};",
                "  assign dma_input_address = job_input_address;",
                "  assign dma_input_length = job_input_length[31:0];",
                "  assign dma_output_address = job_output_address;",
                "  assign dma_output_length = job_output_length[31:0];",
                "  assign dma_start = stream_job_start_pulse;",
                "  assign capability_magic = 32'h44415531;",
                "  assign capability_register_map_version = DAU_REGISTER_MAP_VERSION;",
                "  assign capability_stream_protocol_version = DAU_STREAM_PROTOCOL_VERSION;",
                "  assign capability_operator_bitmap = DAU_OPERATOR_BITMAP;",
                "",
            )
        )
        if stream_module_name is None:
            lines.extend(
                (
                    "  assign stream_input_ready = 1'b0;",
                    "  assign stream_output_valid = 1'b0;",
                    "  assign stream_output_data = 64'd0;",
                    "  assign stream_output_last = 1'b0;",
                    "  assign stream_status_valid = 1'b0;",
                    "  assign stream_status_error = 1'b0;",
                    "  assign stream_status_error_code = 8'd0;",
                    "",
                )
            )

        lines.extend(
            (
                "  always_ff @(posedge " + clk + ") begin",
                f"    if ({reset}) begin",
                "      job_input_address <= DAU_DEFAULT_INPUT_ADDRESS;",
                "      job_input_length <= DAU_DEFAULT_INPUT_LENGTH;",
                "      job_output_address <= DAU_DEFAULT_OUTPUT_ADDRESS;",
                "      job_output_length <= DAU_DEFAULT_OUTPUT_LENGTH;",
                "      job_result_length <= 64'd0;",
                "      job_operation <= 32'd0;",
                "      job_last_error <= 32'd0;",
                "      job_busy <= 1'b0;",
                "      job_done <= 1'b0;",
                "      job_error <= 1'b0;",
                "    end else begin",
                "      if (register_write_enable) begin",
                "        unique case (register_address)",
                "          DAU_REGISTER_INPUT_ADDRESS_LOW_OFFSET: job_input_address[31:0] <= register_write_data;",
                "          DAU_REGISTER_INPUT_ADDRESS_HIGH_OFFSET: job_input_address[63:32] <= register_write_data;",
                "          DAU_REGISTER_INPUT_LENGTH_LOW_OFFSET: job_input_length[31:0] <= register_write_data;",
                "          DAU_REGISTER_INPUT_LENGTH_HIGH_OFFSET: job_input_length[63:32] <= register_write_data;",
                "          DAU_REGISTER_OUTPUT_ADDRESS_LOW_OFFSET: job_output_address[31:0] <= register_write_data;",
                "          DAU_REGISTER_OUTPUT_ADDRESS_HIGH_OFFSET: job_output_address[63:32] <= register_write_data;",
                "          DAU_REGISTER_OUTPUT_LENGTH_LOW_OFFSET: job_output_length[31:0] <= register_write_data;",
                "          DAU_REGISTER_OUTPUT_LENGTH_HIGH_OFFSET: job_output_length[63:32] <= register_write_data;",
                "          DAU_REGISTER_OPERATION_OFFSET: job_operation <= register_write_data;",
                "          default: begin end",
                "        endcase",
                "      end",
                "      if (stream_job_start_pulse) begin",
                "        job_busy <= 1'b1;",
                "        job_done <= 1'b0;",
                "        job_error <= 1'b0;",
                "        job_last_error <= 32'd0;",
                "        job_result_length <= 64'd0;",
                "      end",
                "      if (stream_status_valid && stream_status_ready) begin",
                "        job_busy <= 1'b0;",
                "        job_done <= !stream_status_error;",
                "        job_error <= stream_status_error;",
                "        job_last_error <= {24'd0, stream_status_error_code};",
                "        job_result_length <= stream_status_error ? 64'd0 : DAU_DEFAULT_RESULT_BYTES;",
                "      end else if (dma_error) begin",
                "        job_busy <= 1'b0;",
                "        job_done <= 1'b0;",
                "        job_error <= 1'b1;",
                "        job_last_error <= 32'h0000_0003;",
                "        job_result_length <= 64'd0;",
                "      end else if (dma_done && job_busy) begin",
                "        job_busy <= 1'b0;",
                "        job_done <= 1'b1;",
                "        job_error <= 1'b0;",
                "        job_result_length <= DAU_DEFAULT_RESULT_BYTES;",
                "      end",
                "    end",
                "  end",
                "",
                "  always_comb begin",
                "    unique case (register_address)",
                "      DAU_REGISTER_MAGIC_OFFSET: register_read_data = DAU_MAGIC;",
                "      DAU_REGISTER_MAP_VERSION_OFFSET: register_read_data = DAU_REGISTER_MAP_VERSION;",
                "      DAU_STREAM_PROTOCOL_VERSION_OFFSET: register_read_data = DAU_STREAM_PROTOCOL_VERSION;",
                "      DAU_REGISTER_OPERATOR_BITMAP_OFFSET: register_read_data = DAU_OPERATOR_BITMAP;",
                "      DAU_REGISTER_LAST_ERROR_OFFSET: register_read_data = job_last_error;",
                "      DAU_REGISTER_JOB_CONTROL_OFFSET: register_read_data = 32'd0;",
                "      DAU_REGISTER_JOB_STATUS_OFFSET: register_read_data = job_status_value;",
                "      DAU_REGISTER_INPUT_ADDRESS_LOW_OFFSET: register_read_data = job_input_address[31:0];",
                "      DAU_REGISTER_INPUT_ADDRESS_HIGH_OFFSET: register_read_data = job_input_address[63:32];",
                "      DAU_REGISTER_INPUT_LENGTH_LOW_OFFSET: register_read_data = job_input_length[31:0];",
                "      DAU_REGISTER_INPUT_LENGTH_HIGH_OFFSET: register_read_data = job_input_length[63:32];",
                "      DAU_REGISTER_OUTPUT_ADDRESS_LOW_OFFSET: register_read_data = job_output_address[31:0];",
                "      DAU_REGISTER_OUTPUT_ADDRESS_HIGH_OFFSET: register_read_data = job_output_address[63:32];",
                "      DAU_REGISTER_OUTPUT_LENGTH_LOW_OFFSET: register_read_data = job_output_length[31:0];",
                "      DAU_REGISTER_OUTPUT_LENGTH_HIGH_OFFSET: register_read_data = job_output_length[63:32];",
                "      DAU_REGISTER_OPERATION_OFFSET: register_read_data = job_operation;",
                "      DAU_REGISTER_RESULT_LENGTH_LOW_OFFSET: register_read_data = job_result_length[31:0];",
                "      DAU_REGISTER_RESULT_LENGTH_HIGH_OFFSET: register_read_data = job_result_length[63:32];",
                "      default: register_read_data = 32'hffff_ffff;",
                "    endcase",
                "  end",
                "",
            )
        )

        for module_name, module in selected.items():
            params_str = ""
            if module.parameters:
                param_parts = [f".{parameter.name}({parameter.value})" for parameter in module.parameters]
                params_str = f" #({', '.join(param_parts)})"
            lines.append(f"  {module_name}{params_str} {module_name}_inst (")
            connection_lines = []
            is_stream_module = module_name == stream_module_name
            for input_port in module.inputs:
                connection = _dau_input_connection(input_port.name, module_name, clk=clk, reset=reset, is_stream_module=is_stream_module)
                connection_lines.append(f"    .{input_port.name}({connection or _top_port_name(module_name, input_port.name)})")
            for output_port in module.outputs:
                connection = _dau_output_connection(output_port.name, module_name, is_stream_module=is_stream_module)
                connection_lines.append(f"    .{output_port.name}({connection or _top_port_name(module_name, output_port.name)})")
            lines.append(",\n".join(connection_lines))
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


_DAU_STREAM_INPUT_CONNECTIONS = {
    "input_valid": "stream_input_valid",
    "input_data": "stream_input_data",
    "input_last": "stream_input_last",
    "output_ready": "stream_output_ready",
    "status_ready": "stream_status_ready",
}

_DAU_STREAM_OUTPUT_CONNECTIONS = {
    "input_ready": "stream_input_ready",
    "output_valid": "stream_output_valid",
    "output_data": "stream_output_data",
    "output_last": "stream_output_last",
    "status_valid": "stream_status_valid",
    "status_error": "stream_status_error",
    "status_error_code": "stream_status_error_code",
}


def _is_dau_stream_module(module: Module) -> bool:
    input_names = {input_port.name for input_port in module.inputs}
    output_names = {output_port.name for output_port in module.outputs}
    return set(_DAU_STREAM_INPUT_CONNECTIONS).issubset(input_names) and set(_DAU_STREAM_OUTPUT_CONNECTIONS).issubset(output_names)


def _dau_input_connection(port_name: str, _module_name: str, *, clk: str, reset: str, is_stream_module: bool) -> str | None:
    if port_name == clk:
        return clk
    if port_name == reset:
        return reset
    if is_stream_module:
        return _DAU_STREAM_INPUT_CONNECTIONS.get(port_name)
    return None


def _dau_output_connection(port_name: str, _module_name: str, *, is_stream_module: bool) -> str | None:
    if is_stream_module:
        return _DAU_STREAM_OUTPUT_CONNECTIONS.get(port_name)
    return None


def _top_port_name(module_name: str, port_name: str) -> str:
    return f"{module_name}_{port_name}"


def _top_port_declaration(direction: str, module_name: str, port: Port) -> str:
    dimension = port.dimensions.to_sv()
    space = f" {dimension} " if dimension else " "
    return f"  {direction} {port.keyword}{space}{_top_port_name(module_name, port.name)}"
