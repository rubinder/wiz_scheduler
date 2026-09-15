# Slugify Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/utils/slug.py` exposing one pure function, `slugify(text: str, max_len: int = 30) -> str`, that turns arbitrary text into a URL- and filename-safe token, plus `tests/test_slug.py` covering every rule.

**Architecture:** A standard-library-only module beside `backend/utils/email_normalize.py`, `id_gen.py` and `privacy.py`, with the same shape: module docstring, two private module-level constants, one public function. No caller is wired in — `Company.slug` keeps being minted by `secrets.token_hex(3)` in the auth and ownership-group routers. Tests are plain `pytest` with parametrize tables, no fixtures, no database.

**Tech Stack:** Python 3.11, `re` from the standard library, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-issue-109-add-a-slugify-helper-design.md`

## Global Constraints

- Only two files change in the whole plan: create `backend/utils/slug.py` and create `tests/test_slug.py`. No model, migration, router, service, `src/api/` wrapper, component, or frontend file is touched. No file under `terraform/` or `.github/` is touched.
- No new dependencies. `slug.py` imports only `re` from the standard library. No `unidecode`, no `python-slugify`, no `unicodedata` transliteration pass.
- Output alphabet is exactly `[a-z0-9]` and single `-`; no dash at either end; `len(result) <= max_len` for every input, with one exception: the fallback `"item"` is returned verbatim even when `max_len < 4`.
- `slugify` never raises for any `str` input and any `int` `max_len`. Zero, negative, whitespace-only, punctuation-only, all-non-ASCII, and empty inputs all resolve to `"item"`. `None` or non-`str` input is not handled (the signature is the contract; let `AttributeError` surface).
- No transliteration: `"café au lait"` → `"caf-au-lait"`, `"日本語"` → `"item"`.
- Truncation is a plain character cut at `max_len` followed by `rstrip("-")`; it is not word-boundary aware.
- Idempotence `slugify(slugify(x, n), n) == slugify(x, n)` must hold for every `n >= 4`; at `n < 4` the fallback is allowed to not round-trip (`slugify("", 2) == "item"` but `slugify("item", 2) == "it"`).
- "Today" is UTC per CLAUDE.md; neither new file calls `date.today` or `datetime.now`, so `tests/test_utc_today.py` (an AST sweep of `tests/`) passes untouched.
- Backend test command, exact string, run after every change: `cd /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 && /Users/robran/IdeaProjects/wiz_scheduler/backend/.venv/bin/python -m pytest tests/ -x -q`. Never run tests in the main checkout `/Users/robran/IdeaProjects/wiz_scheduler`.
- Every commit runs from the worktree via `git -C /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 ...`. Never push to `main`, never force-push, never merge.

## Deviation from the spec, decided here

The spec's five-step body truncates with `text[:max_len]` and says a zero **or negative** `max_len` resolves to `"item"` because `text[:0]` is `""`. That is only true for zero. In Python `"hello"[:-1]` is `"hell"` — a negative slice bound cuts from the end — so a literal `[:max_len]` would return `"hell"` for `max_len=-1`, violating the spec's own error-handling contract ("non-positive `max_len` all resolve to `"item"`"). The plan keeps the stated behaviour and fixes the mechanism: the cut is `slug[:max(max_len, 0)]`. This adds no exception path and no second failure mode, which is what the spec was protecting.

---

### Task 1: `slugify` module with rule tests

**Files:**
- Create: `backend/utils/slug.py`
- Create: `tests/test_slug.py`

**Interfaces:**
- Consumes: nothing from the codebase. `backend/utils/__init__.py` already exists, so `from backend.utils.slug import slugify` resolves once the file exists (tests import `backend.utils.email_normalize` the same way).
- Produces: `slugify(text: str, max_len: int = 30) -> str` in `backend/utils/slug.py`, plus private constants `_NON_ALNUM: re.Pattern[str]` and `_FALLBACK: str = "item"`. Task 2 imports `slugify` from this exact path and appends tests to this exact test file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_slug.py` with exactly this content:

