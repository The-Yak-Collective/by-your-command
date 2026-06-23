"""Tests for /showmymode's state normalization (dropping malformed records)."""

from bot.commands import showmymode


def test_keeps_well_formed_record():
    raw = {"users": {"1": {"guild_id": 5, "char": "🙊", "expires_at": 100}}}
    normalized = showmymode._normalize_state(raw)
    assert normalized["users"] == {
        "1": {"guild_id": 5, "char": "🙊", "expires_at": 100}
    }


def test_drops_records_missing_required_keys():
    raw = {
        "users": {
            "ok": {"guild_id": 5, "char": "🙊", "expires_at": 100},
            "no_expiry": {"guild_id": 5, "char": "🙊"},
            "no_guild": {"char": "🙊", "expires_at": 100},
            "no_char": {"guild_id": 5, "expires_at": 100},
        }
    }
    normalized = showmymode._normalize_state(raw)
    assert set(normalized["users"]) == {"ok"}


def test_drops_records_with_wrong_types():
    raw = {
        "users": {
            "guild_str": {"guild_id": "5", "char": "🙊", "expires_at": 100},
            "char_empty": {"guild_id": 5, "char": "", "expires_at": 100},
            "expiry_str": {"guild_id": 5, "char": "🙊", "expires_at": "100"},
            "not_a_dict": "nope",
        }
    }
    normalized = showmymode._normalize_state(raw)
    assert normalized["users"] == {}


def test_handles_non_dict_root_and_users():
    empty = {"version": 1, "users": {}}
    assert showmymode._normalize_state([]) == empty
    assert showmymode._normalize_state("garbage") == empty
    assert showmymode._normalize_state({"users": "not a dict"}) == empty


def test_preserves_original_nick_including_none():
    raw = {
        "users": {
            "had_none": {
                "guild_id": 5,
                "char": "🙊",
                "expires_at": 100,
                "original_nick": None,
            },
            "had_nick": {
                "guild_id": 5,
                "char": "🙊",
                "expires_at": 100,
                "original_nick": "Bob",
            },
        }
    }
    normalized = showmymode._normalize_state(raw)
    # None is meaningful ("no nickname"), so it must survive normalization.
    assert normalized["users"]["had_none"]["original_nick"] is None
    assert normalized["users"]["had_nick"]["original_nick"] == "Bob"


def test_drops_invalid_original_nick_but_keeps_record():
    raw = {
        "users": {
            "1": {
                "guild_id": 5,
                "char": "🙊",
                "expires_at": 100,
                "original_nick": 99,  # wrong type: neither str nor None
            }
        }
    }
    normalized = showmymode._normalize_state(raw)
    # The record is otherwise valid, so it is kept — just without the bad field, so
    # cleanup falls back to stripping the marker rather than trusting bad data.
    assert "1" in normalized["users"]
    assert "original_nick" not in normalized["users"]["1"]
