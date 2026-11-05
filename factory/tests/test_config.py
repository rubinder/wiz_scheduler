import re
from pathlib import Path

from factory import config, globs


def test_double_star_spans_directories():
    assert globs.glob_match("backend/alembic/versions/0055_x.py", "backend/alembic/**")
    assert globs.glob_match("frontend/src/pages/manager/Team.tsx", "frontend/src/pages/**")


def test_single_star_stays_in_one_directory():
    assert globs.glob_match("backend/services/billing.py", "backend/services/billing*.py")
    assert globs.glob_match("backend/services/billing_alerts.py", "backend/services/billing*.py")
    assert not globs.glob_match("backend/services/sub/billing.py", "backend/services/billing*.py")


def test_brace_alternation():
    assert globs.glob_match("frontend/src/components/X.tsx", "frontend/src/{pages,components}/**")
    assert not globs.glob_match("frontend/src/utils/x.ts", "frontend/src/{pages,components}/**")


def test_double_star_slash_matches_zero_directories():
    assert globs.glob_match("a.css", "**/*.css")
    assert globs.glob_match("x/y/a.css", "**/*.css")


def test_load_config_reads_example(tmp_path: Path):
    src = Path(__file__).resolve().parents[1] / "config.example.toml"
    cfg = config.load_config(src)
    assert cfg.idle_minutes == 15
    assert cfg.owner_login == "rubinder"
    assert set(cfg.repos) == {"app", "marketing"}
    assert cfg.repos["app"].slug == "rubinder/wiz_scheduler"
    assert "backend/alembic/**" in cfg.protected["app"]
    assert "src/content/legal/**" in cfg.protected["marketing"]


def test_protected_hits_lists_only_protected_files():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    files = ["backend/routers/employees.py", "backend/alembic/versions/0060_x.py", "factory/state.py"]
    assert config.protected_hits("app", files, cfg) == ["backend/alembic/versions/0060_x.py", "factory/state.py"]


def test_every_spec_protected_path_is_listed():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for f in [
        "backend/services/billing.py", "backend/services/payroll_archive.py",
        "backend/routers/billing.py", "backend/routers/webhooks.py",
        "backend/legal/terms.txt", "backend/routers/gdpr.py", "terraform/ecs.tf",
        ".github/workflows/deploy.yml", "backend/routers/auth.py", "backend/dependencies.py",
        "backend/tenant.py", "backend/services/location_scope.py", ".claude/agents/x.md",
    ]:
        assert config.protected_hits("app", [f], cfg) == [f], f


def test_policy_protects_itself():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for p in ["factory/**", ".claude/**", ".github/**"]:
        assert p in cfg.protected["app"], p
    for p in [".github/**", ".claude/**"]:
        assert p in cfg.protected["marketing"], p


def test_widened_paths_are_protected():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    app_files = [
        "Dockerfile", "docker-compose.yml", "backend/requirements.txt", "requirements_seed.txt",
        "frontend/package.json", "frontend/package-lock.json", "backend/config.py",
        "backend/services/passwords.py", "backend/services/email_verification.py",
        "backend/services/plan.py", "backend/main.py", "backend/database.py",
        "backend/services/rate_limit.py", "backend/services/check_in_token.py",
        "backend/services/data_retention.py", "backend/services/signup_signals.py",
    ]
    for f in app_files:
        assert config.protected_hits("app", [f], cfg) == [f], f
    for f in ["package.json", "astro.config.mjs"]:
        assert config.protected_hits("marketing", [f], cfg) == [f], f


def test_matching_is_case_insensitive():
    assert globs.glob_match("Backend/Routers/AUTH.py", "backend/routers/auth.py")
    assert globs.glob_match("factory/state.py", "FACTORY/**")
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for f in ["FACTORY/state.py", ".Claude/settings.json", "Backend/Alembic/env.py", "dockerfile"]:
        assert config.protected_hits("app", [f], cfg) == [f], f


def test_malformed_pattern_matches_every_path():
    assert globs.glob_match("README.md", "docs/{a,b")
    assert globs.any_match("README.md", ["nope/**", "docs/{a,b"])
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    broken = config.Config(**{**cfg.__dict__, "protected": {"app": ["backend/{x"]}})
    assert config.protected_hits("app", ["README.md"], broken) == ["README.md"]


