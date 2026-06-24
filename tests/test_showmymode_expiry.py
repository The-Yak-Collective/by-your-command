"""Tests for the pure expiry/duration logic behind /showmymode's auto-removal."""

import pytest

from bot.utils import expired_user_ids, resolve_duration_minutes


def test_expired_selects_at_or_before_now():
    users = {
        "1": {"expires_at": 50},
        "2": {"expires_at": 100},  # exactly now -> expired (boundary is inclusive)
        "3": {"expires_at": 150},
    }
    assert sorted(expired_user_ids(users, now=100)) == ["1", "2"]


def test_expired_ignores_missing_timestamp():
    users = {"1": {}, "2": {"expires_at": 10}}
    assert expired_user_ids(users, now=100) == ["2"]


def test_resolve_duration_defaults_when_none():
    assert resolve_duration_minutes(None, default=90) == 90


def test_resolve_duration_accepts_positive():
    assert resolve_duration_minutes(30, default=90) == 30


def test_resolve_duration_rejects_non_positive():
    with pytest.raises(ValueError):
        resolve_duration_minutes(0, default=90)
    with pytest.raises(ValueError):
        resolve_duration_minutes(-5, default=90)


def test_resolve_duration_accepts_value_at_maximum():
    # The boundary itself is allowed; only values strictly above it are rejected.
    assert resolve_duration_minutes(10080, default=90, maximum=10080) == 10080


def test_resolve_duration_rejects_above_maximum():
    with pytest.raises(ValueError):
        resolve_duration_minutes(10081, default=90, maximum=10080)


def test_resolve_duration_no_maximum_allows_large_values():
    # With no maximum (the default), the original "any positive value" behaviour holds.
    assert resolve_duration_minutes(10**9, default=90) == 10**9


def test_resolve_duration_rejects_below_minimum():
    # A value finer than the caller can act on (e.g. shorter than the sweep tick).
    with pytest.raises(ValueError):
        resolve_duration_minutes(3, default=90, minimum=5)


def test_resolve_duration_accepts_value_at_minimum():
    # The floor itself is allowed; only values strictly below it are rejected.
    assert resolve_duration_minutes(5, default=90, minimum=5) == 5


def test_resolve_duration_minimum_does_not_apply_to_default():
    # A None duration returns the default untouched, even below the stated minimum.
    assert resolve_duration_minutes(None, default=2, minimum=5) == 2
