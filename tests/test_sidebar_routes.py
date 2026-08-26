"""Every sidebar link resolves to a real route.

A nav refactor's characteristic failure is a link pointing at a route that
does not exist. Both sides are plain strings, so TypeScript cannot catch it
and the frontend has no test runner — this is the only automated guard.

Parses the source rather than rendering: App.tsx nests child paths under a
parent `<Route path="/manager">` / `"/employee"`, so a child `path="roles"`
means `/manager/roles`.
"""

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
_SIDEBAR = _SRC / "components" / "layout" / "Sidebar.tsx"
_APP = _SRC / "App.tsx"


def _nav_targets() -> set[str]:
    return set(re.findall(r'to:\s*"([^"]+)"', _SIDEBAR.read_text()))


def _routes() -> set[str]:
    routes: set[str] = set()
    parent = ""
    for line in _APP.read_text().splitlines():
        absolute = re.search(r'<Route\s+path="(/[^"]+)"', line)
        if absolute:
            path = absolute.group(1).rstrip("/")
            routes.add(path)
            parent = path
            continue
        relative = re.search(r'<Route\s+path="([^/"][^"]*)"', line)
        if relative:
            routes.add(f"{parent}/{relative.group(1)}")
    return routes


def test_every_sidebar_link_has_a_route():
    missing = sorted(_nav_targets() - _routes())
    assert not missing, (
        f"sidebar links with no matching route in App.tsx: {missing}. "
        "Either the route was renamed or the nav entry has a typo."
    )


def test_the_parser_still_finds_both_sides():
    """Guard the guard: a refactor that changes the shape of either file
    could silently reduce both sets to empty, making the test above pass
    vacuously."""
    assert len(_nav_targets()) >= 20, "parsed too few nav targets — parser is stale"
    assert len(_routes()) >= 20, "parsed too few routes — parser is stale"
