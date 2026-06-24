"""Command-level tests for /showmymode: nickname restoration and the duration cap.

The pure prefix/expiry helpers are tested elsewhere; here we exercise the parts that
actually edit members and persist state, using fakes for the Discord member and the
interaction response, and a throwaway state store under a temp XDG_STATE_HOME.
"""

import asyncio
import types

import pytest

from bot import state
from bot.commands import showmymode

GUILD_ID = 7


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point /showmymode's module-level store at a throwaway directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(showmymode, "store", state.JSONStore(showmymode.COMMAND_NAME))


class FakeMember:
    """A member whose nickname can be edited; display_name mirrors Discord's rule."""

    def __init__(self, member_id, *, nick=None, username="User"):
        self.id = member_id
        self.guild = types.SimpleNamespace(id=GUILD_ID)
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
    return types.SimpleNamespace(response=FakeResponse())


def _cog():
    return showmymode.ShowMyMode(bot=None)


def test_turn_off_restores_absence_of_nickname(isolated_store):
    # A user with NO server nickname turns the mode on, then off. The fix: they must
    # end with no nickname (nick=None), not their username left as an explicit nick.
    cog = _cog()
    member = FakeMember(42, nick=None, username="Alice")

    asyncio.run(cog._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Alice"
    assert showmymode._load_state()["users"]["42"]["original_nick"] is None

    asyncio.run(cog._turn_off(_interaction(), member))
    assert member.nick is None  # restored to "no nickname", not "Alice"
    assert "42" not in showmymode._load_state()["users"]


def test_turn_off_restores_existing_nickname(isolated_store):
    cog = _cog()
    member = FakeMember(43, nick="Bob", username="bob_account")

    asyncio.run(cog._turn_on(_interaction(), member, "🙊", None))
    assert member.nick == "🙊Bob"

    asyncio.run(cog._turn_off(_interaction(), member))
    assert member.nick == "Bob"


def test_turn_on_twice_does_not_capture_marked_nick(isolated_store):
    # Turning the mode on again while already on must keep the *original* nickname,
    # not record the already-marked one (which would make cleanup leave a marker).
    cog = _cog()
    member = FakeMember(44, nick="Carol", username="carol")

    asyncio.run(cog._turn_on(_interaction(), member, "🙊", None))
    asyncio.run(cog._turn_on(_interaction(), member, "🙊", None))
    assert showmymode._load_state()["users"]["44"]["original_nick"] == "Carol"

    asyncio.run(cog._turn_off(_interaction(), member))
    assert member.nick == "Carol"


def test_turn_on_rejects_duration_over_maximum(isolated_store):
    cog = _cog()
    member = FakeMember(45, nick=None, username="Dave")
    interaction = _interaction()

    asyncio.run(
        cog._turn_on(interaction, member, "🙊", showmymode.MAX_DURATION_MINUTES + 1)
    )

    # Nothing was changed or persisted, and the user was told why.
    assert member.edits == []
    assert member.nick is None
    assert "45" not in showmymode._load_state()["users"]
    assert interaction.response.messages
    assert "minutes" in interaction.response.messages[-1][0].lower()


def test_turn_on_accepts_duration_at_maximum(isolated_store):
    cog = _cog()
    member = FakeMember(46, nick=None, username="Erin")

    asyncio.run(
        cog._turn_on(_interaction(), member, "🙊", showmymode.MAX_DURATION_MINUTES)
    )

    assert member.nick == "🙊Erin"
    assert "46" in showmymode._load_state()["users"]


def test_turn_on_rejects_duration_below_tick(isolated_store, monkeypatch):
    # The minimum timeout tracks the maintenance tick: a value shorter than one tick
    # (which the sweep could never honor) is refused — nothing is changed or persisted,
    # and the user is told the floor. Bumping the tick proves the floor follows it.
    monkeypatch.setattr(showmymode.maintenance, "TICK_INTERVAL_MINUTES", 5)
    cog = _cog()
    member = FakeMember(47, nick=None, username="Frank")
    interaction = _interaction()

    asyncio.run(cog._turn_on(interaction, member, "🙊", 3))

    assert member.edits == []
    assert member.nick is None
    assert "47" not in showmymode._load_state()["users"]
    assert "at least 5" in interaction.response.messages[-1][0]


def test_turn_on_accepts_duration_at_tick(isolated_store, monkeypatch):
    # A timeout exactly equal to the tick is allowed (the boundary is inclusive).
    monkeypatch.setattr(showmymode.maintenance, "TICK_INTERVAL_MINUTES", 5)
    cog = _cog()
    member = FakeMember(48, nick=None, username="Gina")

    asyncio.run(cog._turn_on(_interaction(), member, "🙊", 5))

    assert member.nick == "🙊Gina"
    assert "48" in showmymode._load_state()["users"]
