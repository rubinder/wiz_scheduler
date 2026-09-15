# Slugify Helper — Design Spec

Issue: [#109](https://github.com/rubinder/wiz_scheduler/issues/109)
Status: approved, not yet implemented

A smoke test for the agent system. The point is a small, self-contained
change that exercises triage, spec, plan, implementation and review end to
end without touching the schema, the routers, or the UI. The spec is
correspondingly short, but every choice the issue left open is made here so
the implementer has nothing to guess.

## Goal

Add `backend/utils/slug.py` exposing one pure function,
`slugify(text: str, max_len: int = 30) -> str`, that turns arbitrary text
into a URL- and filename-safe token: lowercase, `[a-z0-9]` and single
dashes only, no dash at either end, no longer than `max_len`, and never
empty. Add `tests/test_slug.py` covering each rule. Nothing else in the
repository changes.

## Non-goals

- **Wiring it in.** `Company.slug` is minted today by `secrets.token_hex(3)`
  in `backend/routers/auth.py` and `backend/routers/ownership_group.py`, and
  the check-in QR payload signs over that slug. This issue does not change
  how any slug is generated or stored. Replacing random company slugs with
  name-derived ones would need a uniqueness story and a migration, and is a
  separate decision.
- **Transliteration.** `café` becomes `caf`, not `cafe`. Folding accented
  and non-Latin letters to ASCII needs either a dependency (`unidecode`,
  `python-slugify`) or a hand-rolled NFKD pass with its own edge cases.
  CLAUDE.md forbids new dependencies without instruction, and the issue asks
  for the plain `[a-z0-9]` rule. A caller who needs readable slugs from
  non-ASCII names is a future issue.
- **Uniqueness.** The helper is deterministic and knows nothing about the
  database. Two inputs can produce the same slug; disambiguation is the
  caller's job.
- **Word-boundary-aware truncation.** Cutting at `max_len` may split a word.
  Backing up to the previous dash would make the output length depend on the
  input's word lengths, which is harder to reason about and not asked for.
- **Schema, routers, frontend.** No model, migration, endpoint, `src/api/`
  wrapper, or component is touched.

## Design

### Placement

`backend/utils/` already holds small pure helpers with the same shape —
`email_normalize.py`, `id_gen.py`, `privacy.py` — each a module docstring, a
private module-level constant or two, and one public function. `slug.py`
follows that shape exactly. It imports only `re` from the standard library.

Rejected: putting it under `backend/services/`. Services there own a domain
and talk to the database (`plan.py`, `check_in.py`); a string transform with
no I/O does not belong beside them.

### The function

```python
"""Turn free text into a URL- and filename-safe slug."""
from __future__ import annotations

import re

# Anything that is not a lowercase ASCII letter or digit. The input is
# lowercased first, so the class does not need A-Z.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Returned when nothing survives the transform. Not subject to max_len.
_FALLBACK = "item"


def slugify(text: str, max_len: int = 30) -> str:
    ...
```

The body is five steps, in this order, and the order matters:

1. `text.lower()` — Python's `str.lower`, no locale, no Unicode folding.
2. `_NON_ALNUM.sub("-", ...)` — every run of one or more non-`[a-z0-9]`
   characters, including whitespace, punctuation, underscores and non-ASCII
   letters, collapses to exactly one `-`.
3. `.strip("-")` — drop leading and trailing dashes left by step 2.
4. `[:max_len]` then `.rstrip("-")` — truncate, then remove any dash the
   cut exposed. Truncation can only expose a *trailing* dash (the leading
   edge was already cleaned in step 3), so `rstrip` is sufficient and
   `strip` would be misleading about what can happen here.
5. If the result is empty, return `_FALLBACK`; otherwise return the result.

Lowercasing before the regex is what lets the character class be
`[^a-z0-9]` rather than `[^A-Za-z0-9]`, matching the issue's wording
literally. Stripping before truncating means `"   hello"` with `max_len=5`
gives `"hello"`, not `""` — leading junk is not allowed to eat the budget.

### Decisions the issue left open

- **`max_len` that is zero or negative.** `text[:0]` is `""`, step 5 fires,
  and the caller gets `"item"`. No `ValueError`, no special case. The
  fallback rule already covers it and a helper this small should not have
  two failure modes.
- **`max_len` shorter than the fallback.** `"item"` is four characters and
  is returned verbatim even when `max_len < 4`. A fallback that could itself
  be truncated to `""` would defeat its purpose. The docstring says so.
- **`None` or non-`str` input.** Not handled. The signature is the contract;
  `None.lower()` raises `AttributeError` at the call site, which is the
  right outcome for a type error in a fully typed module. `normalize_email`
  coerces with `(email or "")` because it ingests form input from a router;
  `slugify` has no caller yet and should not pre-emptively hide a bug.
- **Idempotence.** `slugify(slugify(x, n), n) == slugify(x, n)` holds for
  every `n >= 4` — a valid slug passes through every step unchanged — and a
  test pins it, because it is the property a future caller will lean on
  when re-slugging a stored value. The one exception follows from the
  previous decision: at `n < 4` the fallback `"item"` is longer than the
  budget, so `slugify("", 2)` is `"item"` but `slugify("item", 2)` is
  `"it"`. That is accepted rather than "fixed", because the alternative is
  either a fallback that shrinks to `""` or a `max_len` floor that turns
  the argument into a lie. The docstring states both the property and the
  exception.

Rejected: raising on invalid `max_len`. It adds an exception path to a
function whose whole appeal is that it cannot fail, and there is no caller
who would benefit.

## Data flow

There is no request. `slugify` is a pure function of its arguments with no
I/O, no database session, no `company_id`, and no plan gate; multi-tenancy
and free-plan limits do not apply. The only flow is the five-step transform
above, from `text` in to `str` out, always in constant space proportional to
the input.

## Error handling

`slugify` does not raise for any `str` input and any `int` `max_len`. Empty
string, whitespace-only, punctuation-only, all-non-ASCII, and non-positive
`max_len` all resolve to `"item"`. There is no HTTP surface, so there is no
status code or error message to define.

## Testing

`tests/test_slug.py`, modelled on `tests/test_email_normalize.py`: plain
`pytest`, no fixtures, no database, one `@pytest.mark.parametrize` table per
rule so a failure names the rule that broke.

- **Lowercase and collapse** — `"Hello World"` → `"hello-world"`;
  `"Hello   World!!"` → `"hello-world"`; `"foo_bar.baz"` → `"foo-bar-baz"`;
  `"a---b"` → `"a-b"`. Non-ASCII letters become a dash and do not survive:
  `"café au lait"` → `"caf-au-lait"`, `"日本語"` → `"item"`.
- **Edge dashes stripped** — `"  -hello-  "` → `"hello"`;
  `"!!!hello!!!"` → `"hello"`.
- **Truncation without a trailing dash** — `"hello world"` with `max_len=6`
  is `"hello"` (the cut lands on the dash and it is removed), with
  `max_len=5` is `"hello"`, with `max_len=7` is `"hello-w"`. Output length
  is asserted `<= max_len` across a small table.
- **Default `max_len`** — a 40-character alphanumeric input comes back at
  exactly 30 characters.
- **Empty falls back to `"item"`** — `""`, `"   "`, `"---"`, `"!!!"`,
  `"日本語"`, and a non-empty input with `max_len=0` all return `"item"`.
- **Fallback ignores `max_len`** — `slugify("", max_len=2) == "item"`.
- **Idempotent at the default and at any `max_len >= 4`** — for each input
  in the tables, `slugify(slugify(x, n), n) == slugify(x, n)` with `n` in
  `{4, 6, 30}`. A separate case documents the exception:
  `slugify("", 2) == "item"` and `slugify("item", 2) == "it"`, so the
  fallback is the one output that does not round-trip under a budget
  shorter than itself.
- **Distinct inputs stay distinct** — `slugify("a") != slugify("b")`, as a
  guard against a regex that over-collapses.

No frontend tests: no frontend file changes. `tests/test_utc_today.py`
sweeps `tests/` by AST and the new file calls neither `date.today` nor
`datetime.now`, so it passes that sweep untouched.
