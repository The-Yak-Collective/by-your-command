"""Tests for /mode's state normalization and v1->v2 migration.

The state shape is versioned: v2 nests records as guilds -> {gid} -> users ->
{uid} -> record (keying by guild *and* user is what makes one bot instance safe
across multiple servers). v1's flat users -> {uid} -> {guild_id, ...} layout is
migrated to v2 on first load so an existing single-guild deployment upgrades
transparently. These tests cover both validation of the v2 layout and that
migration, plus the long-standing "drop malformed records" behaviour.
"""

from bot.commands import mode_state as mode


# --- v2 nested layout: validation ----------------------------------------------


def test_v2_keeps_well_formed_record():
    raw = {
        "version": 2,
        "guilds": {"5": {"users": {"1": {"char": "🙊", "expires_at": 100}}}},
    }
    normalized = mode._normalize_state(raw)
    assert normalized == {
        "version": 2,
        "guilds": {"5": {"users": {"1": {"char": "🙊", "expires_at": 100}}}},
    }


def test_v2_drops_records_missing_required_keys():
    # Within a guild, a record must have a non-empty char and an integer expires_at.
    raw = {
        "version": 2,
        "guilds": {
            "5": {
                "users": {
                    "ok": {"char": "🙊", "expires_at": 100},
                    "no_expiry": {"char": "🙊"},
                    "no_char": {"expires_at": 100},
                    "empty_char": {"char": "", "expires_at": 100},
                }
            }
        },
    }
    normalized = mode._normalize_state(raw)
    assert set(normalized["guilds"]["5"]["users"]) == {"ok"}


def test_v2_drops_records_with_wrong_types():
    raw = {
        "version": 2,
        "guilds": {
            "5": {
                "users": {
                    "ok": {"char": "🙊", "expires_at": 100},
                    "char_empty": {"char": "", "expires_at": 100},
                    "expiry_str": {"char": "🙊", "expires_at": "100"},
                    "not_a_dict": "nope",
                }
            }
        },
    }
    normalized = mode._normalize_state(raw)
    assert set(normalized["guilds"]["5"]["users"]) == {"ok"}


def test_v2_preserves_original_nick_including_none():
    raw = {
        "version": 2,
        "guilds": {
            "5": {
                "users": {
                    "had_none": {
                        "char": "🙊",
                        "expires_at": 100,
                        "original_nick": None,
                    },
                    "had_nick": {
                        "char": "🙊",
                        "expires_at": 100,
                        "original_nick": "Bob",
                    },
                }
            }
        },
    }
    normalized = mode._normalize_state(raw)
    # None is meaningful ("no nickname"), so it must survive normalization.
    assert normalized["guilds"]["5"]["users"]["had_none"]["original_nick"] is None
    assert normalized["guilds"]["5"]["users"]["had_nick"]["original_nick"] == "Bob"


def test_v2_drops_invalid_original_nick_but_keeps_record():
    raw = {
        "version": 2,
        "guilds": {
            "5": {
                "users": {
                    "1": {
                        "char": "🙊",
                        "expires_at": 100,
                        "original_nick": 99,  # wrong type: neither str nor None
                    }
                }
            }
        },
    }
    normalized = mode._normalize_state(raw)
    # The record is otherwise valid, so it is kept — just without the bad field, so
    # cleanup falls back to stripping the marker rather than trusting bad data.
    assert "1" in normalized["guilds"]["5"]["users"]
    assert "original_nick" not in normalized["guilds"]["5"]["users"]["1"]


def test_v2_ignores_redundant_guild_id_field_in_record():
    # In v2 the guild id is the outer key, so a guild_id field inside a record is
    # vestigial and must be dropped (not echoed into the clean record).
    raw = {
        "version": 2,
        "guilds": {
            "5": {"users": {"1": {"guild_id": 5, "char": "🙊", "expires_at": 100}}}
        },
    }
    normalized = mode._normalize_state(raw)
    record = normalized["guilds"]["5"]["users"]["1"]
    assert "guild_id" not in record
    assert record == {"char": "🙊", "expires_at": 100}


