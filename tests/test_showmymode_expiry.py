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
