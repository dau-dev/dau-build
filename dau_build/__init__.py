from importlib import import_module

__version__ = "0.3.0"

_SVPARSER_EXPORTS = frozenset(
    {
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
    }
)


def __getattr__(name: str):
    if name not in _SVPARSER_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    svparser = import_module(".svparser", __name__)
    return getattr(svparser, name)
