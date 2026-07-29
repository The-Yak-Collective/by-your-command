"""Tests for the /listenmode wrapper.

The wrapper itself is tiny — it always uses the default 🙊 marker and passes
``enable=None`` (swap) to :func:`bot.commands.mode.apply` — so these tests focus
on the wrapper-specific behaviour: the marker is fixed, the swap-on-tap path
is taken, and the DM guard is preserved. The underlying on/off/turn-on
routing, multi-guild independence, persistence, and sweep are all covered by
``test_mode_command.py`` and ``test_mode_state.py``.

Following the convention in ``test_mode_command.py``, we exercise the wrapper's
*core* (the apply call) via the lower-level functions rather than the cog's
``isinstance(member, discord.Member)`` guard — the guard is tested once in
``test_mode_command.py`` (its rejection path) and re-checked here for the
listenmode-specific version.
"""

import asyncio
import types

from bot.commands import listenmode, mode, mode_state

GUILD_ID = 7


class FakeMember:
    """A member whose nickname can be edited; display_name mirrors Discord's rule."""

    def __init__(self, member_id, *, nick=None, username="User", guild_id=GUILD_ID):
        self.id = member_id
        self.guild = types.SimpleNamespace(id=guild_id)
        self.nick = nick
        self._username = username
        self.edits: list[object] = []

    @property
    def display_name(self):
        return self.nick if self.nick is not None else self._username

    async def edit(self, nick=None):
        self.edits.append(nick)
        self.nick = nick


class FakeResponse:
    def __init__(self):
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


def _interaction(member=None):
    return types.SimpleNamespace(
        user=member if member is not None else types.SimpleNamespace(),
        response=FakeResponse(),
    )


def _record(user_id):
    return mode_state._users_in_guild(mode_state._load_state(), GUILD_ID).get(
        str(user_id)
    )


def test_listenmode_first_call_turns_on_with_default_marker(isolated_store):
    """The wrapper always uses 🙊 and routes through apply's swap path."""
    member = FakeMember(200, nick=None, username="alice")
    interaction = _interaction(member)

    # First call: nothing in state → swap turns it on with the hard-coded 🙊.
    asyncio.run(mode.apply(interaction, member, None, "🙊", None))

    # The default 🙊 is hard-coded; this is the wrapper's whole point.
    assert member.nick == "🙊alice"
    assert _record(200)["char"] == "🙊"
    assert interaction.response.messages
    assert (
        interaction.response.messages[-1][0]
        == "You're in 🙊 mode for the next 90 minutes."
    )


def test_listenmode_second_call_toggles_off(isolated_store):
    """Repeated calls swap the state (the wrapper never takes an enable option)."""
    member = FakeMember(201, nick=None, username="bob")

    # First call: turn on (swap path, no record yet).
    asyncio.run(mode.apply(_interaction(member), member, None, "🙊", None))
    assert member.nick == "🙊bob"

    # Second call: swap → turn off.
    interaction = _interaction(member)
    asyncio.run(mode.apply(interaction, member, None, "🙊", None))
    assert member.nick is None  # restored to "no nickname"
    assert _record(201) is None
    assert interaction.response.messages[-1][0] == "🙊 mode off."


def test_listenmode_respects_custom_minutes(isolated_store):
    """The only parameter is the optional minutes timeout."""
    member = FakeMember(202, nick=None, username="carol")
    interaction = _interaction(member)

    asyncio.run(mode.apply(interaction, member, None, "🙊", 30))

    assert member.nick == "🙊carol"
    assert interaction.response.messages[-1][0] == (
        "You're in 🙊 mode for the next 30 minutes."
    )


def test_listenmode_refuses_use_outside_a_server(isolated_store):
    """DM guard mirrors the /mode cog's behaviour."""
    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(), response=FakeResponse()
    )
    cog = listenmode.ListenMode(bot=None)

    asyncio.run(listenmode.ListenMode.listenmode.callback(cog, interaction, None))

    assert interaction.response.messages
    assert "server" in interaction.response.messages[-1][0].lower()
    assert mode_state._load_state()["guilds"] == {}


def test_listenmode_second_call_uses_default_marker_even_after_custom_mode(
    isolated_store,
):
    """If a previous /mode call set a custom marker, /listenmode swaps off.

    A user who is already in, say, 🔇 mode can call /listenmode to turn it off
    (swap path) and is then free to call /listenmode again to turn the default
    🙊 on. This proves /listenmode doesn't try to be smart about the existing
    marker's identity — it just hands the swap to the shared apply().
    """
    member = FakeMember(203, nick=None, username="dave")

    # First, /mode puts the user in a custom mode.
    asyncio.run(mode._turn_on(_interaction(member), member, "🔇", None))
    assert member.nick == "🔇dave"

    # /listenmode swaps the state off (regardless of which marker was on).
    asyncio.run(mode.apply(_interaction(member), member, None, "🙊", None))
    assert member.nick is None
    assert _record(203) is None

    # And /listenmode again puts the default 🙊 on.
    asyncio.run(mode.apply(_interaction(member), member, None, "🙊", None))
    assert member.nick == "🙊dave"
    assert _record(203)["char"] == "🙊"
