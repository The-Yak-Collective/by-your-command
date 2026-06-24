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


def _http_error(status=413):
    """Build a discord.HTTPException like the API raises when an upload is too big."""
    response = types.SimpleNamespace(status=status, reason="Payload Too Large")
    return discord.HTTPException(response, "request entity too large")


class FakeAttachment:
    """Stand-in for discord.Attachment that records how it was re-uploaded.

    ``to_file`` returns a lightweight object in place of a real ``discord.File`` (the
    command only forwards it to ``send``, which the fakes merely record) and remembers
    the spoiler flag it was called with, so a test can assert spoilers are preserved.
    Pass ``fail=True`` to model an attachment whose bytes can't be fetched at all.
    """

    def __init__(self, filename, *, spoiler=False, fail=False):
        self.filename = filename
        self._spoiler = spoiler
        self._fail = fail
        self.to_file_spoiler = None  # the spoiler arg to_file() was last called with

    def is_spoiler(self):
        return self._spoiler

    async def to_file(self, *, spoiler=False):
        if self._fail:
            raise _http_error()
        self.to_file_spoiler = spoiler
        return types.SimpleNamespace(filename=self.filename, spoiler=spoiler)


class FakeSticker:
    """Stand-in for discord.StickerItem.

    Image stickers (png/apng/gif) return bytes from ``read()`` and are re-uploaded as
    files. Discord's standard stickers use the Lottie (vector JSON) format, which isn't
    a renderable image, so the command represents those as text instead. Pass
    ``fail=True`` to model a sticker whose bytes can't be fetched.
    """

    def __init__(self, name, fmt=discord.StickerFormatType.png, *, fail=False):
        self.name = name
        self.format = fmt
        self._fail = fail

    async def read(self):
        if self._fail:
            raise _http_error()
        return b"sticker-bytes"


class FakeChannel(discord.abc.Messageable):
    """A messageable channel/thread in some guild, with controllable permissions.

    Subclassing ``discord.abc.Messageable`` (a plain base class, not an ABC) is what
    makes the command's ``isinstance(..., Messageable)`` guards treat this as a real
    channel without needing a gateway connection.
    """

    def __init__(
        self, channel_id, guild_id, *, can_view=True, message=None, reject_files=False
    ):
        self.id = channel_id
        self.guild = types.SimpleNamespace(id=guild_id)
        self._can_view = can_view
        self._message = message
        # When set, send() rejects any message carrying files, modelling an
        # attachment that exceeds this channel's upload limit.
        self._reject_files = reject_files
        # One record per send(): .content/.files/.embeds/.allowed_mentions.
        self.sent = []

    def permissions_for(self, _user):
        return types.SimpleNamespace(
            view_channel=self._can_view, read_message_history=self._can_view
        )

    async def fetch_message(self, _message_id):
        return self._message

    async def send(
        self, content=None, *, files=None, embeds=None, allowed_mentions=None
    ):
        if self._reject_files and files:
            raise _http_error()
        self.sent.append(
            types.SimpleNamespace(
                content=content,
                files=files,
                embeds=embeds,
                allowed_mentions=allowed_mentions,
            )
        )


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


def _make_message(content="", *, attachments=(), embeds=(), stickers=()):
    """Build a fake source message with the fields /tfurl reads off it."""
    return types.SimpleNamespace(
        author=types.SimpleNamespace(id=AUTHOR_ID),
        content=content,
        attachments=list(attachments),
        embeds=list(embeds),
        stickers=list(stickers),
    )


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
    message = _make_message("hello there")
    source = FakeChannel(SOURCE_CHANNEL, THIS_GUILD, message=message)
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD)
    bot = FakeBot()
    interaction = FakeInteraction(
        guild_id=THIS_GUILD,
        cached_channels={SOURCE_CHANNEL: source},
        destination=destination,
    )

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    # A plain-text message sends exactly once — there's no media to follow it.
    assert len(destination.sent) == 1
    record = destination.sent[0]
    assert f"<@{AUTHOR_ID}>" in record.content
    assert "hello there" in record.content


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
    message = _make_message(body)
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
    allowed = destination.sent[0].allowed_mentions
    assert isinstance(allowed, discord.AllowedMentions)
    # AllowedMentions.none(): nothing is permitted to actually notify anyone.
    assert allowed.everyone is False
    assert allowed.roles is False
    assert allowed.users is False


# --- Attachments and embeds -----------------------------------------------------


def _visible_setup(message, *, reject_files=False):
    """Wire a visible same-guild source + destination for the media tests."""
    source = FakeChannel(SOURCE_CHANNEL, THIS_GUILD, message=message)
    destination = FakeChannel(SOURCE_CHANNEL, THIS_GUILD, reject_files=reject_files)
    bot = FakeBot()
    interaction = FakeInteraction(
        guild_id=THIS_GUILD,
        cached_channels={SOURCE_CHANNEL: source},
        destination=destination,
    )
    return destination, bot, interaction


