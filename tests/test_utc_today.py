"""In this codebase "today" is always UTC — in application code and in tests.

`date.today()` reads the server's LOCAL clock. Production runs UTC, so the
two agree there and the bug is invisible; on a developer's machine west of
UTC they disagree for the last hours of every day, and the symptom is an
import window or a calendar anchor that is off by one for reasons that are
very hard to trace back here (#94).

The point of this file is the sweep. A behavioural test can only cover the
sites that exist today; this one fails on the next one somebody adds.

Parsed with `ast` rather than grepped, so a mention inside a docstring or a
comment — this module is full of them — is not mistaken for a call.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
TESTS = REPO / "tests"

_LOCAL_CLOCK_ATTRS = {"today", "now"}


class _Visitor(ast.NodeVisitor):
    """Collects `date.today()`, `datetime.today()` and bare `datetime.now()`.

    `datetime.now(timezone.utc)` is the correct form and carries an argument,
    so requiring zero args is what separates right from wrong here.
    """

    def __init__(self, path: Path):
        self.path = path
        self.hits: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _LOCAL_CLOCK_ATTRS
            and not node.args
            and not node.keywords
        ):
            base = func.value
            name = getattr(base, "id", None) or getattr(base, "attr", None)
            if name in {"date", "datetime"}:
                self.hits.append(
                    f"{self.path.relative_to(REPO)}:{node.lineno}: "
                    f"{name}.{func.attr}()"
                )
        self.generic_visit(node)


def _offenders(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if any(p in {".venv", "__pycache__", "node_modules"} for p in path.parts):
            continue
        visitor = _Visitor(path)
        visitor.visit(ast.parse(path.read_text()))
        hits.extend(visitor.hits)
    return hits


_FIX = "Use datetime.now(timezone.utc).date() instead — see CLAUDE.md.\n"


def test_backend_never_reads_the_local_clock():
    offenders = _offenders(BACKEND)
    assert offenders == [], _FIX + "\n".join(offenders)


def test_tests_never_read_the_local_clock():
    """#87 fixed four tests that compared local dates against UTC-derived
    production values. The rule only holds if it holds on both sides."""
    offenders = _offenders(TESTS)
    assert offenders == [], _FIX + "\n".join(offenders)
