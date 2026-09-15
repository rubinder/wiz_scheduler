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
