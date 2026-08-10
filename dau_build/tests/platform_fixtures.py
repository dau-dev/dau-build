"""The board dau-build's own tests build against.

dau-build ships no real board, so its tests cannot borrow one. They use the
packaged example board (``platform=platforms/example/probe``), which is a
documented fiction: internally consistent, plausible for a Kintex-7 x8 XDMA
card, and measured on nothing.

Resolve it through hydra rather than constructing a ``PlatformDefinition``
literal, so the tests exercise the same composition path every real board
takes -- a Python-literal fixture would keep passing after the config schema
and the models stopped agreeing.
"""

from __future__ import annotations

from functools import lru_cache

from dau_build.platforms import PlatformDefinition

PROBE_PLATFORM_NAME = "platforms/example/probe"


@lru_cache(maxsize=1)
def _resolved() -> PlatformDefinition:
    from dau_build.config import resolve_platform

    return resolve_platform(PROBE_PLATFORM_NAME)


def probe_platform(**overrides: object) -> PlatformDefinition:
    """The example board, optionally with fields replaced.

    The cached resolution is frozen and shared, so overrides go through
    ``model_copy`` and callers can never mutate the fixture for each other.
    """
    platform = _resolved()
    return platform.model_copy(update=dict(overrides)) if overrides else platform
