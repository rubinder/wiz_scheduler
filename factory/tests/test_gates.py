import json
import re
import subprocess
import sys
from pathlib import Path

from factory import allowlist_growth, gates

SPEC_GATES = {"ci", "correctness", "conventions", "tests", "architecture", "tenancy-security",
              "accessibility", "lighthouse", "i18n", "copy-truth", "migration-safety"}
ALLOWED_PLACEHOLDERS = {"worktree", "py", "base", "app", "urls"}
REPO = Path(__file__).resolve().parents[2]


def test_every_spec_gate_exists_and_parses():
    loaded = {g.name: g for g in gates.load_gates()}
    assert set(loaded) == SPEC_GATES
    for g in loaded.values():
        assert g.prompt.strip() or not g.reviewer, g.name


def test_always_gates():
    always = {g.name for g in gates.load_gates() if g.always}
    assert always == {"ci", "correctness", "conventions", "tests", "architecture"}


def test_lighthouse_has_no_reviewer_and_migration_is_mechanical():
    by = {g.name: g for g in gates.load_gates()}
    assert by["lighthouse"].reviewer is False
    assert any("test_migrations.py" in c for c in by["migration-safety"].mechanical["app"])
    assert any("axe" in c for c in by["accessibility"].mechanical["app"])


def test_render_fills_placeholders():
    assert gates.render("cd {worktree} && {py} -m x", worktree="/w", py="/p") == "cd /w && /p -m x"


def test_only_allowed_placeholders():
    for g in gates.load_gates():
        for cmds in g.mechanical.values():
            for cmd in cmds:
                used = set(re.findall(r"\{(\w*)\}", cmd))
                assert used <= ALLOWED_PLACEHOLDERS, (g.name, used - ALLOWED_PLACEHOLDERS)


def test_architecture_gate_runs_allowlist_growth_and_cites_patterns():
    arch = {g.name: g for g in gates.load_gates()}["architecture"]
    assert any("factory.allowlist_growth" in c for c in arch.mechanical["app"])
    for pid in ("single-evaluator", "thin-router", "fail-closed", "utc-today"):
        assert pid in arch.prompt


def test_tenancy_gate_keeps_findings_out_of_public_places():
    body = {g.name: g for g in gates.load_gates()}["tenancy-security"].prompt
    assert "never go into GitHub comments, issues or PRs" in body
    assert "security finding — see owner notification" in body


def test_pytest_files_named_by_gates_exist():
    for g in gates.load_gates():
        for cmd in g.mechanical.get("app", []):
            for path in re.findall(r"\b(?:tests/\S+\.py|src/\S+\.test\.ts)\b", cmd):
                base = REPO / "frontend" if path.startswith("src/") else REPO
                assert (base / path).exists(), (g.name, path)


# Test configuration each runner reads, per repo and working directory. A PR
# that could change these could change what its own gates run.
_RUNNER_CONFIG = {
    ("app", "pytest"): ["conftest.py", "tests/conftest.py", "pytest.ini", ".pytest.ini", "tests/.pytest.ini",
                        "pyproject.toml", "setup.cfg", "tox.ini"],
    ("app", "npm"): ["package.json", "package-lock.json", ".npmrc", "vite.config.ts", "vitest.config.ts", "vitest.config.mjs",
                     "postcss.config.js", "tailwind.config.ts", "tsconfig.json", "tsconfig.node.json", "src/setupTests.ts"],
    ("marketing", "npm"): ["package.json", "package-lock.json", ".npmrc", "astro.config.mjs", "vite.config.ts",
                           "vitest.config.ts", "vitest.config.mjs", "postcss.config.cjs", "tailwind.config.ts",
                           "tsconfig.json", "tests/setup.ts"],
}
_FILE_TOKEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|sh|mjs|js|ts|json)$")


def gate_named_files(cmd: str, repo: str) -> set[tuple[str, str]]:
    """(repo, path) for every file a mechanical command names or whose runner config it reads.

    Follows `cd {worktree}[/sub]` and `cd {app}[/sub]` (sub-shells included) so a
    relative name resolves against the directory it runs in.
    """
    out: set[tuple[str, str]] = set()
    where = (repo, "")
    join = lambda d, f: f"{d}/{f}" if d else f  # noqa: E731
    stack: list[tuple[str, str]] = []
    for seg in re.split(r"(\(|\))|&&|\|\||;|\{ |(?:^|\s)\}(?=\s|$)", cmd):
        if seg == "(":  # a sub-shell's cd ends with it
            stack.append(where)
            continue
        if seg == ")":
            where = stack.pop() if stack else where
            continue
        words = [w.strip("\"'") for w in (seg or "").split()]
        if not words:
            continue
        if words[0] == "cd" and len(words) == 2:
            m = re.fullmatch(r"\{(worktree|app)\}(?:/(.*))?", words[1])
            if m:
                where = (repo if m.group(1) == "worktree" else "app", (m.group(2) or "").strip("/"))
            elif re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", words[1]):
                where = (where[0], join(where[1], words[1].strip("/")))
            continue
        for i, w in enumerate(words):
            if w.startswith("{app}/"):
                out.add(("app", w.removeprefix("{app}/")))
            elif "$" not in w and "{" not in w and _FILE_TOKEN.fullmatch(w) and not w.startswith("http"):
                out.add((where[0], join(where[1], w)))
            elif w == "-m" and i + 1 < len(words) and words[i + 1].startswith("factory."):
                out.add(("app", words[i + 1].replace(".", "/") + ".py"))
        runner = "pytest" if "pytest" in words else ("npm" if words[0] in ("npm", "npx") else None)
        if runner:
            for f in _RUNNER_CONFIG.get((where[0], runner), []):
                out.add((where[0], join(where[1], f)))
    return out


