from typing import List, Literal, Optional

from amaranth.lib.wiring import Component, In, Module, Out
from pydantic import BaseModel, Field, model_validator
from pyslang import ImplicitAnsiPortSyntax, SyntaxNode, SyntaxTree
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
    dimensions: List[int] = Field(default_factory=list)

    def __str__(self):
        if len(self.dimensions) == 1:
            return f"{self.dimensions[0]}'b"
        elif len(self.dimensions) == 2:
            return f"[{self.dimensions[0]}: {self.dimensions[1]}]"
        else:
            # TODO
            assert False

    def size(self):
        if len(self.dimensions) == 1:
            return self.dimensions[0]
        elif len(self.dimensions) == 2:
            return self.dimensions[0] - self.dimensions[1] + 1
        else:
            # TODO
            assert False


class _Base(BaseModel):
    name: str
    node: Optional[object] = Field(default=None)

    def __str__(self):
        return f"{self.__class__.__name__}({self.name})"

    def __repr__(self):
        return self.__str__()

    def _amaranth(self):
        raise NotImplementedError()


class Port(_Base):
    keyword: Keyword
    dimensions: Dimensions

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


class Parameter(_Base):
    value: int

    def __str__(self):
        return f"{self.__class__.__name__}({self.name}={self.value})"


class Module(_Base):
    parameters: List[Parameter] = Field(default_factory=list)
    inputs: List[Input] = Field(default_factory=list)
    outputs: List[Output] = Field(default_factory=list)

    @classmethod
    def from_str(self, st: str) -> Module:
        tree = SyntaxTree.fromText(st)
        return Module(name=tree.root.header.name.value, node=tree.root)

    def __str__(self):
        ret = f"{self.__class__.__name__}({self.name})"
        for param in self.parameters:
            ret += f"\n\t{param}"
        if self.parameters:
            ret += "\n"
        for input in self.inputs:
            ret += f"\n\t{input}"
        if self.inputs:
            ret += "\n"
        for output in self.outputs:
            ret += f"\n\t{output}"
        if self.outputs:
            ret += "\n"
        return ret

    def __repr__(self):
        return self.__str__()

    @model_validator(mode="after")
    def _parse_structure(self) -> Self:
        self._parse_params()
        self._parse_ports()
        self.__amaranth__ = type(self.name, (Component,), {})
        for input in self.inputs:
            self.__amaranth__.__annotations__[input.name] = input.__amaranth__
        for output in self.outputs:
            self.__amaranth__.__annotations__[output.name] = output.__amaranth__
        return self

    def _parse_params(self):
        for paramlist in self.node.header.parameters:
            if isinstance(paramlist, SyntaxNode):
                for param in paramlist:
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
                    direction = port.header.direction.valueText
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
                    # print(f"{direction} {keyword} {dimensions} {declarator}")
        else:
            # TODO
            assert False
