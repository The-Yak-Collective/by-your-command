"""Tests for chunk_message (splitting long text into Discord-sized pieces)."""

from bot.utils import chunk_message


def test_short_text_is_single_chunk():
    assert chunk_message("hello", limit=100) == ["hello"]


def test_splits_on_newline_boundary():
    text = "a" * 60 + "\n" + "b" * 60
    chunks = chunk_message(text, limit=100)
    assert chunks == ["a" * 60, "b" * 60]
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_hard_splits_long_unbroken_line():
    # A single 250-char line with no newline must still be broken at the limit
    # (the legacy implementation could recurse forever here).
    chunks = chunk_message("x" * 250, limit=100)
    assert chunks == ["x" * 100, "x" * 100, "x" * 50]
    assert all(len(chunk) <= 100 for chunk in chunks)
