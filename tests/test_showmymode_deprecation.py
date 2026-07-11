"""Tests for the deprecated /showmymode shim: it still works AND nudge-to-/chmod.

The shared on/off logic is covered by test_chmod_command.py; here we only assert the
shim's own concerns — that invoking it runs the mode change and then sends a private
deprecation followup pointing at /chmod — using fakes for the member and interaction.
"""

import asyncio
import types

from bot.commands import showmymode

GUILD_ID = 7


class FakeMember:
    def __init__(self, member_id, *, nick=None, username="User"):
        self.id = member_id
        self.guild = types.SimpleNamespace(id=GUILD_ID)
        self.nick = nick
        self._username = username
        self.edits = []

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


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


class FakeInteraction:
    def __init__(self, member):
        self.user = member
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def _choice(value):
    return types.SimpleNamespace(value=value)


def test_showmymode_on_marks_nick_and_sends_deprecation(isolated_store):
    member = FakeMember(60, nick=None, username="Alice")
    interaction = FakeInteraction(member)

    asyncio.run(showmymode._run_showmymode(interaction, member, _choice(1), "🙊", None))

    assert member.nick == "🙊Alice"
    assert interaction.response.messages
    _result, result_eph = interaction.response.messages[-1]
    assert result_eph is True
    assert _result == "You're in 🙊 mode for the next 90 minutes."
    assert interaction.followup.messages
    msg, followup_eph = interaction.followup.messages[-1]
    assert followup_eph is True
    assert "/chmod" in msg


def test_showmymode_off_restores_nick_and_sends_deprecation(isolated_store):
    member = FakeMember(61, nick="Bob", username="bob")
    asyncio.run(
        showmymode._run_showmymode(
            FakeInteraction(member), member, _choice(1), "🙊", None
        )
    )

    interaction = FakeInteraction(member)
    asyncio.run(showmymode._run_showmymode(interaction, member, _choice(2), "🙊", None))

    assert member.nick == "Bob"  # restored
    assert interaction.response.messages[-1][0] == "🙊 mode off."
    assert interaction.followup.messages
    assert "/chmod" in interaction.followup.messages[-1][0]


def test_showmymode_refuses_use_outside_a_server(isolated_store):
    interaction = FakeInteraction(types.SimpleNamespace())
    cog = showmymode.ShowMyMode(bot=None)

    asyncio.run(
        showmymode.ShowMyMode.showmymode.callback(
            cog, interaction, _choice(1), None, None
        )
    )

    assert interaction.response.messages
    assert "server" in interaction.response.messages[-1][0].lower()
    assert interaction.followup.messages == []  # no followup on the DM bail