def test_gate_named_files_parse_cd_context():
    got = gate_named_files("cd {worktree}/frontend && node scripts/verify/contrast.mjs", "app")
    assert ("app", "frontend/scripts/verify/contrast.mjs") in got
    got = gate_named_files('cd {worktree} && x; cd {worktree} && VERIFY_BASE=h node {app}/frontend/scripts/verify/axe.mjs', "marketing")
    assert ("app", "frontend/scripts/verify/axe.mjs") in got
    got = gate_named_files("cd {worktree} && {py} -m pytest tests/test_a.py -q", "app")
    assert {("app", "tests/test_a.py"), ("app", "tests/conftest.py"), ("app", "pyproject.toml")} <= got


def test_every_file_a_gate_runs_and_its_test_config_is_protected():
    from factory import config
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    named: set[tuple[str, str]] = set()
    for g in gates.load_gates():
        for repo, cmds in g.mechanical.items():
            for cmd in cmds:
                named |= gate_named_files(cmd, repo)
    assert ("app", "tests/test_tenant_filter_integration.py") in named and ("app", "factory/stack.sh") in named
    assert ("marketing", "tests/locale-parity.test.ts") in named and ("marketing", "package.json") in named
    unprotected = sorted(f"{r}:{f}" for r, f in named if not config.protected_hits(r, [f], cfg))
    assert unprotected == []


# --- allowlist_growth ---

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _entry(rule: str, file: str, detail: str = "d") -> dict:
    return {"rule": rule, "file": file, "detail": detail, "issue": None}


def _repo(tmp_path: Path, base: list[dict]) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "tests/architecture_allowlist.json").write_text(json.dumps(base))
    (tmp_path / "frontend/src/architecture_allowlist.json").write_text("[]")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _run(repo: Path, base: str = "main") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "factory.allowlist_growth", "--worktree", str(repo),
                           "--base", base], capture_output=True, text=True, cwd=REPO)


def test_growth_flags_new_entry(tmp_path):
    repo = _repo(tmp_path, [_entry("a", "x.py")])
    (repo / "tests/architecture_allowlist.json").write_text(json.dumps([_entry("a", "x.py"), _entry("b", "y.py")]))
    r = _run(repo)
    assert r.returncode == 1
    assert "b y.py d" in r.stdout and "a x.py" not in r.stdout


def test_growth_passes_on_reorder_and_shrink(tmp_path):
    repo = _repo(tmp_path, [_entry("a", "x.py"), _entry("b", "y.py")])
    (repo / "tests/architecture_allowlist.json").write_text(json.dumps([_entry("b", "y.py")]))
    assert _run(repo).returncode == 0
    (repo / "tests/architecture_allowlist.json").write_text(
        json.dumps([_entry("b", "y.py"), _entry("a", "x.py")]))
    assert _run(repo).returncode == 0


def test_growth_errors_on_bad_base_and_bad_json(tmp_path):
    repo = _repo(tmp_path, [])
    assert _run(repo, base="nope").returncode == 2
    (repo / "tests/architecture_allowlist.json").write_text("{not json")
    assert _run(repo).returncode == 2


def test_growth_main_in_process(tmp_path, capsys):
    repo = _repo(tmp_path, [])
    assert allowlist_growth.main(["--worktree", str(repo), "--base", "main"]) == 0


# --- fix round 1 ---

CONFIDENTIALITY = ("never go into GitHub comments, issues or PRs", "security finding — see owner notification")


def test_every_reviewer_gate_has_confidentiality():
    for g in gates.load_gates():
        if g.reviewer:
            for phrase in CONFIDENTIALITY:
                assert phrase in g.prompt, (g.name, phrase)


def test_factory_modules_run_from_app_clone_and_exist():
    for g in gates.load_gates():
        for cmds in g.mechanical.values():
            for cmd in cmds:
                for m in re.finditer(r"\{py\} -m factory\.(\w+)", cmd):
                    cds = re.findall(r"\bcd (\S+)", cmd[: m.start()])
                    assert cds and cds[-1] == "{app}", (g.name, cmd)
                    mod = m.group(1)
                    assert (REPO / "factory" / f"{mod}.py").exists(), (g.name, mod)


def test_preview_commands_use_private_pidfile_and_guard_build():
    for g in gates.load_gates():
        for cmds in g.mechanical.values():
            for cmd in cmds:
                assert "/tmp/wizfactory" not in cmd
                if "preview" in cmd:
                    assert "npm run build && {" in cmd and "rev-parse --absolute-git-dir" in cmd
                    assert '[ -f "$pf" ]' in cmd and "pkill -P" in cmd


def test_copy_truth_fires_on_app_locale_files():
    app = {g.name: g for g in gates.load_gates()}["copy-truth"].triggers["app"]
    assert app == ["frontend/src/i18n/*.ts"]
