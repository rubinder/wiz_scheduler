from factory import smoke

ROUTER = '''
from fastapi import APIRouter
router = APIRouter(prefix="/employees", tags=["employees"])

@router.get("")
async def list_employees(): ...

@router.get("/{employee_id}")
async def get_one(employee_id: str): ...

@router.post("/import")
async def imp(): ...

@router.get("/export/csv")
async def export(): ...
'''


def test_app_routes_parameterless_gets_only():
    routes = smoke.app_routes({"backend/routers/employees.py": ROUTER})
    assert routes == [("/api/v1/employees", {200, 401, 403, 422, 429}), ("/api/v1/employees/export/csv", {200, 401, 403, 422, 429})]


def test_app_routes_ignores_non_router_files():
    assert smoke.app_routes({"backend/services/x.py": ROUTER}) == []


LOCALES = ["en", "es", "ar"]
ALL = ["/", "/features"]


def test_marketing_urls_for_pages_and_guides():
    files = ["src/pages/pricing.astro", "src/pages/[locale]/features.astro",
             "src/content/guides/en/shift-swaps.md", "src/content/guides/ar/shift-swaps.md",
             "src/pages/es/terms.astro", "src/views/Home.astro"]
    urls = smoke.marketing_urls(files, LOCALES, ALL)
    assert ("/pricing", "en") in urls
    assert ("/es/features", "es") in urls and ("/ar/features", "ar") in urls
    assert ("/guides/shift-swaps", "en") in urls and ("/ar/guides/shift-swaps", "ar") in urls
    assert ("/es/terms", "es") in urls
    assert ("/", "en") in urls and ("/features", "en") in urls  # always checked


def test_check_page_flags_wrong_dir_and_missing_canonical():
    html = '<html lang="ar" dir="ltr"><head></head></html>'
    problems = smoke.check_page(html, "ar", "/ar/guides/x", {"ar", "ur"})
    assert any("dir" in p for p in problems) and any("canonical" in p for p in problems)


def test_check_page_passes_good_page():
    html = '<html lang="ar" dir="rtl"><head><link rel="canonical" href="https://wizscheduler.com/ar/guides/x"></head></html>'
    assert smoke.check_page(html, "ar", "/ar/guides/x", {"ar", "ur"}) == []


def test_valid_sha_and_router_path():
    assert smoke.valid_sha("bed9bfa7") and smoke.valid_sha("a" * 40)
    assert not smoke.valid_sha("abc") and not smoke.valid_sha("a" * 41)
    assert not smoke.valid_sha("main") and not smoke.valid_sha("abcdef1; rm")
    assert smoke.valid_router_path("backend/routers/employees.py")
    assert not smoke.valid_router_path("backend/routers/../../x.py")
    assert not smoke.valid_router_path("backend/routers/sub/x.py")
    assert not smoke.valid_router_path("backend/routers/a-b.py")
    assert not smoke.valid_router_path("backend/routers/x.py?ref=evil")


def test_marketing_locales_parses_typed_ts(tmp_path):
    (tmp_path / "src/lib").mkdir(parents=True)
    (tmp_path / "src/lib/links.ts").write_text(
        'export const LOCALES = [\n  "en",\n  "es",\n  "ar",\n] as const;\n'
        'export const SITE_LOCALES = ["en", "es"] as const;\n'
        'export const RTL_LOCALES: readonly Locale[] = ["ar", "ur"];\n'
        'export const ALL_LOCALE_PATHS = ["/", "/features"] as const;\n')
    assert smoke._marketing_locales(str(tmp_path)) == (["en", "es", "ar"], ["/", "/features"], {"ar", "ur"})


def test_locale_page_outside_all_paths_yields_no_locale_urls():
    urls = smoke.marketing_urls(["src/pages/[locale]/pricing.astro"], LOCALES, ALL)
    assert not any(u[0].endswith("/pricing") and u[1] != "en" for u in urls)
    assert ("/es/pricing", "es") not in urls


def test_app_routes_skips_unparseable_router(capsys):
    assert smoke.app_routes({"backend/routers/bad.py": "def (:"}) == []
    assert "SKIP backend/routers/bad.py" in capsys.readouterr().out


def _stub_cfg(monkeypatch):
    from types import SimpleNamespace as NS
    import factory.config as config
    monkeypatch.setattr(config, "load_config", lambda: NS(repos={
        "app": NS(slug="o/app", path="/x"), "marketing": NS(slug="o/m", path="/y")}))


