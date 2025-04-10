from __future__ import annotations

from pathlib import Path
from typing import Literal

from amaranth import Instance
from amaranth.lib.wiring import Component, In, Out
from pydantic import BaseModel, Field, model_validator
from pyslang import (
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
    ScopedNameSyntax,
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
    "Parameter",
    "Module",
)


Keyword = Literal["bit", "wire", "logic"]


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


class _Base(BaseModel):
    name: str
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
    name: str
    inputs: list[Input] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    modports: list["Modport"] = Field(default_factory=list)

    def to_string(self, indent: str = ""):
        ret = f"{self.__class__.__name__}({self.name})"
        for input in self.inputs:
            ret += f"\n{indent}\t{input}"
        if self.inputs:
            ret += "\n"
        for output in self.outputs:
            ret += f"\n{indent}\t{output}"
        if self.outputs:
            ret += "\n"
        for modport in self.modports:
            ret += f"\n{indent}\t{modport}"
        if self.modports:
            ret += "\n"
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
    modports: list[Modport] = Field(default_factory=list, description="Modport inputs/outputs")

    submodules: list["SubModule"] = Field(default_factory=list, description="Sub module instantiations")
    submodports: list[Modport] = Field(default_factory=list, description="Modport instantiations")

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
        return cls.from_str(st)

    @classmethod
    def from_str(cls, st: str) -> Module:
        tree = SyntaxTree.fromText(st)
        return Module(name=tree.root.header.name.value, node=tree.root)

    def to_string(self, indent=""):
        ret = f"{self.__class__.__name__}({self.name})"
        for param in self.parameters:
            ret += f"\n{indent}\t{param}"
        if self.parameters:
            ret += "\n"
        for input in self.inputs:
            ret += f"\n{indent}\t{input}"
        if self.inputs:
            ret += "\n"
        for output in self.outputs:
            ret += f"\n{indent}\t{output}"
        if self.outputs:
            ret += "\n"
        for modport in self.modports:
            mod_str = modport.to_string(indent=indent + "\t")
            ret += f"\n{indent}\t{mod_str.name}: {mod_str}"
        if self.modports:
            ret += "\n"
        for submodule in self.submodules:
            submod_str = submodule.to_string(indent=indent + "\t")
            ret += f"\n{indent}\t{submodule.instance_name}: {submod_str}"
        if self.submodules:
            ret += "\n"
        return ret

    def __str__(self):
        return self.to_string()

    def __repr__(self):
        return self.__str__()

    def resolve_submodules(self, root: Path = Path(".")) -> Module:
        for i, submodule in enumerate(self.submodules):
            self.submodules[i] = submodule.resolve(root=root)
            # recurse
            self.submodules[i].resolve_submodules(root=root)
        return self

    @model_validator(mode="after")
    def _parse_structure(self) -> Self:
        # Skip parsing if not parseable
        if isinstance(self, SubModule) and not self.node:
            return self

        self._parse_params()
        self._parse_ports()
        self._parse_submodules()
        self._parse_modports()

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

    def _parse_ports(self):
        if len(self.node.header.ports) == 3:
            for port in self.node.header.ports[1]:
                if isinstance(port, ImplicitAnsiPortSyntax):
                    if isinstance(port.header, InterfacePortHeaderSyntax):
                        # ifc_name = port.header.nameOrKeyword.value
                        # modport_member = port.header.modport.member.value
                        # modport = Modport(name=ifc_name, value=modport_member)
                        # TODO
                        raise NotImplementedError("Modports coming soon")
                    else:
                        direction = port.header.direction.valueText
                        if isinstance(port.header.dataType, ImplicitTypeSyntax):
                            keyword = "bit"
                        else:
                            keyword = port.header.dataType.keyword.valueText
                        declarator = port.declarator.name.value
                        if port.header.dataType.dimensions:
                            if len(port.header.dataType.dimensions) == 1:
                                left = port.header.dataType.dimensions[0].specifier[0][0]
                                right = port.header.dataType.dimensions[0].specifier[0][2]
                                # TODO
                                # print(eval(left.__str__(), {"SIZE": 30}))
                                dimensions = [
                                    int(eval(left.__str__(), {p.name: p.value for p in self.parameters})),
                                    int(eval(right.__str__(), {p.name: p.value for p in self.parameters})),
                                ]
                            else:
                                # TODO
                                assert False
                        else:
                            dimensions = [1]
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
                        else:
                            # TODO
                            assert False
                elif port.kind == TokenKind.Comma:
                    continue
                else:
                    assert False
        else:
            # TODO
            assert False

    def _parse_submodules(self):
        for member in self.node.members:
            if isinstance(member, HierarchyInstantiationSyntax):
                # name of module instance
                # TODO more than 1?
                instance_name = member.instances[0].decl.name.value
                # module type of instance
                module_type = member.type.value

                submod = SubModule(instance_name=instance_name, name=module_type)

                for i, connection in enumerate(member.instances[0].connections):
                    if isinstance(connection, (OrderedPortConnectionSyntax, NamedPortConnectionSyntax)):
                        # TODO
                        if isinstance(connection.expr[0][0], ScopedNameSyntax):
                            # name = connection.expr[0][0].right.identifier.value
                            # TODO: split apart?
                            val = connection.expr[0][0].__str__().strip()
                            link = Link(name=val, connection=val, position=i)
                            submod.links.append(link)
                        elif isinstance(connection.expr[0][0], IdentifierNameSyntax):
                            val = connection.expr[0][0].identifier.value
                            link = Link(name=val, connection=val, position=i)
                            submod.links.append(link)
                        else:
                            # TODO
                            raise NotImplementedError
                    elif isinstance(connection, WildcardPortConnectionSyntax):
                        # connection like conn(.*)
                        # TODO
                        raise NotImplementedError
                # TODO
                self.submodules.append(submod)

    def _parse_modports(self):
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
                                if direction == "input":
                                    mp.inputs.append(Input(name=port_name))
                                elif direction == "output":
                                    mp.outputs.append(Output(name=port_name))
                print(mp)
                self.submodports.append(mp)


class SubModule(Module):
    """Submodule instantiation inside a module"""

    name: str
    instance_name: str
    links: list[Link] = Field(default_factory=list)

    def resolve(self, root: Path = Path(".")) -> "SubModule":
        m = Module.from_module(self.name, root=root)
        print(m.submodports)
        return SubModule(**{**m.model_dump(), "name": self.name, "instance_name": self.instance_name, "links": self.links})
