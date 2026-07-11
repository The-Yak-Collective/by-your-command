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

from bot import state
from bot.commands import chmod

GUILD_ID = 7
OTHER_GUILD = 8


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point /chmod's module-level store at a throwaway directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(chmod, "store", state.JSONStore(chmod.STATE_NAMESPACE))


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
        # Discord shows the nickname when set, otherwise the account username.
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
    # _turn_on/_turn_off/_apply only touch interaction.response (they send the result);
    # the invoking member is passed separately, so this interaction carries no user.
    return types.SimpleNamespace(response=FakeResponse())


def _record(user_id, *, guild_id=GUILD_ID) -> Any:
    """Return the user's persisted record in the given guild, or None.

    Uses the real per-guild accessor so the tests exercise the same nesting the
    production code reads, rather than hardcoding the on-disk shape again. Typed
    ``Any`` (not ``dict | None``) so subscripting the result in assertions matches
    the direct-dict-access style of the original tests and doesn't trip the strict
    optional-subscript rule the test config doesn't relax.
    """
    return chmod._users_in_guild(chmod._load_state(), guild_id).get(str(user_id))


# --- on / off (the shared core) ------------------------------------------------


def test_turn_off_restores_absence_of_nickname(isolated_store):
    # A user with NO server nickname turns the mode on, then off. The fix: they must
    # end with no nickname (nick=None), not their username left as an explicit nick.
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
    # Turning the mode on again while already on must keep the *original* nickname,
    # not record the already-marked one (which would make cleanup leave a marker).
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

    # Nothing was changed or persisted, and the user was told why.
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
    # The minimum timeout tracks the maintenance tick: a value shorter than one tick
    # (which the sweep could never honor) is refused — nothing is changed or persisted,
    # and the user is told the floor. Bumping the tick proves the floor follows it.
    monkeypatch.setattr(chmod.maintenance, "TICK_INTERVAL_MINUTES", 5)
    member = FakeMember(47, nick=None, username="Frank")
    interaction = _interaction()

    asyncio.run(chmod._turn_on(interaction, member, "🙊", 3))

    assert member.edits == []
    assert member.nick is None
    assert _record(47) is None
    assert "at least 5" in interaction.response.messages[-1][0]


def test_turn_on_accepts_duration_at_tick(isolated_store, monkeypatch):
    # A timeout exactly equal to the tick is allowed (the boundary is inclusive).
    monkeypatch.setattr(chmod.maintenance, "TICK_INTERVAL_MINUTES", 5)
    member = FakeMember(48, nick=None, username="Gina")

    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", 5))

    assert member.nick == "🙊Gina"
    assert _record(48) is not None


def test_messages_reflect_the_selected_character(isolated_store):
    # The on/off responses name the chosen character, so a custom marker reads as
    # e.g. "🎄 mode" rather than a generic label.
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
    # enable=True routes to _turn_on: marker applied, state recorded.
    member = FakeMember(50, nick=None, username="Henry")
    asyncio.run(chmod.apply(_interaction(), member, True, "🙊", None))
    assert member.nick == "🙊Henry"
    assert _record(50) is not None


def test_apply_enable_false_turns_off(isolated_store):
    # enable=False routes to _turn_off: turn on first, then force off.
    member = FakeMember(51, nick="Ivy", username="ivy")
    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Ivy"

    asyncio.run(chmod.apply(_interaction(), member, False, "🙊", None))
    assert member.nick == "Ivy"
    assert _record(51) is None


def test_apply_enable_omitted_swaps_on_when_inactive(isolated_store):
    # Omitted enable (None) swaps state: inactive -> on.
    member = FakeMember(52, nick=None, username="Jules")
    asyncio.run(chmod.apply(_interaction(), member, None, "🙊", None))
    assert member.nick == "🙊Jules"
    assert _record(52) is not None