def test_reuploads_attachments_below_the_text():
    # An uploaded image must be re-attached to the repost, not dropped: the text goes
    # out first, then a second message carrying the re-uploaded file.
    attachment = FakeAttachment("cat.png")
    message = _make_message("look at this", attachments=[attachment])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 2
    text, media = destination.sent
    assert "look at this" in text.content
    assert media.files is not None and len(media.files) == 1
    assert media.files[0].filename == "cat.png"
    # Nothing was lost, so the invoker just gets the plain confirmation.
    assert interaction.followup.messages[-1][0] == "Done — unfurled below."


def test_reupload_preserves_spoiler_flag():
    # A spoiler-tagged attachment must stay spoilered: to_file() strips the flag by
    # default, so the command has to pass spoiler=True explicitly.
    attachment = FakeAttachment("secret.png", spoiler=True)
    message = _make_message(attachments=[attachment])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert attachment.to_file_spoiler is True
    assert destination.sent[-1].files[0].spoiler is True


def test_forwards_rich_embeds_but_not_auto_previews():
    # Bot/webhook-authored ("rich") embeds are forwarded; Discord's own auto-generated
    # link/image previews are not (Discord refuses to re-post them, and regenerates
    # them from any URL left in the copied text).
    rich = types.SimpleNamespace(type="rich")
    auto = types.SimpleNamespace(type="image")
    message = _make_message("see https://example.com", embeds=[rich, auto])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert destination.sent[-1].embeds == [rich]


def test_auto_preview_alone_sends_no_media_message():
    # If the only embed is an auto-generated preview, there's nothing to forward, so
    # the command must not emit an empty trailing media message.
    auto = types.SimpleNamespace(type="image")
    message = _make_message("https://example.com", embeds=[auto])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 1  # just the text


def test_oversize_attachment_is_reported_not_fatal():
    # A file too large to re-upload must not sink the whole unfurl: the text still
    # posts, any rich embed is still delivered, and the invoker is told privately.
    big = FakeAttachment("huge.zip")
    rich = types.SimpleNamespace(type="rich")
    message = _make_message("my files", attachments=[big], embeds=[rich])
    destination, bot, interaction = _visible_setup(message, reject_files=True)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    # The copied text went out, and no surviving send carries the rejected file.
    contents = [record.content for record in destination.sent]
    assert any("my files" in (content or "") for content in contents)
    assert all(record.files is None for record in destination.sent)
    # The rich embed still made it through on the files-less retry.
    assert any(record.embeds == [rich] for record in destination.sent)
    # And the shortfall is disclosed to the invoker, not silently swallowed.
    assert "couldn't be reposted" in interaction.followup.messages[-1][0]


def test_failed_attachment_download_is_counted():
    # If an attachment's bytes can't be fetched at all, it's skipped (not fatal) and
    # reported, while a sibling attachment that downloads fine is still reposted.
    good = FakeAttachment("ok.png")
    broken = FakeAttachment("gone.png", fail=True)
    message = _make_message("two files", attachments=[good, broken])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    media = destination.sent[-1]
    assert [file.filename for file in media.files] == ["ok.png"]
    assert "couldn't be reposted" in interaction.followup.messages[-1][0]


# --- Stickers -------------------------------------------------------------------


def test_image_sticker_is_reuploaded_as_a_file():
    # A custom (image) sticker must be re-uploaded so it renders, named after itself.
    sticker = FakeSticker("partyblob", discord.StickerFormatType.gif)
    message = _make_message("woo", stickers=[sticker])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 2
    text, media = destination.sent
    assert "woo" in text.content
    assert [file.filename for file in media.files] == ["partyblob.gif"]
    # It rendered as an image, so no text marker and no shortfall reported.
    assert "[sticker:" not in text.content
    assert interaction.followup.messages[-1][0] == "Done — unfurled below."


def test_lottie_sticker_is_shown_as_text_marker():
    # A standard (Lottie) sticker can't be re-rendered, so it appears as a text marker
    # in the repost — and no empty media message is sent.
    sticker = FakeSticker("Wumpus", discord.StickerFormatType.lottie)
    message = _make_message(stickers=[sticker])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 1
    assert "[sticker: Wumpus]" in destination.sent[0].content
    # It's shown (as text), not dropped, so the invoker gets the plain confirmation.
    assert interaction.followup.messages[-1][0] == "Done — unfurled below."


def test_unreadable_sticker_falls_back_to_text_marker():
    # An image sticker whose bytes won't download degrades to the same text marker
    # rather than vanishing or aborting the unfurl.
    sticker = FakeSticker("brokenblob", discord.StickerFormatType.png, fail=True)
    message = _make_message("hi", stickers=[sticker])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 1  # text only; nothing re-uploaded
    assert "[sticker: brokenblob]" in destination.sent[0].content


def test_image_and_lottie_stickers_together():
    # A message mixing both kinds: the image sticker is re-uploaded, the Lottie one is
    # named in text, in a single coherent repost.
    image = FakeSticker("catjam", discord.StickerFormatType.apng)
    lottie = FakeSticker("Wave", discord.StickerFormatType.lottie)
    message = _make_message("hey", stickers=[image, lottie])
    destination, bot, interaction = _visible_setup(message)

    _run_tfurl(bot, interaction, _link(THIS_GUILD))

    assert len(destination.sent) == 2
    text, media = destination.sent
    assert "[sticker: Wave]" in text.content
    # APNG re-uploads with a .png extension.
    assert [file.filename for file in media.files] == ["catjam.png"]
