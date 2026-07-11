"""Command-level tests for /chmod: nickname restoration, the duration cap, routing,
and multi-guild independence.

The pure prefix/expiry helpers are tested elsewhere; here we exercise the parts that
actually edit members and persist state, using fakes for the Discord member and the
interaction response, and a throwaway state store under a temp XDG_STATE_HOME.

The multi-guild tests are the heart of the layout change: a user's mode in one
guild must be wholly independent of their mode in another — turning on/off in one
never touches (or leaks a nickname into) the other.
"""

import asyncio
import types
from typing import Any

import pytest

from bot.commands import chmod, chmod_state

GUILD_ID = 7
OTHER_GUILD = 8


class FakeMember:
    """A member whose nickname can be edited; display_name mirrors Discord's rule.

    ``guild_id`` defaults to GUILD_ID so existing single-guild tests are unchanged,
    but multi-guild tests pass a different id to model the same user in another
    server (a member is per-guild in Discord, so each gets its own FakeMember).
    """

    def __init__(self, member_id, *, nick=None, username="User", guild_id=GUILD_ID):
        self.id = member_id
        self.guild = types.SimpleNamespace(id=guild_id)
        self.nick = nick
        self._username = username
        self.edits = []  # every nick value passed to edit(), in order

    @property
    def display_name(self):
        return self.nick if self.nick is not None else self._username

    async def edit(self, nick=None):
        self.edits.append(nick)
        self.nick = nick


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


def _interaction():
    return types.SimpleNamespace(response=FakeResponse())


def _record(user_id, *, guild_id=GUILD_ID) -> Any:
    """Return the user's persisted record in the given guild, or None.

    Uses the real per-guild accessor so the tests exercise the same nesting the
    production code reads, rather than hardcoding the on-disk shape again.
    """
    return chmod_state._users_in_guild(chmod_state._load_state(), guild_id).get(
        str(user_id)
    )


# --- on / off (the shared core) ------------------------------------------------


def test_turn_off_restores_absence_of_nickname(isolated_store):
    member = FakeMember(42, nick=None, username="Alice")

    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Alice"
    assert _record(42)["original_nick"] is None

    asyncio.run(chmod._turn_off(_interaction(), member))
    assert member.nick is None  # restored to "no nickname", not "Alice"
    assert _record(42) is None


def test_turn_off_restores_existing_nickname(isolated_store):
    member = FakeMember(43, nick="Bob", username="bob_account")

    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Bob"

    asyncio.run(chmod._turn_off(_interaction(), member))
    assert member.nick == "Bob"


def test_turn_on_twice_does_not_capture_marked_nick(isolated_store):
    member = FakeMember(44, nick="Carol", username="carol")

    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert _record(44)["original_nick"] == "Carol"

    asyncio.run(chmod._turn_off(_interaction(), member))
    assert member.nick == "Carol"


def test_turn_on_rejects_duration_over_maximum(isolated_store):
    member = FakeMember(45, nick=None, username="Dave")
    interaction = _interaction()

    asyncio.run(
        chmod._turn_on(interaction, member, "🙊", chmod.MAX_DURATION_MINUTES + 1)
    )

    assert member.edits == []
    assert member.nick is None
    assert _record(45) is None
    assert interaction.response.messages
    assert "minutes" in interaction.response.messages[-1][0].lower()


def test_turn_on_accepts_duration_at_maximum(isolated_store):
    member = FakeMember(46, nick=None, username="Erin")

    asyncio.run(
        chmod._turn_on(_interaction(), member, "🙊", chmod.MAX_DURATION_MINUTES)
    )

    assert member.nick == "🙊Erin"
    assert _record(46) is not None


def test_turn_on_rejects_duration_below_tick(isolated_store, monkeypatch):
    import bot.maintenance as maint

    monkeypatch.setattr(maint, "TICK_INTERVAL_MINUTES", 5)
    member = FakeMember(47, nick=None, username="Frank")
    interaction = _interaction()

    asyncio.run(chmod._turn_on(interaction, member, "🙊", 3))

    assert member.edits == []
    assert member.nick is None
    assert _record(47) is None
    assert "at least 5" in interaction.response.messages[-1][0]


def test_turn_on_accepts_duration_at_tick(isolated_store, monkeypatch):
    import bot.maintenance as maint

    monkeypatch.setattr(maint, "TICK_INTERVAL_MINUTES", 5)
    member = FakeMember(48, nick=None, username="Gina")

    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", 5))

    assert member.nick == "🙊Gina"
    assert _record(48) is not None