def test_unsafe_paths_are_protected():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for f in ["backend/../factory/state.py", "../x.md", "docs/..", "docs\\x.md",
              "docs/x\0.md", "/etc/passwd", "/README.md", ""]:
        assert config.protected_hits("app", [f], cfg) == [f], repr(f)
        assert config.protected_hits("marketing", [f], cfg) == [f], repr(f)
    # A dotted file name is not a `..` segment.
    assert config.protected_hits("app", ["docs/a..b.md", "docs/.hidden.md"], cfg) == []


def test_build_and_env_paths_are_protected():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for f in ["backend/alembic.ini", "Dockerfile.prod", "backend/Dockerfile", "frontend/Dockerfile.dev",
              ".env", "backend/.env.production", ".gitmodules", ".gitattributes",
              "frontend/vite.config.ts", "frontend/vite.config.js"]:
        assert config.protected_hits("app", [f], cfg) == [f], f
    for f in ["scripts/build.mjs", "scripts/a/b.sh", ".gitmodules", ".gitattributes", ".env", "src/.env.local"]:
        assert config.protected_hits("marketing", [f], cfg) == [f], f
    assert config.protected_hits("marketing", ["src/content/guides/scripts.md"], cfg) == []


def test_guide_boundary():
    g = "content/guide-2026-11-04-x"
    assert config.guide_boundary_ok("marketing", g, ["src/content/guides/en/a.md", "public/guides/a/b.png"])
    assert not config.guide_boundary_ok("marketing", g, ["src/content/guides/en/a.md", "src/views/Home.astro"])
    assert not config.guide_boundary_ok("app", g, ["src/content/guides/en/a.md"])
    assert not config.guide_boundary_ok("marketing", g, [])
    assert not config.guide_boundary_ok("marketing", g, ["src/content/guides/../../views/Home.astro"])
    assert not config.guide_boundary_ok("marketing", g, ["SRC/content/guides/a.md"])
    assert not config.guide_boundary_ok("marketing", g, ["src/content/guides"])
    # Non-guide branches are not constrained by it.
    assert config.guide_boundary_ok("app", "agent/issue-3-x", ["backend/x.py"])


def test_guide_boundary_allows_only_locale_markdown_and_raster_images():
    # The same name policy as the marketing repo's scripts/check-guide-boundary.mjs.
    g = "content/guide-2026-11-04-x"
    locales = ("en", "zh", "pcm")
    ok = ["src/content/guides/en/shift-swaps.md", "src/content/guides/zh/a1.md", "src/content/guides/pcm/a-b-c.md",
          "public/guides/shift-swaps/hero.png", "public/guides/a/b.jpg", "public/guides/a/b.jpeg",
          "public/guides/a/b.webp", "public/guides/a/b.avif", "public/guides/x/y-z/a-b.jpg",
          "public/guides/x/y/z/a1.png"]
    for f in ok:
        assert config.guide_outside("marketing", [f], locales) == [], f
    bad = ["src/content/guides/a.md",               # no locale directory
           "src/content/guides/fr/a.md",            # a locale the site does not have
           "src/content/guides/en/a/b.md",          # nested
           "src/content/guides/pt-BR/a.md",         # not a plain lowercase locale folder
           "src/content/guides/en/Shift-Swaps.md", "src/content/guides/en/shift_swaps.md", "src/content/guides/en/-a.md",
           "src/content/guides/en/a-.md", "src/content/guides/en/a--b.md", "src/content/guides/en/a.MD",
           "src/content/guides/en/a.mdx", "src/content/guides/en/a.html", "src/content/guides/en/a.astro",
           "src/content/guides/en/a.ts", "src/content/guides/EN/a.md", "src/content/guides/en/a.md.html",
           "src/content/guides/en/a.html.md", "src/content/guides/english/a.md", "src/content/guides/en/.hidden.md",
           "public/guides/a.png",                   # an image at the public/guides root
           "public/guides/a.jpg", "public/guides/A/b.png", "public/guides/a/B.png", "public/guides/a/b.PNG",
           "public/guides/a/b.Jpg", "public/guides/x/y_z/a-b.jpg", "public/guides/a/-b.png", "public/guides/a-/b.png",
           "public/guides/a/b.svg", "public/guides/a/b.png.svg", "public/guides/a/b.svg.png", "public/guides/a/b.html",
           "public/guides/a/b.js", "public/guides/.htaccess", "public/guides/.x/a.png", "public/guides/a/b.png/x.md",
           "public/guidesx/a/b.png", "public/guides/a/b c.png", "public/guides/a/\u00e9.png",
           "public/guides/a/\u0131.png", "src/content/guides/en/\u0131.md", "public/guides/a/b.\u0130\u0131ng"]
    for f in bad:
        assert config.guide_outside("marketing", [f], locales) == [f], f
        assert not config.guide_boundary_ok("marketing", g, ["src/content/guides/en/ok.md", f], locales), f
    # No LOCALES list (unreadable): every guide markdown file is outside.
    assert config.guide_outside("marketing", ["src/content/guides/en/a.md"], ()) == ["src/content/guides/en/a.md"]


