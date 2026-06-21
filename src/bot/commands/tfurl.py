"""/tfurl — "unfurl" a Discord message link by reposting its content in-channel.

Given a link to a message, the bot fetches that message and reposts its text in the
current channel, attributed to the original author and source channel. This is a
multi-guild-safe rewrite of the legacy slashayak ``tfurl`` command.

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
            _guild_id, channel_id, message_id = parse_message_link(link)
            channel = await self._resolve_channel(interaction, channel_id)
            message = await channel.fetch_message(message_id)
        except (ValueError, discord.HTTPException) as exc:
            log.info("tfurl could not resolve %r: %s", link, exc)
            await interaction.followup.send(
                "Sorry — are you sure that's a link to a Discord message I can see?",
                ephemeral=True,
            )
            return

        # Repost the content, attributed to its author and source channel. The
        # <@id> / <#id> tokens render as proper mentions in Discord.
        body = f"<@{message.author.id}> in <#{channel_id}>:\n{message.content}"
        await splitsend(interaction.channel, body)
        await interaction.followup.send("Done — unfurled above.", ephemeral=True)

    async def _resolve_channel(self, interaction: discord.Interaction, channel_id: int):
        """Return the channel/thread for ``channel_id``, fetching it if uncached.

        We try the interaction's own guild cache first (cheap), then fall back to an
        API fetch. Using ``interaction.guild`` rather than the legacy ``guilds[0]``
        makes this correct when the bot is in more than one server.
        """
        guild = interaction.guild
        channel = guild.get_channel_or_thread(channel_id) if guild else None
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TfUrl(bot))
