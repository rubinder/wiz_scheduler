"""Post-deploy smoke check for a merged factory PR.

Run after the deploy workflow for the merge commit finished. Exit 0 = pass.
"""
from __future__ import annotations

import argparse
import ast
import http.client
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_BASE = "https://app.wizscheduler.com"
SITE_BASE = "https://wizscheduler.com"
# /health is served by CloudFront as the SPA (text/html), so the API liveness
# probe is the unauthenticated /auth/me, which must answer 401.
# 429 (rate limited) means the route answered: reachable, never a revert.
APP_LIVENESS = [("/api/v1/auth/me", {401, 429}), ("/", {200, 429}), ("/login", {200, 429})]
# A probed GET with no auth may also answer 422 (required query parameters):
# the route exists and the app is serving it.
OK_AUTH = {200, 401, 403, 422, 429}
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")
_ROUTER_RE = re.compile(r"backend/routers/[A-Za-z0-9_]+\.py")


def valid_sha(sha: str) -> bool:
    return _SHA_RE.fullmatch(sha) is not None


def valid_router_path(path: str) -> bool:
    return _ROUTER_RE.fullmatch(path) is not None


def app_routes(changed: dict[str, str]) -> list[tuple[str, set[int]]]:
    out: list[tuple[str, set[int]]] = []
    for path, text in sorted(changed.items()):
        if not valid_router_path(path):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            print(f"SKIP {path}: does not parse")
            continue
        prefix = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter":
                for kw in node.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "get"
                        and dec.args and isinstance(dec.args[0], ast.Constant) and "{" not in dec.args[0].value):
                    out.append((f"/api/v1{prefix}{dec.args[0].value}", OK_AUTH))
    return out


def marketing_urls(files: list[str], locales: list[str], all_locale_paths: list[str]) -> list[tuple[str, str]]:
    def loc(locale: str, path: str) -> str:
        if locale == "en":
            return path
        return f"/{locale}" if path == "/" else f"/{locale}{path}"

    urls: list[tuple[str, str]] = [("/", "en"), ("/features", "en")]
    for f in files:
        m = re.fullmatch(r"src/content/guides/([a-z]+)/([a-z0-9-]+)\.md", f)
        if m:
            urls.append((loc(m.group(1), f"/guides/{m.group(2)}"), m.group(1)))
            continue
        m = re.fullmatch(r"src/pages/(.+)\.astro", f)
        if not m:
            continue
        route = m.group(1)
        if route.startswith("[locale]/"):
            page = "/" + route.removeprefix("[locale]/").removesuffix("index").rstrip("/")
            page = "/" if page in {"/", ""} else page
            if page in all_locale_paths:
                urls += [(loc(l, page), l) for l in locales if l != "en"]
            continue
        parts = route.split("/")
        locale = parts[0] if parts[0] in locales and len(parts) > 1 else "en"
        path = "/" + "/".join(parts).removesuffix("index").rstrip("/")
        if "[" in path or path.endswith("/404"):
            continue
        urls.append((path or "/", locale))
    seen: set[tuple[str, str]] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def check_page(html: str, locale: str, url: str, rtl: set[str]) -> list[str]:
    problems: list[str] = []
    m = re.search(r"<html[^>]*\blang=\"([^\"]+)\"", html)
    if not m or not m.group(1).lower().startswith(locale.lower()):
        problems.append(f"{url}: lang is {m.group(1) if m else 'missing'}, expected {locale}")
    want_dir = "rtl" if locale in rtl else "ltr"
    d = re.search(r"<html[^>]*\bdir=\"([^\"]+)\"", html)
    if (d.group(1) if d else "ltr") != want_dir:
        problems.append(f"{url}: dir is {d.group(1) if d else 'missing'}, expected {want_dir}")
    if not re.search(rf"<link[^>]*rel=\"canonical\"[^>]*href=\"{re.escape(SITE_BASE + (url if url != '/' else '/'))}/?\"", html):
        problems.append(f"{url}: canonical missing or wrong")
    return problems