def test_messages_reflect_the_selected_character(isolated_store):
    member = FakeMember(49, nick=None, username="Tree")
    on_interaction = _interaction()
    asyncio.run(chmod._turn_on(on_interaction, member, "🎄", None))
    assert (
        on_interaction.response.messages[-1][0]
        == "You're in 🎄 mode for the next 90 minutes."
    )

    off_interaction = _interaction()
    asyncio.run(chmod._turn_off(off_interaction, member))
    assert off_interaction.response.messages[-1][0] == "🎄 mode off."


# --- routing (the shared apply() entry point) -------------------------


def test_apply_enable_true_turns_on(isolated_store):
    member = FakeMember(50, nick=None, username="Henry")
    asyncio.run(chmod.apply(_interaction(), member, True, "🙊", None))
    assert member.nick == "🙊Henry"
    assert _record(50) is not None


def test_apply_enable_false_turns_off(isolated_store):
    member = FakeMember(51, nick="Ivy", username="ivy")
    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Ivy"

    asyncio.run(chmod.apply(_interaction(), member, False, "🙊", None))
    assert member.nick == "Ivy"
    assert _record(51) is None


def test_apply_enable_omitted_swaps_on_when_inactive(isolated_store):
    member = FakeMember(52, nick=None, username="Jules")
    asyncio.run(chmod.apply(_interaction(), member, None, "🙊", None))
    assert member.nick == "🙊Jules"
    assert _record(52) is not None


def test_apply_enable_omitted_swaps_off_when_active(isolated_store):
    member = FakeMember(53, nick="Kim", username="kim")
    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Kim"

    asyncio.run(chmod.apply(_interaction(), member, None, "🙊", None))
    assert member.nick == "Kim"
    assert _record(53) is None


# --- the cog's in-server guard -------------------------------------------------


def test_chmod_refuses_use_outside_a_server(isolated_store):
    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(), response=FakeResponse()
    )
    cog = chmod.Chmod(bot=None)

    asyncio.run(chmod.Chmod.chmod.callback(cog, interaction, None, None, None))

    assert interaction.response.messages
    assert "server" in interaction.response.messages[-1][0].lower()
    assert chmod_state._load_state()["guilds"] == {}


# --- no-record-off guard (multi-guild reachable) -------------------------------


def test_turn_off_with_no_record_in_this_guild_is_a_noop(isolated_store):
    member = FakeMember(70, nick="Plain", username="plain")
    interaction = _interaction()

    asyncio.run(chmod._turn_off(interaction, member))

    assert member.edits == []  # no nickname edit attempted
    assert member.nick == "Plain"  # untouched
    assert "marker" in interaction.response.messages[-1][0].lower()
    assert chmod_state._load_state()["guilds"] == {}  # nothing to delete


# --- multi-guild independence --------------------------------------------------


