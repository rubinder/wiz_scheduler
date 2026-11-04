import subprocess
from pathlib import Path

import pytest

from factory import gates, select_gates

FIX = Path(__file__).resolve().parent / "fixtures" / "diffs"
ALL = gates.load_gates()
ALWAYS = {"ci", "correctness", "conventions", "tests", "architecture"}


def load(name):
    files, diff = (FIX / name).read_text().split("---", 1)
    return [f for f in files.split("\n") if f.strip()], diff


def pick(repo, name):
    files, diff = load(name)
    return set(select_gates.by_globs(repo, files, diff, ALL))


def test_tenancy_triggers_on_router_change():
    assert pick("app", "tenancy.txt") == ALWAYS | {"tenancy-security"}


def test_page_change_triggers_a11y_and_i18n_key():
    assert pick("app", "a11y_app.txt") == ALWAYS | {"accessibility", "i18n"}


def test_marketing_view_triggers_a11y_lighthouse_copy_truth():
    assert pick("marketing", "marketing_view.txt") == ALWAYS | {"accessibility", "lighthouse", "copy-truth"}


def test_migration_triggers_migration_safety():
    assert "migration-safety" in pick("app", "migration.txt")


def test_docs_only_triggers_only_always_gates():
    assert pick("app", "docs_only.txt") == ALWAYS


def test_each_selectable_gate_has_a_negative_fixture():
    for g in ALL:
        if not g.always:
            assert g.name not in pick("app", "docs_only.txt")


def test_model_can_add_but_never_remove():
    chosen = {"ci": "always", "tenancy-security": "touches x"}
    got = select_gates.add_by_model("app", ["frontend/src/utils/x.ts"], "changes rendering", chosen, ALL,
                                    ask=lambda p: '{"add": [{"name": "accessibility", "reason": "utils change alters rendering"}, {"name": "made-up", "reason": "x"}], "remove": ["ci"]}')
    assert got == {**chosen, "accessibility": "model: utils change alters rendering"}


def test_model_failure_keeps_glob_selection():
    chosen = {"ci": "always"}

    def boom(p):
        raise RuntimeError("claude down")
    assert select_gates.add_by_model("app", [], "", chosen, ALL, ask=boom) == chosen
    assert select_gates.add_by_model("app", [], "", chosen, ALL, ask=lambda p: "not json") == chosen


def test_oversized_model_reply_is_ignored():
    chosen = {"ci": "always"}
    big = '{"add": [{"name": "accessibility", "reason": "' + "x" * 25000 + '"}]}'
    assert select_gates.add_by_model("app", [], "", chosen, ALL, ask=lambda p: big) == chosen


def test_malformed_model_shapes_keep_selection():
    chosen = {"ci": "always"}
    for reply in ('[]', '{"add": "accessibility"}', '{"add": [1, null, {"name": ["a"]}]}'):
        assert select_gates.add_by_model("app", [], "", chosen, ALL, ask=lambda p, r=reply: r) == chosen


def _repo(tmp_path):
    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    g("add", ".")
    g("commit", "-qm", "base")
    (tmp_path / "docs.md").write_text("x")
    g("add", ".")
    g("commit", "-qm", "change")
    return str(tmp_path)


def test_select_renders_commands_and_drops_unsafe_urls(tmp_path, capsys):
    wt = _repo(tmp_path)
    (Path(wt) / "frontend/src/pages").mkdir(parents=True)
    (Path(wt) / "frontend/src/pages/P.tsx").write_text("x")
    subprocess.run(["git", "-C", wt, "add", "."], check=True)
    subprocess.run(["git", "-C", wt, "commit", "-qm", "page"], check=True, capture_output=True)
    out = select_gates.select("app", wt, "HEAD~2", "/app", "/py",
                              ["/ok", "/a;rm -rf x", "/$(id)", "/team/list"], ask=lambda p: "{}")
    a11y = next(g for g in out if g["name"] == "accessibility")
    joined = " ".join(a11y["mechanical"])
    assert "/ok /team/list" in joined
    assert ";rm" not in joined and "$(id)" not in joined
    err = capsys.readouterr().err
    assert "/a;rm -rf x" in err and "/$(id)" in err


def test_select_fails_closed_when_git_diff_fails(tmp_path):
    wt = _repo(tmp_path)
    with pytest.raises(RuntimeError):
        select_gates.select("app", wt, "no-such-ref", "/app", "/py", ["/"], ask=lambda p: "{}")


def test_ask_haiku_is_argv_and_read_only(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n")
    monkeypatch.setattr(select_gates.subprocess, "run", fake_run)
    assert select_gates._ask_haiku("hi; $(x)") == "ok"
    assert isinstance(seen["argv"], list) and seen["argv"][-1] == "hi; $(x)"
    assert "plan" == seen["argv"][seen["argv"].index("--permission-mode") + 1]
    assert not seen["kw"].get("shell")


def test_trailing_newline_or_cr_urls_are_dropped(capsys):
    assert select_gates._safe_urls(["/a\n", "/b\r", "/ok"]) == ["/ok"]
    assert select_gates._safe_urls(["/a\n"]) == ["/"]


def test_rename_out_of_routers_still_selects_tenancy(monkeypatch):
    calls = []

    def fake_git(wt, *a):
        calls.append(a)
        if "--name-only" in a:
            return "backend/routers/employees.py\0backend/services/employees_new.py\0"
        return ""
    monkeypatch.setattr(select_gates, "_git", fake_git)
    out = select_gates.select("app", "/w", "origin/main", "/app", "/py", ["/"], ask=lambda p: "{}")
    assert "tenancy-security" in {g["name"] for g in out}
    assert all("--no-renames" in c for c in calls)
    assert "-z" in next(c for c in calls if "--name-only" in c)


def test_render_shell_quotes_values(monkeypatch):
    monkeypatch.setattr(select_gates, "_git", lambda wt, *a: "frontend/src/pages/P.tsx\0" if "--name-only" in a else "")
    out = select_gates.select("app", "/w x", "origin/main", "/app", "/py", ["/ok"], ask=lambda p: "{}")
    cmd = next(g for g in out if g["name"] == "accessibility")["mechanical"][0]
    assert "cd '/w x' " in cmd and "/ok" in cmd