```python
"""slugify is a pure string transform: lowercase, [a-z0-9] and single dashes,
no edge dashes, capped at max_len, never empty."""

import pytest

from backend.utils.slug import slugify


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Case never survives; runs of anything else collapse to one dash.
        ("Hello World", "hello-world"),
        ("Hello   World!!", "hello-world"),
        ("foo_bar.baz", "foo-bar-baz"),
        ("a---b", "a-b"),
        # No transliteration: non-ASCII letters become a dash, not a letter.
        ("café au lait", "caf-au-lait"),
        ("日本語", "item"),
    ],
)
def test_lowercases_and_collapses(raw: str, expected: str):
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["  -hello-  ", "!!!hello!!!", "-hello", "hello-"],
)
def test_edge_dashes_stripped(raw: str):
    assert slugify(raw) == "hello"


@pytest.mark.parametrize(
    "raw,max_len,expected",
    [
        # The cut lands on the dash and the dash is removed.
        ("hello world", 6, "hello"),
        ("hello world", 5, "hello"),
        ("hello world", 7, "hello-w"),
        # Exactly at and above the natural length: unchanged.
        ("hello world", 11, "hello-world"),
        ("hello world", 100, "hello-world"),
        # Leading junk is stripped before the cut, so it does not eat the budget.
        ("   hello", 5, "hello"),
    ],
)
def test_truncates_without_trailing_dash(raw: str, max_len: int, expected: str):
    result = slugify(raw, max_len=max_len)
    assert result == expected
    assert len(result) <= max_len


def test_default_max_len_is_30():
    assert slugify("a" * 40) == "a" * 30


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "---", "!!!", "日本語"],
)
def test_empty_falls_back_to_item(raw: str):
    """Never return an empty string."""
    assert slugify(raw) == "item"


@pytest.mark.parametrize("max_len", [0, -1])
def test_non_positive_max_len_falls_back_to_item(max_len: int):
    """A zero or negative budget leaves nothing, so the fallback fires. No
    ValueError: a helper this small should not have two failure modes."""
    assert slugify("hello", max_len=max_len) == "item"


def test_fallback_ignores_max_len():
    """"item" is four characters and is returned verbatim even under a
    shorter budget; a fallback that could shrink to "" defeats its purpose."""
    assert slugify("", max_len=2) == "item"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 && /Users/robran/IdeaProjects/wiz_scheduler/backend/.venv/bin/python -m pytest tests/test_slug.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'backend.utils.slug'`.

- [ ] **Step 3: Write the implementation**

Create `backend/utils/slug.py` with exactly this content:

```python
"""Turn free text into a URL- and filename-safe slug.

A pure function with no I/O. It knows nothing about the database, so two
inputs can produce the same slug — disambiguation is the caller's job. It
does not transliterate: accented and non-Latin letters are dropped, not
folded to ASCII, so ``"café"`` becomes ``"caf"``.

Nothing calls this yet. ``Company.slug`` is still minted by
``secrets.token_hex(3)`` in the auth and ownership-group routers.
"""
from __future__ import annotations

import re

# Anything that is not a lowercase ASCII letter or digit. The input is
# lowercased first, so the class does not need A-Z.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Returned when nothing survives the transform. Not subject to max_len.
_FALLBACK = "item"


def slugify(text: str, max_len: int = 30) -> str:
    """Return *text* as lowercase ``[a-z0-9]`` and single dashes, with no
    dash at either end, at most *max_len* characters long, and never empty.

    Every run of characters outside ``[a-z0-9]`` — whitespace, punctuation,
    underscores, non-ASCII letters — collapses to one ``-``. Edge dashes are
    stripped before truncating, so leading junk never eats the budget.
    Truncation is a plain character cut; it may split a word.

    If nothing survives (empty, whitespace-only, punctuation-only,
    all-non-ASCII input, or a *max_len* of zero or less) the result is
    ``"item"``. That fallback is returned verbatim even when *max_len* is
    shorter than four, because a fallback that could shrink to ``""`` would
    defeat its purpose. Consequently ``slugify(slugify(x, n), n) ==
    slugify(x, n)`` holds for every ``n >= 4`` but not below it:
    ``slugify("", 2)`` is ``"item"`` while ``slugify("item", 2)`` is ``"it"``.

    Never raises for any ``str`` *text* and any ``int`` *max_len*. Non-``str``
    input is not handled; ``None`` raises ``AttributeError`` at ``.lower()``.
    """
    slug = _NON_ALNUM.sub("-", text.lower()).strip("-")
    # A negative slice bound would cut from the end ("hello"[:-1] == "hell"),
    # so clamp: a non-positive budget means nothing fits and the fallback fires.
    # Only a trailing dash can be exposed by the cut — the leading edge was
    # already cleaned above — so rstrip, not strip.
    slug = slug[: max(max_len, 0)].rstrip("-")
    return slug or _FALLBACK
```

- [ ] **Step 4: Run the new test file to verify it passes**

Run:

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 && /Users/robran/IdeaProjects/wiz_scheduler/backend/.venv/bin/python -m pytest tests/test_slug.py -v
```

Expected: 25 passed (6 + 4 + 6 + 1 + 5 + 2 + 1), 0 failed.

- [ ] **Step 5: Run the full backend suite**

Run:

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 && /Users/robran/IdeaProjects/wiz_scheduler/backend/.venv/bin/python -m pytest tests/ -x -q
```

Expected: all passed, 0 failed. `tests/test_utc_today.py` sweeps `tests/` for `date.today` / `datetime.now` and must stay green — `tests/test_slug.py` calls neither.

- [ ] **Step 6: Commit**