def test_turn_on_in_two_guilds_keeps_independent_records(isolated_store):
    member_a = FakeMember(80, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(80, nick=None, username="alice", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    asyncio.run(chmod._turn_on(_interaction(), member_b, "🔇", None))

    assert member_a.nick == "🙊Alice"
    assert member_b.nick == "🔇alice"
    assert _record(80, guild_id=GUILD_ID) == {
        "char": "🙊",
        "expires_at": pytest.approx(int(__import__("time").time()) + 90 * 60, abs=5),
        "original_nick": "Alice",
    }
    assert _record(80, guild_id=OTHER_GUILD)["char"] == "🔇"
    assert _record(80, guild_id=OTHER_GUILD)["original_nick"] is None


def test_swap_in_guild_b_does_not_turn_off_guild_a(isolated_store):
    member_a = FakeMember(81, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(81, nick=None, username="alice", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    asyncio.run(chmod.apply(_interaction(), member_b, None, "🔇", None))

    assert member_a.nick == "🙊Alice"
    assert member_b.nick == "🔇alice"
    assert _record(81, guild_id=GUILD_ID)["char"] == "🙊"
    assert _record(81, guild_id=OTHER_GUILD)["char"] == "🔇"


def test_force_off_in_guild_without_record_leaves_other_guild_intact(isolated_store):
    member_a = FakeMember(82, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(82, nick=None, username="alice", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    assert member_a.nick == "🙊Alice"

    interaction_b = _interaction()
    asyncio.run(chmod.apply(interaction_b, member_b, False, "🙊", None))

    assert member_b.edits == []
    assert "marker" in interaction_b.response.messages[-1][0].lower()
    assert member_a.nick == "🙊Alice"
    assert _record(82, guild_id=GUILD_ID) is not None


def test_turn_off_in_one_guild_does_not_remove_other_guilds_marker(isolated_store):
    member_a = FakeMember(83, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(83, nick="Bee", username="bee", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    asyncio.run(chmod._turn_on(_interaction(), member_b, "🙊", None))

    asyncio.run(chmod._turn_off(_interaction(), member_a))

    assert member_a.nick == "Alice"
    assert _record(83, guild_id=GUILD_ID) is None
    assert member_b.nick == "🙊Bee"
    assert _record(83, guild_id=OTHER_GUILD) is not None


# --- sweep, per guild ----------------------------------------------------------


class FakeGuild:
    def __init__(self, guild_id, *, members):
        self.id = guild_id
        self._members = members  # {member_id: FakeMember}

    async def fetch_member(self, member_id):
        return self._members[member_id]


class FakeBot:
    def __init__(self, *, guilds):
        self._guilds = guilds  # {guild_id: FakeGuild}

    def get_guild(self, guild_id):
        return self._guilds.get(guild_id)


def test_sweep_clears_two_guilds_independently(isolated_store):
    from bot.commands.chmod_maintenance import _sweep_expired

    state_dict = chmod_state._empty_state()
    state_dict["guilds"] = {
        str(GUILD_ID): {
            "users": {"10": {"char": "🙊", "expires_at": 1, "original_nick": "Ten"}}
        },
        str(OTHER_GUILD): {
            "users": {"20": {"char": "🔇", "expires_at": 1, "original_nick": "Twenty"}}
        },
    }
    chmod_state._save_state(state_dict)

    member_a = FakeMember(10, nick="🙊TenNow", username="ten", guild_id=GUILD_ID)
    member_b = FakeMember(
        20, nick="🔇TwentyNow", username="twenty", guild_id=OTHER_GUILD
    )
    bot = FakeBot(
        guilds={
            GUILD_ID: FakeGuild(GUILD_ID, members={10: member_a}),
            OTHER_GUILD: FakeGuild(OTHER_GUILD, members={20: member_b}),
        }
    )

    asyncio.run(_sweep_expired(bot))

    assert member_a.nick == "Ten"
    assert member_b.nick == "Twenty"
    assert chmod_state._load_state()["guilds"] == {}


def test_sweep_leaves_unexpired_marker_in_other_guild(isolated_store):
    from bot.commands.chmod_maintenance import _sweep_expired

    now = int(__import__("time").time())
    state_dict = chmod_state._empty_state()
    state_dict["guilds"] = {
        str(GUILD_ID): {
            "users": {
                "30": {"char": "🙊", "expires_at": now - 1, "original_nick": "Alice"}
            }
        },
        str(OTHER_GUILD): {
            "users": {
                "30": {"char": "🔇", "expires_at": now + 9999, "original_nick": "Bee"}
            }
        },
    }
    chmod_state._save_state(state_dict)

    member_a = FakeMember(30, nick="🙊AliceNow", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(30, nick="🔇BeeNow", username="bee", guild_id=OTHER_GUILD)
    bot = FakeBot(
        guilds={
            GUILD_ID: FakeGuild(GUILD_ID, members={30: member_a}),
            OTHER_GUILD: FakeGuild(OTHER_GUILD, members={30: member_b}),
        }
    )

    asyncio.run(_sweep_expired(bot))

    assert member_a.nick == "Alice"
    assert member_b.nick == "🔇BeeNow"
    assert str(GUILD_ID) not in chmod_state._load_state()["guilds"]
    assert _record(30, guild_id=OTHER_GUILD) is not None


def test_sweep_drops_record_when_bot_left_that_guild(isolated_store):
    from bot.commands.chmod_maintenance import _sweep_expired

    state_dict = chmod_state._empty_state()
    state_dict["guilds"] = {
        str(GUILD_ID): {
            "users": {"40": {"char": "🙊", "expires_at": 1, "original_nick": "Forty"}}
        }
    }
    chmod_state._save_state(state_dict)

    bot = FakeBot(guilds={})  # bot left GUILD_ID

    asyncio.run(_sweep_expired(bot))

    assert chmod_state._load_state()["guilds"] == {}
