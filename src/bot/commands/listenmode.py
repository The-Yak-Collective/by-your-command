"""/listenmode — a simplified wrapper over /mode that always uses 🙊.

A user-facing alternative to the more configurable ``/mode`` command: it always
prepends the "speak no evil" 🙊 marker, and has no ``enable`` option — calling it
repeatedly simply toggles the state, so the only thing to think about is "do I
want the marker on right now?". The single optional parameter is the auto-remove
timeout in minutes (default 90, same as ``/mode``).

This module is a thin wrapper over :mod:`bot.commands.mode` (specifically, its
:func:`~bot.commands.mode.apply` entry point). All state and logic — including
the persistent ``modes.json``, the multi-guild independence, the maintenance
sweep, and the first-boot scan — live there, so ``/listenmode`` adds no new
behaviour beyond hard-coding the marker and the swap-on-tap behaviour. Passing
``None`` for ``apply``'s ``enable`` argument is the existing "swap current state"
semantic, which is exactly what a tap-to-toggle UX needs.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .mode import DEFAULT_CHAR, apply


class ListenMode(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    @app_commands.checks.cooldown(3, 60.0)
    @app_commands.command(
        name="listenmode",
        description='Tap to toggle the "speak no evil" 🙊 marker on your nickname.',
    )
    @app_commands.describe(
        minutes="Minutes until the marker auto-removes (optional, default 90).",
    )
    async def listenmode(
        self,
        interaction: discord.Interaction,
        minutes: int | None = None,
    ) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            # Nicknames only exist within a server, not in DMs.
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        # ``enable=None`` triggers apply's swap path, so a second call turns the
        # marker off again. The marker is hard-coded to the default 🙊 — this is
        # the only behavioural difference from ``/mode``.
        await apply(interaction, member, None, DEFAULT_CHAR, minutes)


async def setup(bot: commands.Bot) -> None:
    # No maintenance registration here: bot.commands.mode owns the single source of
    # state and registers the startup scan + periodic sweep. Adding only the cog
    # keeps the sweep from being double-registered.
    await bot.add_cog(ListenMode(bot))