def test_guide_regexes_are_strict_ascii():
    # A case-insensitive regex folds the dotless i (U+0131) onto `i`; the policy is lowercase ASCII only.
    for rx in (config._GUIDE_MD_RE, config._GUIDE_IMG_RE):
        assert not rx.flags & re.IGNORECASE
        assert "(?i" not in rx.pattern
    assert not config._GUIDE_IMG_RE.fullmatch("public/guides/a/b.av\u0131f")


def test_npm_and_build_config_files_are_protected():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for f in [".npmrc", "frontend/.npmrc", "backend/x/.npmrc", "frontend/postcss.config.js",
              "frontend/postcss.config.cjs", "frontend/tailwind.config.ts", "frontend/tailwind.config.js"]:
        assert config.protected_hits("app", [f], cfg) == [f], f
    for f in [".npmrc", "src/.npmrc", "postcss.config.cjs", "postcss.config.js", "tailwind.config.ts",
              "astro.config.mjs", "astro.config.ts"]:
        assert config.protected_hits("marketing", [f], cfg) == [f], f


def test_every_pytest_config_name_is_protected_at_any_depth():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for name in ["pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg", "conftest.py"]:
        for f in [name, f"tests/{name}", f"backend/a/b/{name}"]:
            assert config.protected_hits("app", [f], cfg) == [f], f


def test_non_ascii_paths_fail_closed():
    # Case-folding filesystems can alias a non-ASCII name onto a protected one.
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for f in ["tests/confte\u017ft.py", ".mcp.j\u017fon", "frontend/\u017fcripts/verify/axe.mjs", "docs/\u00fc.md",
              "docs/a\u200bb.md"]:
        assert config.unsafe_path(f), f
        assert config.protected_hits("app", [f], cfg) == [f], f
        assert config.protected_hits("marketing", [f], cfg) == [f], f
        assert config.guide_outside("marketing", [f], ("en",)) == [f], f
    assert not config.unsafe_path("docs/plain-ascii.md")


def test_read_locales_fails_closed(tmp_path: Path):
    f = tmp_path / "links.ts"
    assert config.read_locales(f) == ()
    f.write_text('export const LOCALES = ["en", "es", "pt-BR"] as const;\n')
    assert config.read_locales(f) == ("en", "es", "pt-BR")
    f.write_text("export const LOCALES = ['en', '../x'];\n")
    assert config.read_locales(f) == ()
    f.write_text("export const OTHER = []\n")
    assert config.read_locales(f) == ()


AGENT_INSTRUCTIONS = ["CLAUDE.md", "frontend/CLAUDE.md", "CLAUDE.local.md", "a/CLAUDE.local.md", "AGENTS.md",
                      "x/AGENTS.md", "GEMINI.md", "x/GEMINI.md", ".cursorrules", "x/.cursorrules",
                      ".cursor/rules/a.mdc", "x/.cursor/mcp.json", ".mcp.json", "x/.mcp.json",
                      ".claude/settings.json", ".claude/agents/x.md"]