def test_v2_handles_non_dict_root_and_guilds():
    empty = {"version": 2, "guilds": {}}
    assert mode._normalize_state([]) == empty
    assert mode._normalize_state("garbage") == empty
    assert mode._normalize_state({"version": 2, "guilds": "not a dict"}) == empty
    # A guild entry that isn't a dict, or whose users isn't a dict, is skipped.
    assert (
        mode._normalize_state(
            {"version": 2, "guilds": {"5": "nope", "6": {"users": "nope"}}}
        )
        == empty
    )


def test_v2_two_guilds_are_independent():
    # The point of the nested layout: the same user can hold a marker in two guilds,
    # each recorded under its own guild key.
    raw = {
        "version": 2,
        "guilds": {
            "100": {"users": {"1": {"char": "🙊", "expires_at": 100}}},
            "200": {"users": {"1": {"char": "🔇", "expires_at": 200}}},
        },
    }
    normalized = mode._normalize_state(raw)
    assert normalized["guilds"]["100"]["users"]["1"]["char"] == "🙊"
    assert normalized["guilds"]["200"]["users"]["1"]["char"] == "🔇"


# --- v1 -> v2 migration --------------------------------------------------------


def test_migrates_v1_flat_to_v2_nested():
    # A v1 record carries guild_id inside; migration relocates it under that guild
    # key, and the redundant guild_id field is dropped from the record.
    raw = {
        "version": 1,
        "users": {"1": {"guild_id": 5, "char": "🙊", "expires_at": 100}},
    }
    normalized = mode._normalize_state(raw)
    assert normalized == {
        "version": 2,
        "guilds": {"5": {"users": {"1": {"char": "🙊", "expires_at": 100}}}},
    }


def test_migrates_versionless_v1_state():
    # Even with no version field, a flat users dict is treated as v1 and migrated.
    raw = {"users": {"1": {"guild_id": 5, "char": "🙊", "expires_at": 100}}}
    normalized = mode._normalize_state(raw)
    assert normalized["version"] == 2
    assert "1" in normalized["guilds"]["5"]["users"]


def test_v1_migration_preserves_original_nick():
    raw = {
        "version": 1,
        "users": {
            "1": {
                "guild_id": 5,
                "char": "🙊",
                "expires_at": 100,
                "original_nick": None,
            }
        },
    }
    normalized = mode._normalize_state(raw)
    assert normalized["guilds"]["5"]["users"]["1"]["original_nick"] is None


def test_v1_record_without_guild_id_is_dropped():
    # Without a guild_id there's no guild key to place the record under, so the v2
    # shape (which keys by guild) can't represent it — it is dropped, not silently
    # stuffed under a sentinel key.
    raw = {"version": 1, "users": {"1": {"char": "🙊", "expires_at": 100}}}
    normalized = mode._normalize_state(raw)
    assert normalized["guilds"] == {}


def test_v1_record_with_non_int_guild_id_is_dropped():
    # A string guild_id (the JSON-native type for keys, but invalid here) can't be
    # trusted to address a guild, so the record is dropped rather than coerced.
    raw = {
        "version": 1,
        "users": {"1": {"guild_id": "5", "char": "🙊", "expires_at": 100}},
    }
    normalized = mode._normalize_state(raw)
    assert normalized["guilds"] == {}


def test_v1_migration_drops_malformed_records():
    # The same malformed-record rules apply during migration: a record missing
    # char/expires_at is dropped rather than placed.
    raw = {
        "version": 1,
        "users": {
            "ok": {"guild_id": 5, "char": "🙊", "expires_at": 100},
            "bad": {"guild_id": 5, "char": "🙊"},  # no expires_at
        },
    }
    normalized = mode._normalize_state(raw)
    assert set(normalized["guilds"]["5"]["users"]) == {"ok"}


def test_v1_migration_places_each_record_under_its_own_guild():
    # Two users in two different guilds land under separate guild keys.
    raw = {
        "version": 1,
        "users": {
            "1": {"guild_id": 100, "char": "🙊", "expires_at": 100},
            "2": {"guild_id": 200, "char": "🔇", "expires_at": 200},
        },
    }
    normalized = mode._normalize_state(raw)
    assert set(normalized["guilds"]) == {"100", "200"}
    assert normalized["guilds"]["100"]["users"]["1"]["char"] == "🙊"
    assert normalized["guilds"]["200"]["users"]["2"]["char"] == "🔇"
