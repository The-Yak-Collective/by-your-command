"""/tfurl — "unfurl" a Discord message link by reposting its content in-channel.

Given a link to a message, the bot fetches that message and reposts its text in the
current channel, attributed to the original author and source channel. This is a
rewrite of the legacy slashayak ``tfurl`` command.

Disclosure is deliberately constrained: the command only unfurls messages from the
*current* server, and only when the person invoking it could already read the source
channel themselves. A user therefore cannot use the bot to surface a private channel
in another server (or one hidden from them in this one). Copied content is also sent
with mentions suppressed, so a linked message can never make the bot ping anyone.

To add another command, copy this file, rename the cog class and the command, and
edit the body — :mod:`bot.client` auto-discovers it on the next start.
"""

from __future__ import annotations

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
        self.bot = bot

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

        # Repost the content, attributed to its author and source channel. We send
        # with AllowedMentions.none(): the <@id>/<#id> attribution tokens and any
        # @everyone/@here/role/user mentions copied from the body all render as text
        # but notify nobody, so a linked message can't make the bot ping people.
        body = f"<@{message.author.id}> in <#{channel_id}>:\n{message.content}"
        destination = interaction.channel
        if not isinstance(destination, discord.abc.Messageable):
            # Practically unreachable — a slash command is invoked from a messageable
            # channel — but this keeps us from posting into a category/forum/None.
            await interaction.followup.send(
                "I can't repost into this kind of channel.", ephemeral=True
            )
            return
        await splitsend(
            destination, body, allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.followup.send("Done — unfurled above.", ephemeral=True)

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