def test_instruction_and_gate_code_files_are_protected():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    app = AGENT_INSTRUCTIONS + [
        "tests/conftest.py", "backend/conftest.py", "conftest.py", "pytest.ini", "backend/pytest.ini",
        "pyproject.toml", "backend/pyproject.toml", "setup.cfg", "tox.ini",
        "frontend/vitest.config.ts", "frontend/vitest.config.mjs", "frontend/vite.config.ts",
        "frontend/src/setupTests.ts", "frontend/tsconfig.json", "frontend/tsconfig.node.json",
        "frontend/scripts/verify/axe.mjs", "frontend/scripts/verify/contrast.mjs",
        ".github/workflows/deploy.yml", "backend/requirements.txt", "backend/requirements-dev.txt",
        "tests/test_architecture.py", "frontend/src/architecture.test.ts",
        "tests/test_tenant_filter_integration.py", "tests/test_rls_enforce_integration.py",
        "tests/test_body_id_ownership_integration.py", "tests/test_location_scope.py",
        "tests/test_location_scope_admin_routes_integration.py",
        "tests/test_location_scope_employee_routes_integration.py",
        "tests/test_location_scope_schedule_routes_integration.py",
        "tests/test_migrations.py", "tests/test_schema_7shifts_compat.py", "tests/test_schema_deputy_compat.py",
        "frontend/src/i18n/appStringsTranslated.test.ts", "frontend/src/i18n/landingTranslated.test.ts"]
    for f in app:
        assert config.protected_hits("app", [f], cfg) == [f], f
    marketing = AGENT_INSTRUCTIONS + [
        "scripts/check-guide-boundary.mjs", "scripts/anything.mjs", "tests/guide-boundary.test.ts",
        "tests/setup.ts", "tests/locale-parity.test.ts", "tests/logical-direction.test.ts", "vitest.config.ts", "vitest.config.mjs", "vite.config.ts", "tsconfig.json", "astro.config.mjs"]
    for f in marketing:
        assert config.protected_hits("marketing", [f], cfg) == [f], f
    # Ordinary tests and sources stay open to the factory.
    assert config.protected_hits("app", ["tests/test_employees.py", "frontend/src/pages/Team.tsx"], cfg) == []
    assert config.protected_hits("marketing", ["tests/home.test.ts", "src/content/guides/en/a.md"], cfg) == []


def test_control_characters_cannot_hide_a_protected_path():
    assert globs.glob_match("factory/a\nb.py", "factory/**")
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    for d in ["factory/", ".claude/", ".github/", "backend/alembic/"]:
        for f in [f"{d}a\nb.py", f"{d}a\rb.py", f"{d}x/\nevil.py"]:
            assert config.protected_hits("app", [f], cfg) == [f], repr(f)
    for f in ["docs/a\tb.md", "docs/a\x1fb.md", "docs/a\x7fb.md", "docs/a\nb.md"]:
        assert config.protected_hits("app", [f], cfg) == [f], repr(f)
        assert config.protected_hits("marketing", [f], cfg) == [f], repr(f)


def test_unknown_repo_protects_every_path():
    cfg = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
    assert config.protected_hits("other", ["README.md", "docs/x.md"], cfg) == ["README.md", "docs/x.md"]
    assert config.protected_hits("App", ["README.md"], cfg) == ["README.md"]


def _write_config(tmp_path: Path, repos: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text('notify_email = "a@b.c"\nowner_login = "rubinder"\n' + repos)
    return p


def test_load_config_rejects_unknown_or_missing_repos(tmp_path: Path):
    import pytest
    good = '[repos.app]\nslug = "a/b"\npath = "/x"\n[repos.marketing]\nslug = "a/c"\npath = "/y"\n'
    assert set(config.load_config(_write_config(tmp_path, good)).repos) == {"app", "marketing"}
    with pytest.raises(ValueError):
        config.load_config(_write_config(tmp_path, good + '[repos.docs]\nslug = "a/d"\npath = "/z"\n'))
    with pytest.raises(ValueError):
        config.load_config(_write_config(tmp_path, '[repos.app]\nslug = "a/b"\npath = "/x"\n'))


def test_load_config_rejects_protected_toml_without_both_repos(tmp_path: Path, monkeypatch):
    import pytest
    src = Path(__file__).resolve().parents[1] / "config.example.toml"
    for body in ['app = ["factory/**"]\n', 'marketing = [".github/**"]\n', 'app = "factory/**"\nmarketing = []\n']:
        bad = tmp_path / "protected.toml"
        bad.write_text(body)
        monkeypatch.setattr(config, "PROTECTED_FILE", bad)
        with pytest.raises(ValueError):
            config.load_config(src)
