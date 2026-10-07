"""Every `dau-build` command the README and docs show composes under
`--explain`. A documented command that does not compose is a claim the
repository cannot back; execution is left to the tests that own each task."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*/*.md"))]
BLOCK = re.compile(r"```(?:bash|text|sh)\n(.*?)```", re.DOTALL)


def _documented_commands() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for page in PAGES:
        for block in BLOCK.findall(page.read_text(encoding="utf-8")):
            joined = block.replace("\\\n", " ")
            for line in joined.splitlines():
                line = line.strip()
                if not line.startswith("dau-build ") or "<" in line or "..." in line:
                    continue  # prose and placeholders, not commands
                if "execute=true" in line or "--config-dir" in line:
                    continue  # hardware or an overlay this repository does not ship
                found.append((page.relative_to(ROOT).as_posix(), line))
    return found


@pytest.mark.parametrize(("page", "command"), _documented_commands(), ids=lambda value: value if "/" in str(value) else None)
def test_a_documented_command_composes(page: str, command: str, monkeypatch, capsys) -> None:
    from dau_build.cli import main

    monkeypatch.chdir(ROOT)
    argv = shlex.split(command)[1:]
    if "--explain" not in argv:
        argv = ["--explain", *argv]
    assert main(argv) == 0, f"{page}: {command}"
    assert "model" in capsys.readouterr().out, f"{page}: {command} composed no model"