def test_apply_enable_omitted_swaps_off_when_active(isolated_store):
    # Omitted enable (None) swaps state: active -> off. Turn on first, then swap.
    member = FakeMember(53, nick="Kim", username="kim")
    asyncio.run(chmod._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Kim"

    asyncio.run(chmod.apply(_interaction(), member, None, "🙊", None))
    assert member.nick == "Kim"
    assert _record(53) is None


# --- the cog's in-server guard -------------------------------------------------


def test_chmod_refuses_use_outside_a_server(isolated_store):
    # interaction.user not being a discord.Member (e.g. a DM) is refused before any
    # state is touched. The fake user has no nick/edit, modelling a non-member.
    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(), response=FakeResponse()
    )
    cog = chmod.Chmod(bot=None)

    asyncio.run(chmod.Chmod.chmod.callback(cog, interaction, None, None, None))

    assert interaction.response.messages
    assert "server" in interaction.response.messages[-1][0].lower()
    assert chmod._load_state()["guilds"] == {}


# --- no-record-off guard (multi-guild reachable) -------------------------------


def test_turn_off_with_no_record_in_this_guild_is_a_noop(isolated_store):
    # No active marker in this guild: _turn_off must not edit the nick and must tell
    # the user, rather than clobbering a nickname or restoring another guild's stored
    # nick. This is the path the guard exists for — reachable in multi-guild when the
    # user has the mode on in another guild but runs /chmod off here.
    member = FakeMember(70, nick="Plain", username="plain")
    interaction = _interaction()

    asyncio.run(chmod._turn_off(interaction, member))

    assert member.edits == []  # no nickname edit attempted
    assert member.nick == "Plain"  # untouched
    assert "marker" in interaction.response.messages[-1][0].lower()
    assert chmod._load_state()["guilds"] == {}  # nothing to delete


# --- multi-guild independence --------------------------------------------------