```bash
git -C /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 add backend/utils/slug.py tests/test_slug.py
git -C /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 commit -m "feat(utils): add slugify helper (#109)"
```

---

### Task 2: Property tests — idempotence, the fallback exception, distinctness

**Files:**
- Modify: `tests/test_slug.py` (append to the end of the file created in Task 1)

**Interfaces:**
- Consumes: `slugify(text: str, max_len: int = 30) -> str` from `backend/utils/slug.py`, already imported at the top of `tests/test_slug.py` as `from backend.utils.slug import slugify`.
- Produces: nothing new for later tasks; this is the last task.

- [ ] **Step 1: Write the property tests**

Append exactly this to the end of `tests/test_slug.py`:

```python
# Every input from the tables above, so the property is checked against the
# same corpus that pins the individual rules.
_CORPUS = [
    "Hello World",
    "Hello   World!!",
    "foo_bar.baz",
    "a---b",
    "café au lait",
    "日本語",
    "  -hello-  ",
    "!!!hello!!!",
    "hello world",
    "   hello",
    "",
    "   ",
    "---",
    "!!!",
    "a" * 40,
]


@pytest.mark.parametrize("max_len", [4, 6, 30])
@pytest.mark.parametrize("raw", _CORPUS)
def test_idempotent_at_max_len_of_four_or_more(raw: str, max_len: int):
    """A valid slug passes through every step unchanged, so re-slugging a
    stored value is a no-op. This is the property a future caller leans on."""
    once = slugify(raw, max_len)
    assert slugify(once, max_len) == once


def test_fallback_does_not_round_trip_under_a_short_budget():
    """The one documented exception to idempotence: "item" is longer than a
    budget below four, so it is the only output that does not round-trip."""
    assert slugify("", 2) == "item"
    assert slugify("item", 2) == "it"


def test_distinct_inputs_stay_distinct():
    """Guard against a regex that over-collapses."""
    assert slugify("a") != slugify("b")
```

- [ ] **Step 2: Run the new tests to verify they pass against the Task 1 implementation**

These are property tests over an implementation that already exists, so they are expected to pass on first run. If any fails, the implementation in `backend/utils/slug.py` is wrong, not the test — the spec pins idempotence for every `n >= 4` and the `n < 4` exception explicitly.

Run:

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 && /Users/robran/IdeaProjects/wiz_scheduler/backend/.venv/bin/python -m pytest tests/test_slug.py -v
```

Expected: 72 passed (25 from Task 1 + 45 idempotence cases + 1 + 1), 0 failed.

- [ ] **Step 3: Confirm nothing else in the repository changed**

Run:

```bash
git -C /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 status --short
```

Expected: exactly one line, ` M tests/test_slug.py`. If any other path appears, revert it — the spec says nothing else in the repository changes.

- [ ] **Step 4: Run the full backend suite**

Run:

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 && /Users/robran/IdeaProjects/wiz_scheduler/backend/.venv/bin/python -m pytest tests/ -x -q
```

Expected: all passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git -C /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 add tests/test_slug.py
git -C /Users/robran/IdeaProjects/wiz_scheduler/.claude/worktrees/issue-109 commit -m "test(utils): pin slugify idempotence and fallback exception (#109)"
```

---

## Self-review against the spec

- **Placement, imports, shape** (`backend/utils/slug.py`, only `re`, docstring + two private constants + one function): Task 1 Step 3.
- **Five-step body in order** (lower, collapse, strip, cut + rstrip, fallback): Task 1 Step 3, with the negative-`max_len` clamp documented under "Deviation from the spec".
- **Lowercase and collapse table**, including `"café au lait"` and `"日本語"`: Task 1 `test_lowercases_and_collapses`.
- **Edge dashes stripped**: Task 1 `test_edge_dashes_stripped`.
- **Truncation without trailing dash, `len <= max_len` asserted across a table**: Task 1 `test_truncates_without_trailing_dash`.
- **Default `max_len` — 40 chars in, exactly 30 out**: Task 1 `test_default_max_len_is_30`.
- **Empty falls back to `"item"`, including non-empty input with `max_len=0`**: Task 1 `test_empty_falls_back_to_item` and `test_non_positive_max_len_falls_back_to_item`.
- **Fallback ignores `max_len`**: Task 1 `test_fallback_ignores_max_len`.
- **Idempotent at `n in {4, 6, 30}` over the tables' inputs, plus the documented `n < 4` exception**: Task 2 `test_idempotent_at_max_len_of_four_or_more` and `test_fallback_does_not_round_trip_under_a_short_budget`.
- **Distinct inputs stay distinct**: Task 2 `test_distinct_inputs_stay_distinct`.
- **Docstring states the fallback-ignores-`max_len` rule and both the idempotence property and its exception**: Task 1 Step 3 docstring.
- **No frontend, schema, router or migration changes; no new dependencies**: Global Constraints; Task 2 Step 3 verifies with `git status`.
