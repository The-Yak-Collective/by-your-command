"""Tests for the idempotent nickname-prefix helpers used by /showmymode."""

from bot.utils import add_mode_prefix, remove_mode_prefix


def test_add_prepends_marker():
    assert add_mode_prefix("Alice", "🙊") == "🙊Alice"


def test_add_is_idempotent():
    # Turning the mode "on" twice must not stack markers.
    assert add_mode_prefix("🙊Alice", "🙊") == "🙊Alice"


def test_remove_strips_one_marker():
    assert remove_mode_prefix("🙊Alice", "🙊") == "Alice"


def test_remove_is_noop_without_marker():
    assert remove_mode_prefix("Alice", "🙊") == "Alice"