def test_turn_on_in_two_guilds_keeps_independent_records(isolated_store):
    # The same user turns the mode on in two guilds. Both nicks are marked, and each
    # guild records its own pre-marker nickname — turning on in one must not overwrite
    # or leak the other's record (the bug under the flat user-id keying).
    member_a = FakeMember(80, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(80, nick=None, username="alice", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    asyncio.run(chmod._turn_on(_interaction(), member_b, "🔇", None))

    assert member_a.nick == "🙊Alice"
    assert member_b.nick == "🔇alice"
    # Two independent records, each with its own char and original_nick.
    assert _record(80, guild_id=GUILD_ID) == {
        "char": "🙊",
        "expires_at": pytest.approx(int(__import__("time").time()) + 90 * 60, abs=5),
        "original_nick": "Alice",
    }
    assert _record(80, guild_id=OTHER_GUILD)["char"] == "🔇"
    assert _record(80, guild_id=OTHER_GUILD)["original_nick"] is None


def test_swap_in_guild_b_does_not_turn_off_guild_a(isolated_store):
    # Mode on in guild A; swap (enable omitted) in guild B. Because B has no record,
    # the swap turns B *on* — it must not turn A off (the bug under flat keying, where
    # the global record made the swap believe the mode was already active).
    member_a = FakeMember(81, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(81, nick=None, username="alice", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    asyncio.run(chmod.apply(_interaction(), member_b, None, "🔇", None))

    # A is still marked; B was turned on (not off).
    assert member_a.nick == "🙊Alice"
    assert member_b.nick == "🔇alice"
    assert _record(81, guild_id=GUILD_ID)["char"] == "🙊"
    assert _record(81, guild_id=OTHER_GUILD)["char"] == "🔇"


def test_force_off_in_guild_without_record_leaves_other_guild_intact(isolated_store):
    # Mode on in guild A; force off (enable=False) in guild B where there's no record.
    # The no-record guard fires in B (no edit, no state change), and A's record and
    # marker are left completely intact.
    member_a = FakeMember(82, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(82, nick=None, username="alice", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    assert member_a.nick == "🙊Alice"

    interaction_b = _interaction()
    asyncio.run(chmod.apply(interaction_b, member_b, False, "🙊", None))

    # Guild B's nick was not touched (the guard fired)...
    assert member_b.edits == []
    assert "marker" in interaction_b.response.messages[-1][0].lower()
    # ...and guild A's marker and record survived.
    assert member_a.nick == "🙊Alice"
    assert _record(82, guild_id=GUILD_ID) is not None


def test_turn_off_in_one_guild_does_not_remove_other_guilds_marker(isolated_store):
    # Turn on in both guilds, then off in A. A's record is cleared and nick restored;
    # B's record and marker are untouched (under flat keying, off in A would delete
    # the shared record and orphan B's marker).
    member_a = FakeMember(83, nick="Alice", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(83, nick="Bee", username="bee", guild_id=OTHER_GUILD)

    asyncio.run(chmod._turn_on(_interaction(), member_a, "🙊", None))
    asyncio.run(chmod._turn_on(_interaction(), member_b, "🙊", None))

    asyncio.run(chmod._turn_off(_interaction(), member_a))

    assert member_a.nick == "Alice"
    assert _record(83, guild_id=GUILD_ID) is None
    # B is still marked and tracked.
    assert member_b.nick == "🙊Bee"
    assert _record(83, guild_id=OTHER_GUILD) is not None


# --- sweep, per guild ----------------------------------------------------------


class FakeGuild:
    """A guild whose members can be fetched by id (for the maintenance sweep)."""

    def __init__(self, guild_id, *, members):
        self.id = guild_id
        self._members = members  # {member_id: FakeMember}

    async def fetch_member(self, member_id):
        return self._members[member_id]


class FakeBot:
    """Stand-in bot whose get_guild returns registered guilds (for the sweep)."""

    def __init__(self, *, guilds):
        self._guilds = guilds  # {guild_id: FakeGuild}

    def get_guild(self, guild_id):
        return self._guilds.get(guild_id)


def test_sweep_clears_two_guilds_independently(isolated_store):
    # Two users in two guilds, both expired. The sweep restores each in its own guild
    # and prunes both (now-empty) guild entries.
    state_dict = chmod._empty_state()
    state_dict["guilds"] = {
        str(GUILD_ID): {
            "users": {"10": {"char": "🙊", "expires_at": 1, "original_nick": "Ten"}}
        },
        str(OTHER_GUILD): {
            "users": {"20": {"char": "🔇", "expires_at": 1, "original_nick": "Twenty"}}
        },
    }
    chmod._save_state(state_dict)

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

    asyncio.run(chmod._sweep_expired(bot))

    # Each nick restored to that guild's recorded original.
    assert member_a.nick == "Ten"
    assert member_b.nick == "Twenty"
    # Both records and the now-empty guild entries were pruned.
    assert chmod._load_state()["guilds"] == {}


def test_sweep_leaves_unexpired_marker_in_other_guild(isolated_store):
    # The same user in two guilds: expired in A, still active in B. The sweep must
    # restore A and leave B's record and marker fully intact — the core multi-guild
    # guarantee for the sweep.
    now = int(__import__("time").time())
    state_dict = chmod._empty_state()
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
    chmod._save_state(state_dict)

    member_a = FakeMember(30, nick="🙊AliceNow", username="alice", guild_id=GUILD_ID)
    member_b = FakeMember(30, nick="🔇BeeNow", username="bee", guild_id=OTHER_GUILD)
    bot = FakeBot(
        guilds={
            GUILD_ID: FakeGuild(GUILD_ID, members={30: member_a}),
            OTHER_GUILD: FakeGuild(OTHER_GUILD, members={30: member_b}),
        }
    )

    asyncio.run(chmod._sweep_expired(bot))

    # A restored, B untouched.
    assert member_a.nick == "Alice"
    assert member_b.nick == "🔇BeeNow"
    # A's guild entry pruned; B's record and guild entry intact.
    assert str(GUILD_ID) not in chmod._load_state()["guilds"]
    assert _record(30, guild_id=OTHER_GUILD) is not None


def test_sweep_drops_record_when_bot_left_that_guild(isolated_store):
    # If the bot is no longer in a guild (get_guild returns None), the expired record
    # is still dropped — there's no member to restore, but keeping it would retry
    # forever. (Matches the existing "member may have left" leniency, at guild scale.)
    state_dict = chmod._empty_state()
    state_dict["guilds"] = {
        str(GUILD_ID): {
            "users": {"40": {"char": "🙊", "expires_at": 1, "original_nick": "Forty"}}
        }
    }
    chmod._save_state(state_dict)

    bot = FakeBot(guilds={})  # bot left GUILD_ID

    asyncio.run(chmod._sweep_expired(bot))

    assert chmod._load_state()["guilds"] == {}
