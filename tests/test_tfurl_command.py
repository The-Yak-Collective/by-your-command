"""Command-level tests for /tfurl's authorization boundaries and mention safety.

These drive the real command coroutine (``TfUrl.tfurl.callback``) with small fakes
standing in for Discord objects, so no network or gateway is involved. The fake
channels are registered as virtual ``discord.abc.Messageable`` subclasses so the
command's ``isinstance(..., Messageable)`` guards behave as they would in production.
"""

import asyncio
import types

import discord
import pytest

from bot.commands import tfurl

# IDs used throughout. THIS_GUILD is the guild the slash command is invoked in.
THIS_GUILD = 111
OTHER_GUILD = 999
SOURCE_CHANNEL = 222
MESSAGE_ID = 333
AUTHOR_ID = 444


class FakeChannel(discord.abc.Messageable):
    """A messageable channel/thread in some guild, with controllable permissions.

    Subclassing ``discord.abc.Messageable`` (a plain base class, not an ABC) is what
    makes the command's ``isinstance(..., Messageable)`` guards treat this as a real
    channel without needing a gateway connection.
    """

    def __init__(self, channel_id, guild_id, *, can_view=True, message=None):
        self.id = channel_id
        self.guild = types.SimpleNamespace(id=guild_id)
        self._can_view = can_view
        self._message = message
        self.sent = []  # records (content, allowed_mentions) for each send()

    def permissions_for(self, _user):
        return types.SimpleNamespace(
            view_channel=self._can_view, read_message_history=self._can_view
        )

    async def fetch_message(self, _message_id):
        return self._message

    async def send(self, content, allowed_mentions=None):
        self.sent.append((content, allowed_mentions))


class FakeResponse:
    async def defer(self, ephemeral=False):
        self.deferred = ephemeral


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


class FakeBot:
    """Stand-in bot whose fetch_channel is recorded (and optionally returns a channel)."""

    def __init__(self, fetch_result=None):
        self.fetch_result = fetch_result
        self.fetch_calls = []

    async def fetch_channel(self, channel_id):
        self.fetch_calls.append(channel_id)
        if self.fetch_result is None:
            # Model "channel not found"; the command catches this and refuses. (None of
            # the tests below actually reach this branch — they assert it isn't called.)
            raise ValueError("unknown channel")
        return self.fetch_result


class FakeInteraction:
    def __init__(self, *, guild_id, cached_channels=None, destination=None):
        self.guild_id = guild_id
        self.guild = types.SimpleNamespace(
            id=guild_id,
            get_channel_or_thread=lambda cid: (cached_channels or {}).get(cid),
        )
        self.user = types.SimpleNamespace(id=1)
        self.channel = destination
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def _link(guild_id, channel_id=SOURCE_CHANNEL, message_id=MESSAGE_ID):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _run_tfurl(bot, interaction, link):
    """Invoke the real command coroutine and return the interaction for assertions."""
    cog = tfurl.TfUrl(bot)
    asyncio.run(tfurl.TfUrl.tfurl.callback(cog, interaction, link))
    return interaction


def _was_rejected(interaction, destination):
    """True if the command sent the generic refusal and reposted nothing."""
    return bool(interaction.followup.messages) and not destination.sent


# --- Authorization boundaries ---------------------------------------------------


def test_rejects_link_from_another_guild():
    # A message link whose guild differs from the invoking guild must be refused,
    # and must never reach the cross-guild fetch_channel fallback.
    source = FakeChannel(SOURCE_CHANNEL, OTHER_GUILD)
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD)
    bot = FakeBot()
    interaction = FakeInteraction(guild_id=THIS_GUILD, destination=destination)

    _run_tfurl(bot, interaction, _link(OTHER_GUILD))

    assert _was_rejected(interaction, destination)
    assert bot.fetch_calls == []  # cross-guild disclosure path never taken
    assert source.sent == []


def test_rejects_channel_user_cannot_view():
    # Same guild, but the invoking user lacks permission to read the source channel.
    source = FakeChannel(SOURCE_CHANNEL, THIS_GUILD, can_view=False)
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD)
    bot = FakeBot()
    interaction = FakeInteraction(
        guild_id=THIS_GUILD,
        cached_channels={SOURCE_CHANNEL: source},
        destination=destination,
    )

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert _was_rejected(interaction, destination)


def test_does_not_disclose_via_cross_guild_fetch_fallback():
    # The link's guild matches, but the channel isn't cached, so _resolve_channel
    # fetches it — and the fetched channel turns out to belong to another guild.
    # The guild-membership check must reject it rather than disclose its content.
    foreign = FakeChannel(SOURCE_CHANNEL, OTHER_GUILD, message=object())
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD)
    bot = FakeBot(fetch_result=foreign)
    interaction = FakeInteraction(guild_id=THIS_GUILD, destination=destination)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert bot.fetch_calls == [SOURCE_CHANNEL]  # the fetch happened...
    assert _was_rejected(interaction, destination)  # ...but disclosure did not


def test_unfurls_visible_same_guild_message():
    message = types.SimpleNamespace(
        author=types.SimpleNamespace(id=AUTHOR_ID), content="hello there"
    )
    source = FakeChannel(SOURCE_CHANNEL, THIS_GUILD, message=message)
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD)
    bot = FakeBot()
    interaction = FakeInteraction(
        guild_id=THIS_GUILD,
        cached_channels={SOURCE_CHANNEL: source},
        destination=destination,
    )

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 1
    content, _allowed = destination.sent[0]
    assert f"<@{AUTHOR_ID}>" in content
    assert "hello there" in content


# --- Mention suppression --------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "@everyone get in here",
        "@here look at this",
        "ping <@&5550000> role",
        "hey <@123456> user",
    ],
)
def test_copied_mentions_are_suppressed(body):
    # Whatever the linked content contains, the repost must go out with all mentions
    # suppressed so the bot can never amplify a ping into another channel.
    message = types.SimpleNamespace(
        author=types.SimpleNamespace(id=AUTHOR_ID), content=body
    )
    source = FakeChannel(SOURCE_CHANNEL, THIS_GUILD, message=message)
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD)
    bot = FakeBot()
    interaction = FakeInteraction(
        guild_id=THIS_GUILD,
        cached_channels={SOURCE_CHANNEL: source},
        destination=destination,
    )

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 1
    _content, allowed = destination.sent[0]
    assert isinstance(allowed, discord.AllowedMentions)
    # AllowedMentions.none(): nothing is permitted to actually notify anyone.
    assert allowed.everyone is False
    assert allowed.roles is False
    assert allowed.users is False
