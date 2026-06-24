"""/tfurl — "unfurl" a Discord message link by reposting its content in-channel.

Given a link to a message, the bot fetches that message and reposts it in the current
channel, attributed to the original author and source channel. The text, any uploaded
attachments (re-uploaded so they render permanently), any rich (bot/webhook) embeds,
and any stickers are all carried across — image stickers re-uploaded, and Discord's
non-image (Lottie) stickers named in text. This is a rewrite of the legacy slashayak
``tfurl`` command, which copied only the text.

Disclosure is deliberately constrained: the command only unfurls messages from the
*current* server, and only when the person invoking it could already read the source
channel themselves. A user therefore cannot use the bot to surface a private channel
in another server (or one hidden from them in this one). Copied content is also sent
with mentions suppressed, so a linked message can never make the bot ping anyone.

To add another command, copy this file, rename the cog class and the command, and
edit the body — :mod:`bot.client` auto-discovers it on the next start.
"""

from __future__ import annotations

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..utils import parse_message_link, splitsend

log = logging.getLogger(__name__)


def _user_can_view(user: discord.abc.User, channel: object) -> bool:
    """True if ``user`` is allowed to read ``channel``.

    We gate disclosure on the *invoking user's* permissions, not the bot's: the bot
    may be able to see a channel the user cannot, and unfurling it would leak content.
    A channel the user can neither view nor read history from is treated as off-limits.
    """
    permissions_for = getattr(channel, "permissions_for", None)
    if permissions_for is None:
        return False
    perms = permissions_for(user)
    return bool(perms.view_channel and perms.read_message_history)


