"""/showmymode — DEPRECATED; use /chmod instead.

This is a thin shim that preserves the old ``/showmymode`` command (same options and
behaviour) so existing users aren't left without it, while nudging them toward
``/chmod``. It holds none of the feature's state or logic: everything lives in
:mod:`bot.commands.chmod`, and this module only maps the old on/off choice onto
``/chmod``'s ``enable`` boolean (calling the shared :func:`~bot.commands.chmod.apply`)
and follows up with a private deprecation notice.

When the migration is complete, deleting this one file removes ``/showmymode`` without
touching the core or its persisted state.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from .chmod import DEFAULT_CHAR, apply

# Shown only to the invoker (ephemeral followup) so it never clutters the channel.
DEPRECATION_MESSAGE = (
    "⚠️ `/showmymode` is deprecated and will be removed in a future release. "
    "Please switch to `/chmod` — it has the same effect with a clearer name, and its "
    "`enable` option can be omitted to swap your current state."
)


async def _run_showmymode(
    interaction: discord.Interaction,
    member: discord.Member,
    onoff: Choice[int],
    char: str,
    minutes: int | None,
) -> None:
    """Apply the old on/off choice, then deliver the deprecation notice.

    The old ``on``/``off`` choice maps directly onto ``/chmod``'s ``enable`` boolean
    (``on`` → ``True``, ``off`` → ``False``), so this reuses :func:`bot.commands.chmod.apply`
    rather than duplicating the on/off logic. ``apply`` sends the result as the initial
    (ephemeral) response, so the deprecation goes out as an ephemeral *followup* right
    beneath it — visible only to the invoker. It is sent on every path (success or
    error) so the nudge reaches the user regardless of whether the mode change took.
    """
    enable = onoff.value == 1  # on -> True, off -> False
    await apply(interaction, member, enable, char, minutes)
    await interaction.followup.send(DEPRECATION_MESSAGE, ephemeral=True)


class ShowMyMode(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    @app_commands.command(
        name="showmymode",
        description="[deprecated] Toggle a marker (default 🙊) on your nickname to show your current mode.",
    )
    @app_commands.describe(
        onoff="Turn the marker on or off.",
        monkeychar="A single character to use instead of 🙊 (optional).",
        minutes="Minutes until the marker auto-removes (optional, default 90).",
    )
    @app_commands.choices(
        onoff=[Choice(name="on", value=1), Choice(name="off", value=2)]
    )
    async def showmymode(
        self,
        interaction: discord.Interaction,
        onoff: Choice[int],
        monkeychar: str | None = None,
        minutes: int | None = None,
    ) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            # Nicknames only exist within a server, not in DMs.
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        # Use only the first character, so a pasted multi-character string still
        # yields a single marker.
        char = (monkeychar or DEFAULT_CHAR)[0]
        await _run_showmymode(interaction, member, onoff, char, minutes)


async def setup(bot: commands.Bot) -> None:
    # No maintenance registration here: bot.commands.chmod owns the single source of
    # state and registers the startup scan + periodic sweep. Adding only the cog keeps
    # the sweep from being double-registered.
    await bot.add_cog(ShowMyMode(bot))
