# tests/test_architecture.py
"""Mechanical layering rules from .claude/skills/architecture/SKILL.md.

An AST sweep in the style of test_utc_today.py. Existing violations live in
architecture_allowlist.json; the list may only shrink: a violation not on the
list fails, and a listed violation that no longer exists fails too, so whoever
fixes one also deletes its entry. (`date.today()` stays in test_utc_today.py.)

Pure AST — no database — but it lives in tests/ so CI runs it; conftest's
session Postgres starts anyway.
"""
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
ALLOWLIST = Path(__file__).resolve().parent / "architecture_allowlist.json"

_STRIPE_MONEY = {("PaymentIntent", "create"), ("PaymentIntent", "confirm"), ("Refund", "create"),
                 ("Charge", "create"), ("Invoice", "pay")}


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def _imports(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
            out += [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names]
    return out


def _violations() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(BACKEND.rglob("*.py")):
        if any(p in {".venv", "__pycache__", "alembic"} for p in path.parts):
            continue
        rel = _rel(path)
        tree = ast.parse(path.read_text())
        mods = _imports(tree)
        if rel.startswith("backend/routers/"):
            for m in mods:
                if m.startswith("backend.routers.") and m.count(".") >= 2:
                    found.add(("router-imports-router", rel, m.split(".")[2]))
        if rel.startswith("backend/models/") or rel == "backend/models.py":
            for m in mods:
                if m.startswith(("backend.services", "backend.routers")):
                    found.add(("model-imports-service", rel, m.split(".")[1] + "." + (m.split(".")[2] if m.count(".") >= 2 else "")))
        in_billing = path.parent.name == "services" and path.name.startswith("billing")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                inner = node.func.value
                if isinstance(inner, ast.Attribute) and (inner.attr, node.func.attr) in _STRIPE_MONEY and not in_billing:
                    found.add(("stripe-outside-billing", rel, f"{inner.attr}.{node.func.attr}"))
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) and t.attr == "archived_at" and rel != "backend/services/archive.py":
                        found.add(("archived-at-outside-archive", rel, "archived_at ="))
    return found


def _allowed() -> set[tuple[str, str, str]]:
    return {(e["rule"], e["file"], e["detail"]) for e in json.loads(ALLOWLIST.read_text())}


def test_no_new_layering_violations():
    new = sorted(_violations() - _allowed())
    assert new == [], ("New architecture violations (see .claude/skills/architecture/SKILL.md):\n"
                       + "\n".join(f"{r}: {f} ({d})" for r, f, d in new))


def test_allowlist_only_shrinks():
    gone = sorted(_allowed() - _violations())
    assert gone == [], ("These allowlisted violations are fixed — delete their entries from "
                        "tests/architecture_allowlist.json:\n" + "\n".join(f"{r}: {f} ({d})" for r, f, d in gone))


def test_allowlist_entries_are_well_formed():
    for e in json.loads(ALLOWLIST.read_text()):
        assert set(e) == {"rule", "file", "detail", "issue"}, e
        assert e["issue"] is None or isinstance(e["issue"], int), e


def test_planted_violation_is_caught(tmp_path, monkeypatch):
    """Prove the sweep fails: plant one real violation shape for every rule."""
    backend = tmp_path / "backend"
    for sub in ("routers", "models", "services"):
        (backend / sub).mkdir(parents=True)
    (backend / "routers" / "a.py").write_text("from backend.routers.b import thing\n")
    (backend / "models" / "m.py").write_text("from backend.services.plan import assert_can_add\n")
    (backend / "services" / "x.py").write_text(
        "import stripe\nstripe.Refund.create(amount=1)\n"
        "def f(emp, now):\n    emp.archived_at = now\n")
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO", tmp_path)
    monkeypatch.setattr(module, "BACKEND", backend)
    v = _violations()
    assert ("router-imports-router", "backend/routers/a.py", "b") in v
    assert ("model-imports-service", "backend/models/m.py", "services.plan") in v
    assert ("stripe-outside-billing", "backend/services/x.py", "Refund.create") in v
    assert ("archived-at-outside-archive", "backend/services/x.py", "archived_at =") in v
