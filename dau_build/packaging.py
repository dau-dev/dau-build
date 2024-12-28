from typing import List, Literal

from ccflow import BaseModel as FlowBaseModel
from pydantic import Field
from pydantic_extra_types.semantic_version import SemanticVersion

Language = Literal["systemverilog", "amaranth"]


class Module(FlowBaseModel):
    """Wrapper around an HDL module"""

    name: str
    source: "Source"  # Backreferece, the source file in which this module is defined


class Source(FlowBaseModel):
    """Wrapper around an HDL source file"""

    language: Language
    modules: List[Module] = Field(default_factory=list)


class Package(FlowBaseModel):
    """Package is a collection of source files and their corresponding modules"""

    name: str
    version: SemanticVersion
    sources: List[Source] = Field(default_factory=list)

    @property
    def modules(self):
        return [module for source in self.sources for module in source.modules]