class Unreachable(Exception):
    """Production gave no usable answer (DNS, TLS, timeout, truncated or malformed HTTP): unknown, never a revert."""


def fetch(url: str, tries: int = 3) -> tuple[int, str]:
    """(status, body). Raises Unreachable when no attempt got any HTTP answer."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wizfactory-smoke"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries - 1:
                return e.code, ""
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
            # HTTPException covers a cut-off body (IncompleteRead) and a garbled status line.
            if attempt == tries - 1:
                raise Unreachable(f"{url}: {type(e).__name__}") from e
        time.sleep(20)
    raise Unreachable(url)


def _gh(argv: list[str]) -> str:
    return subprocess.run(["gh", *argv], capture_output=True, text=True, check=True).stdout


def _json_stream(text: str) -> list[dict]:
    """Parse the concatenated JSON arrays `gh api --paginate` prints."""
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        page, i = dec.raw_decode(text, i)
        out += page
    return out


def _pr_files(slug: str, pr: int) -> list[str]:
    """Paths present after the PR: removed files dropped; a rename lists only its new path."""
    entries = _json_stream(_gh(["api", f"repos/{slug}/pulls/{pr}/files", "--paginate"]))
    return [e["filename"] for e in entries if e["status"] != "removed"]


def _fetch_router(slug: str, path: str, sha: str) -> str | None:
    try:
        return _gh(["api", f"repos/{slug}/contents/{path}?ref={sha}", "-H", "Accept: application/vnd.github.raw"])
    except subprocess.CalledProcessError as e:
        if "404" in (e.stderr or ""):
            print(f"SKIP {path}: not found at {sha}")
            return None
        raise


def run_app(slug: str, pr: int, sha: str) -> list[str]:
    if not valid_sha(sha):
        raise ValueError(f"invalid sha {sha!r}")
    changed: dict[str, str] = {}
    for f in _pr_files(slug, pr):
        if valid_router_path(f):
            text = _fetch_router(slug, f, sha)
            if text is not None:
                changed[f] = text
    failures = []
    for path, ok in APP_LIVENESS + app_routes(changed):
        code, _ = fetch(APP_BASE + path)
        print(f"{code} {path}")
        if code not in ok:
            failures.append(f"{path} returned {code}, expected one of {sorted(ok)}")
    return failures


def run_marketing(slug: str, pr: int, locales: list[str], all_paths: list[str], rtl: set[str]) -> list[str]:
    files = _pr_files(slug, pr)
    failures = []
    for path, locale in marketing_urls(files, locales, all_paths):
        code, html = fetch(SITE_BASE + path)
        print(f"{code} {path}")
        if code != 200:
            failures.append(f"{path} returned {code}")
        else:
            failures += check_page(html, locale, path, rtl)
    return failures


def _marketing_locales(clone: str) -> tuple[list[str], list[str], set[str]]:
    with open(f"{clone}/src/lib/links.ts") as fh:
        text = fh.read()

    def grab(name: str) -> list[str]:
        m = re.search(rf"\b{name}\b[^=\n]*=\s*\[([^\]]*)\]", text)
        if not m:
            raise ValueError(f"{name} not found in links.ts")
        return re.findall(r'"([^"]+)"', m.group(1))

    return grab("LOCALES"), grab("ALL_LOCALE_PATHS"), set(grab("RTL_LOCALES"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", choices=["app", "marketing"], required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    from factory.config import load_config
    try:
        cfg = load_config()
        if a.repo == "app":
            failures = run_app(cfg.repos["app"].slug, a.pr, a.sha)
        else:
            locales, paths, rtl = _marketing_locales(str(cfg.repos["marketing"].path))
            failures = run_marketing(cfg.repos["marketing"].slug, a.pr, locales, paths, rtl)
    except (Unreachable, subprocess.CalledProcessError, OSError, ValueError, SyntaxError, KeyError) as e:
        # json.JSONDecodeError is a ValueError. Exit 1 means production answered wrongly; this is 2.
        print(f"UNKNOWN {type(e).__name__}: {e}")
        return 2
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
