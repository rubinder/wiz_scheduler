"""Factory configuration: ~/factory/config.toml plus the protected-path list.

The protected list lives in the repo (factory/protected.toml), not in the
owner's config file, so widening or narrowing it is a reviewed change.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from factory.globs import any_match

REPO_KEYS = ("app", "marketing")
FACTORY_HOME = Path(os.environ.get("FACTORY_HOME", str(Path.home() / "factory")))
PROTECTED_FILE = Path(__file__).resolve().parent / "protected.toml"


@dataclass(frozen=True)
class RepoConfig:
    key: str
    slug: str
    path: Path


@dataclass(frozen=True)
class Config:
    notify_email: str
    owner_login: str
    idle_minutes: int
    quiet_hours: tuple[int, int] | None
    digest_hour: int
    repos: dict[str, RepoConfig]
    protected: dict[str, list[str]]
    # The marketing site's LOCALES (src/lib/links.ts in the marketing clone);
    # empty when unreadable, which puts every guide file outside the boundary.
    guide_locales: tuple[str, ...] = ()


def load_config(path: Path | None = None) -> Config:
    path = path or FACTORY_HOME / "config.toml"
    raw = tomllib.loads(path.read_text())
    protected = tomllib.loads(PROTECTED_FILE.read_text())
    if not str(raw.get("owner_login") or "").strip():
        raise ValueError("owner_login must be set in the factory config")
    if set(raw.get("repos") or {}) != set(REPO_KEYS):
        raise ValueError(f"repos must be exactly {', '.join(REPO_KEYS)}")
    missing = [k for k in REPO_KEYS if not isinstance(protected.get(k), list)]
    if missing:
        raise ValueError(f"protected.toml lacks a list for {', '.join(missing)}")
    qh = raw.get("quiet_hours")
    return Config(
        notify_email=raw["notify_email"],
        owner_login=raw["owner_login"],
        idle_minutes=int(raw.get("idle_minutes", 15)),
        quiet_hours=(int(qh[0]), int(qh[1])) if qh else None,
        digest_hour=int(raw.get("digest_hour", 8)),
        repos={
            key: RepoConfig(key=key, slug=r["slug"], path=Path(os.path.expanduser(r["path"])))
            for key, r in raw["repos"].items()
        },
        protected={k: list(v) for k, v in protected.items()},
        guide_locales=read_locales(Path(os.path.expanduser(raw["repos"]["marketing"]["path"])) / "src" / "lib" / "links.ts"),
    )


_LOCALE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")


def read_locales(links_ts: Path) -> tuple[str, ...]:
    """``export const LOCALES = [...]`` from the marketing site, or () (fail closed)."""
    try:
        m = re.search(r"\bLOCALES\b[^=\n]*=\s*\[([^\]]*)\]", links_ts.read_text())
    except (OSError, UnicodeDecodeError):
        return ()
    if not m:
        return ()
    found = tuple(re.findall(r"[\"']([^\"']*)[\"']", m.group(1)))
    return found if found and all(_LOCALE_RE.fullmatch(x) for x in found) else ()


GUIDE_BRANCH_PREFIX = "content/guide-"
# A guide PR may touch only these files, and only in the marketing repo; the
# policy is the marketing repo's scripts/check-guide-boundary.mjs, verbatim:
# src/content/guides/<locale>/<slug>.md (no .mdx, .astro or .html, which carry
# markup or code), and raster images public/guides/<folder>/.../<name>.<ext>
# with at least one folder (no .svg, which can carry script). Every folder,
# slug and name is a lowercase hyphenated ASCII word and the extension is
# lowercase, so a double extension, a dotfile or a capital never matches.
# No IGNORECASE: it would fold the dotless i (U+0131) onto `i`.
_WORD = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_GUIDE_MD_RE = re.compile(rf"src/content/guides/([a-z]{{2,3}})/{_WORD}\.md")
_GUIDE_IMG_RE = re.compile(rf"public/guides/(?:{_WORD}/)+{_WORD}\.(?:png|jpg|jpeg|webp|avif)")


def _guide_file_ok(path: str, locales: Iterable[str] | None) -> bool:
    if _GUIDE_IMG_RE.fullmatch(path):
        return True
    m = _GUIDE_MD_RE.fullmatch(path)
    if not m:
        return False
    return locales is None or m.group(1) in set(locales)


def unsafe_path(path: str) -> bool:
    """A path no pattern can be trusted to classify: it always counts as protected.

    Any non-ASCII character fails closed: a case-folding filesystem can alias
    such a name onto a protected ASCII one.
    """
    return (not path or path.startswith("/") or "\\" in path or not path.isascii()
            or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path)
            or ".." in path.split("/"))


def protected_hits(repo: str, files: Iterable[str], cfg: Config) -> list[str]:
    if repo not in cfg.protected:
        return list(files)  # unknown repo: fail closed
    patterns = cfg.protected[repo]
    return [f for f in files if unsafe_path(f) or any_match(f, patterns)]


def guide_outside(repo: str, files: Iterable[str], locales: Iterable[str] | None = None) -> list[str]:
    """The files a guide PR may not touch: all of them outside marketing.

    ``locales`` is the site's LOCALES (``Config.guide_locales``); the merge-time
    check always passes it. Without it only the locale's shape is checked.
    """
    locales = None if locales is None else tuple(locales)
    return [f for f in files if repo != "marketing" or unsafe_path(f) or not _guide_file_ok(f, locales)]


def guide_boundary_ok(repo: str, branch: str, files: Iterable[str], locales: Iterable[str] | None = None) -> bool:
    """False for a guide PR that is outside the marketing repo or touches anything but guides.

    PRs on other branches are not guide PRs and always pass. `files` must
    include rename sources (previous_filename). Directory names are
    case-sensitive on purpose: an allowlist that folded case would admit
    `SRC/content/guides/...`.
    """
    if not branch.startswith(GUIDE_BRANCH_PREFIX):
        return True
    files = list(files)
    return repo == "marketing" and bool(files) and not guide_outside(repo, files, locales)