def test_main_infrastructure_error_exits_2(monkeypatch, capsys):
    import subprocess
    _stub_cfg(monkeypatch)

    def boom(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="boom")
    monkeypatch.setattr(smoke, "_gh", boom)
    assert smoke.main(["--repo", "app", "--pr", "1", "--sha", "abcdef1"]) == 2
    assert "UNKNOWN" in capsys.readouterr().out


def test_main_invalid_sha_exits_2(monkeypatch):
    _stub_cfg(monkeypatch)
    assert smoke.main(["--repo", "app", "--pr", "1", "--sha", "nope; rm"]) == 2


def test_main_bad_json_exits_2(monkeypatch):
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(smoke, "_gh", lambda argv: "not json")
    assert smoke.main(["--repo", "app", "--pr", "1", "--sha", "abcdef1"]) == 2


def test_main_wrong_production_answer_exits_1(monkeypatch):
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(smoke, "_gh", lambda argv: "[]")
    monkeypatch.setattr(smoke, "fetch", lambda url: (500, ""))
    assert smoke.main(["--repo", "app", "--pr", "1", "--sha", "abcdef1"]) == 1


def test_run_app_skips_removed_and_missing_router_files(monkeypatch, capsys):
    import json, subprocess
    listing = json.dumps([
        {"filename": "backend/routers/gone.py", "status": "removed"},
        {"filename": "backend/routers/new_name.py", "status": "renamed", "previous_filename": "backend/routers/old.py"},
        {"filename": "backend/routers/ok.py", "status": "modified"},
    ])
    fetched: list[str] = []

    def gh(argv):
        if argv[1].endswith("/files"):
            return listing
        path = argv[1].split("/contents/")[1].split("?")[0]
        fetched.append(path)
        if path.endswith("new_name.py"):
            raise subprocess.CalledProcessError(1, argv, stderr="gh: Not Found (HTTP 404)")
        return 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/ok")\n@router.get("/ping")\ndef p(): ...\n'
    monkeypatch.setattr(smoke, "_gh", gh)
    seen: list[str] = []
    monkeypatch.setattr(smoke, "fetch", lambda url: (seen.append(url) or (401, "")))
    failures = smoke.run_app("o/app", 1, "abcdef1")
    assert "backend/routers/gone.py" not in fetched and "backend/routers/old.py" not in fetched
    assert smoke.APP_BASE + "/api/v1/ok/ping" in seen
    assert [f for f in failures if "ok/ping" in f] == []


def test_unreachable_production_is_unknown_not_a_revert(monkeypatch, capsys):
    import urllib.error
    import urllib.request
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(smoke, "_gh", lambda argv: "[]")
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)

    def down(req, timeout=0):
        raise urllib.error.URLError("nodename nor servname provided")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    for repo in ("app", "marketing"):
        if repo == "marketing":
            monkeypatch.setattr(smoke, "_marketing_locales", lambda clone: (["en"], ["/"], set()))
        assert smoke.main(["--repo", repo, "--pr", "1", "--sha", "abcdef1"]) == 2, repo
        out = capsys.readouterr().out
        assert "UNKNOWN Unreachable" in out and "FAIL " not in out, repo


def test_rate_limited_or_unprocessable_probe_is_reachable(monkeypatch):
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(smoke, "_gh", lambda argv: "[]")
    monkeypatch.setattr(smoke, "fetch", lambda url: (429, ""))
    assert smoke.main(["--repo", "app", "--pr", "1", "--sha", "abcdef1"]) == 0
    routes = smoke.app_routes({"backend/routers/employees.py": ROUTER})
    assert all({422, 429} <= ok for _, ok in routes)


def test_truncated_or_malformed_http_answer_is_unknown_not_a_revert(monkeypatch, capsys):
    import http.client
    import urllib.request
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(smoke, "_gh", lambda argv: "[]")
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)

    class Cut:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"<html", 100)

    for err in ("incomplete", "badstatus"):
        def opener(req, timeout=0, err=err):
            if err == "badstatus":
                raise http.client.BadStatusLine("garbage")
            return Cut()
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        monkeypatch.setattr(smoke, "_marketing_locales", lambda clone: (["en"], ["/"], set()))
        assert smoke.main(["--repo", "marketing", "--pr", "1", "--sha", "abcdef1"]) == 2, err
        out = capsys.readouterr().out
        assert "UNKNOWN Unreachable" in out and "FAIL " not in out, err
