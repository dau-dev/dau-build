"""Typed ``key=value`` manifests.

The Vivado backend and the spec build write their handoff manifests as
``key=value`` lines, the format the generated Tcl appends to in-band. A
manifest is a frozen model: the writer builds the model and serializes it,
and the reader parses the lines (a later line for the same key replaces an
earlier one, which is how the Tcl records outcomes over the planned values)
and validates them through the same model.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, ValidationError

__all__ = (
    "CommaList",
    "KeyValueManifest",
    "KeyValueManifestError",
    "NonEmptyText",
    "OptionalText",
    "parse_key_value_text",
)


class KeyValueManifestError(ValueError):
    pass


def _split_commas(value: Any) -> Any:
    if isinstance(value, str):
        return tuple(item for item in value.split(",") if item)
    return value


def _empty_to_none(value: Any) -> Any:
    return None if value == "" else value


# a comma-joined list on the line, a tuple in the model
CommaList = Annotated[tuple[str, ...], BeforeValidator(_split_commas), PlainSerializer(lambda value: ",".join(value), return_type=str)]
# written as an empty value when unset; the line is always present
OptionalText = Annotated[str | None, BeforeValidator(_empty_to_none), PlainSerializer(lambda value: value or "", return_type=str)]
NonEmptyText = Annotated[str, Field(min_length=1)]


def parse_key_value_text(text: str) -> dict[str, str]:
    """``key=value`` lines to a mapping; a repeated key keeps its last value.
    Blank lines and ``#`` comments are skipped."""
    items: dict[str, str] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            errors.append(f"manifest line {line_number} is missing '='")
        elif not key:
            errors.append(f"manifest line {line_number} has an empty key")
        else:
            items[key] = value
    if errors:
        raise KeyValueManifestError("; ".join(errors))
    return items


class KeyValueManifest(BaseModel):
    """Base for a ``key=value`` manifest. Fields serialize in declaration
    order; a field whose value is ``None`` is left out of the text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def from_items(cls, items: Iterable[tuple[str, str]]) -> Self:
        try:
            return cls.model_validate(dict(items))
        except ValidationError as exc:
            raise KeyValueManifestError(f"invalid {cls.__name__}: {exc}") from exc

    @classmethod
    def from_text(cls, text: str) -> Self:
        return cls.from_items(parse_key_value_text(text).items())

    @classmethod
    def read(cls, path: Path) -> Self:
        try:
            return cls.from_text(path.read_text(encoding="utf-8"))
        except KeyValueManifestError as exc:
            raise KeyValueManifestError(f"{path.as_posix()}: {exc}") from exc

    def items(self) -> tuple[tuple[str, str], ...]:
        # OptionalText dumps an unset value as "", so only plain optional
        # fields come out as None here
        return tuple((key, str(value)) for key, value in self.model_dump(mode="json").items() if value is not None)

    def to_text(self) -> str:
        return "".join(f"{key}={value}\n" for key, value in self.items())