class TfUrl(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    @app_commands.command(
        name="tfurl",
        description="Unfurl a Discord message link by reposting its content here.",
    )
    @app_commands.describe(link="A link to the Discord message you want to unfurl.")
    async def tfurl(self, interaction: discord.Interaction, link: str) -> None:
        # Fetching the linked message can exceed Discord's 3-second reply window, so
        # acknowledge privately first, then follow up once the work is done.
        await interaction.response.defer(ephemeral=True)
        try:
            guild_id, channel_id, message_id = parse_message_link(link)
            # Only unfurl messages from *this* server. Refusing cross-guild links is
            # what stops the bot being used to read a private channel elsewhere.
            if interaction.guild_id is None or guild_id != interaction.guild_id:
                raise ValueError("message link is not for the current server")
            channel = await self._resolve_channel(interaction, channel_id)
            # Don't disclose a channel the invoking user couldn't read themselves.
            if not _user_can_view(interaction.user, channel):
                raise PermissionError("invoking user cannot view the source channel")
            message = await channel.fetch_message(message_id)
        except (ValueError, PermissionError, discord.HTTPException) as exc:
            # One deliberately vague message for every failure, so we never confirm
            # whether a channel the user can't see actually exists.
            log.info("tfurl could not resolve %r: %s", link, exc)
            await interaction.followup.send(
                "Sorry — are you sure that's a link to a message in this server "
                "that you can see?",
                ephemeral=True,
            )
            return

        destination = interaction.channel
        if not isinstance(destination, discord.abc.Messageable):
            # Practically unreachable — a slash command is invoked from a messageable
            # channel — but this keeps us from posting into a category/forum/None.
            await interaction.followup.send(
                "I can't repost into this kind of channel.", ephemeral=True
            )
            return

        # message.content is *only* the text the author typed; uploaded files, rich
        # preview cards, and stickers hang off separate fields. Gather everything we
        # mean to repost — downloading attachments and image stickers — before posting
        # anything, so the text can name any sticker we can't re-render as an image.
        files, rich_embeds, sticker_notes, dropped = await self._collect_media(message)

        # Attribute the repost to its author and source channel. Stickers we can't
        # reproduce as an image (Discord's standard, Lottie ones) are shown as a text
        # marker so the channel still sees that a sticker was sent.
        body = f"<@{message.author.id}> in <#{channel_id}>:\n{message.content}"
        if sticker_notes:
            body += "\n" + " ".join(f"[sticker: {name}]" for name in sticker_notes)

        # Post the text first: it anchors the unfurl and, unlike re-uploaded files,
        # can never fail on a size limit. Every send uses AllowedMentions.none(), so
        # the <@id>/<#id> attribution and any mentions copied from the body render as
        # text but notify nobody — a linked message can't make the bot ping people.
        await splitsend(
            destination, body, allowed_mentions=discord.AllowedMentions.none()
        )
        dropped += await self._send_media(destination, files, rich_embeds)

        confirmation = "Done — unfurled below."
        if dropped:
            # Tell the invoker (privately) about anything we couldn't reproduce —
            # most often a file too large for this channel — rather than dropping it
            # without a trace.
            confirmation += f" ({dropped} attachment(s) couldn't be reposted here.)"
        await interaction.followup.send(confirmation, ephemeral=True)

    async def _collect_media(
        self, message: discord.Message
    ) -> tuple[list[discord.File], list[discord.Embed], list[str], int]:
        """Gather everything to repost beneath the copied text.

        Returns ``(files, rich_embeds, sticker_notes, dropped)``:

        * ``files`` — attachments and image stickers re-fetched as uploadable files.
          Attachments are re-uploaded rather than linked so they render permanently,
          instead of via a CDN URL that Discord now signs and expires within a day; a
          spoiler-marked attachment stays spoilered.
        * ``rich_embeds`` — only *rich* (bot/webhook) embeds. Discord refuses to
          re-post its own auto-generated link/image previews and regenerates them for
          free from any URL left in the copied text.
        * ``sticker_notes`` — names of stickers we can't reproduce as an image (the
          standard Lottie stickers, or any that won't download); the caller shows
          these as text so the channel still sees them.
        * ``dropped`` — count of attachments whose bytes couldn't be fetched at all.
        """
        files: list[discord.File] = []
        dropped = 0
        for attachment in message.attachments:
            try:
                # to_file() strips the spoiler flag by default, so set it explicitly.
                files.append(await attachment.to_file(spoiler=attachment.is_spoiler()))
            except discord.HTTPException as exc:
                log.info(
                    "tfurl: could not fetch attachment %r: %s", attachment.filename, exc
                )
                dropped += 1

        sticker_notes: list[str] = []
        for sticker in message.stickers:
            sticker_file = await self._sticker_to_file(sticker)
            if sticker_file is None:
                sticker_notes.append(sticker.name)
            else:
                files.append(sticker_file)

        rich_embeds = [embed for embed in message.embeds if embed.type == "rich"]
        return files, rich_embeds, sticker_notes, dropped

    async def _sticker_to_file(
        self, sticker: discord.StickerItem
    ) -> discord.File | None:
        """Re-download a sticker as an uploadable image, or ``None`` if it isn't one.

        Custom guild stickers (PNG/APNG/GIF) come back as a :class:`discord.File` named
        after the sticker. Discord's standard stickers are Lottie (vector JSON), not an
        image we can render, so those return ``None`` — as does any sticker whose bytes
        can't be fetched.
        """
        if sticker.format is discord.StickerFormatType.lottie:
            return None
        try:
            data = await sticker.read()
        except discord.HTTPException as exc:
            log.info("tfurl: could not fetch sticker %r: %s", sticker.name, exc)
            return None
        filename = f"{sticker.name}.{sticker.format.file_extension}"
        return discord.File(io.BytesIO(data), filename=filename)

    async def _send_media(
        self,
        destination: discord.abc.Messageable,
        files: list[discord.File],
        rich_embeds: list[discord.Embed],
    ) -> int:
        """Send the re-uploaded files and forwarded embeds beneath the copied text.

        Sent as a single trailing message so a size-limit rejection can't duplicate or
        block the text already posted. Returns the number of files dropped because the
        message was rejected (most often a re-uploaded attachment too large for this
        channel); any rich embeds are still delivered on a files-less retry.
        """
        if not files and not rich_embeds:
            return 0

        # Keep mentions suppressed here too. An embed can't ping on its own, but
        # applying AllowedMentions.none() to every send keeps the "a linked message
        # can never make the bot notify anyone" guarantee uniform. We branch on what's
        # present rather than passing empty lists, since the typed API rejects ``None``.
        suppress = discord.AllowedMentions.none()
        try:
            if files and rich_embeds:
                await destination.send(
                    files=files, embeds=rich_embeds, allowed_mentions=suppress
                )
            elif files:
                await destination.send(files=files, allowed_mentions=suppress)
            else:
                await destination.send(embeds=rich_embeds, allowed_mentions=suppress)
        except discord.HTTPException as exc:
            log.info("tfurl: could not repost files, dropping them: %s", exc)
            if rich_embeds:
                await destination.send(embeds=rich_embeds, allowed_mentions=suppress)
            return len(files)
        return 0

    async def _resolve_channel(
        self, interaction: discord.Interaction, channel_id: int
    ) -> discord.abc.Messageable:
        """Return the messageable channel/thread for ``channel_id`` in this guild.

        We try the interaction guild's cache first (cheap), then fall back to an API
        fetch. Either way we verify the resolved channel actually belongs to this
        guild before returning it: the ``fetch_channel`` fallback can otherwise reach
        any channel in any server the bot has joined, which is exactly the cross-guild
        disclosure we must prevent. Channels that can't hold messages (categories,
        forums) are rejected too. Raises ``ValueError`` for any of these cases.
        """
        guild = interaction.guild
        channel = guild.get_channel_or_thread(channel_id) if guild else None
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        channel_guild_id = getattr(getattr(channel, "guild", None), "id", None)
        if channel_guild_id is None or channel_guild_id != interaction.guild_id:
            raise ValueError("resolved channel is not in the current guild")
        if not isinstance(channel, discord.abc.Messageable):
            raise ValueError("resolved channel cannot contain messages")
        return channel


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TfUrl(bot))
