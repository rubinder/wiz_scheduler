# factory/gates.py
"""Gate definitions: factory/gates/<name>.md with TOML front matter."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

GATES_DIR = Path(__file__).resolve().parent / "gates"


@dataclass(frozen=True)
class Gate:
    name: str
    always: bool
    triggers: dict[str, list[str]]
    mechanical: dict[str, list[str]]
    reviewer: bool
    prompt: str


def _parse(path: Path) -> Gate:
    text = path.read_text()
    _, front, body = text.split("+++", 2)
    meta = tomllib.loads(front)
    return Gate(
        name=meta["name"],
        always=bool(meta.get("always", False)),
        triggers={k: list(v) for k, v in (meta.get("triggers") or {}).items()},
        mechanical={k: list(v) for k, v in (meta.get("mechanical") or {}).items()},
        reviewer=bool(meta.get("reviewer", True)),
        prompt=body.strip(),
    )


def load_gates(directory: Path = GATES_DIR) -> list[Gate]:
    return [_parse(p) for p in sorted(directory.glob("*.md"))]


def render(cmd: str, **values: str) -> str:
    for k, v in values.items():
        cmd = cmd.replace("{" + k + "}", v)
    return cmd
