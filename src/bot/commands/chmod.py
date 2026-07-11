"""/chmod — toggle a mode marker emoji on your nickname.

Turning the mode on prepends a marker (default 🙊) to your server nickname so others
can see your current mode; it auto-removes after a timeout (default 90 minutes). This
is a rewrite of the legacy slashayak ``showmymode`` command with the timeout
*actually implemented*, backed by persistent state so it survives the bot's nightly
restart.

This module is the command cog and the on/off/swap routing. Persistent state lives
in :mod:`bot.commands.chmod_state`; the maintenance sweep lives in
:mod:`bot.commands.chmod_maintenance`. The deprecated ``/showmymode`` command
(kept only so existing users get a nudge to switch) is a thin shim in
:mod:`bot.commands.showmymode` that maps its old on/off choice onto :func:`apply`
here. When ``/showmymode`` is eventually removed, deleting that one file leaves
this core untouched.
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from .. import maintenance
from ..utils import add_mode_prefix, resolve_duration_minutes
from . import chmod_state
from .chmod_maintenance import _nick_to_restore, _scan_on_first_boot, _sweep_expired
from .chmod_state import (
    DEFAULT_CHAR,
    DEFAULT_DURATION_MINUTES,
    STATE_NAMESPACE,
    _drop_user,
    _ensure_guild_users,
    _load_state,
    _save_state,
    _users_in_guild,
)

log = logging.getLogger(__name__)

# Upper bound on the auto-remove timeout. Without a cap, a very large value would
# leave a marker effectively forever and keep stale state hanging around indefinitely.
MAX_DURATION_MINUTES = 7 * 24 * 60  # one week


def _edit_error_message(exc: Exception) -> str:
    """A friendly explanation for why editing a nickname might have failed."""
    return (
        "I couldn't change your nickname. I need the **Manage Nicknames** permission "
        "and a role above yours, and Discord never lets anyone edit the server "
        f"owner's nickname. (Details: {exc})"
    )


async def _turn_on(
    interaction: discord.Interaction,
    member: discord.Member,
    char: str,
    minutes: int | None,
) -> None:
    try:
        duration = resolve_duration_minutes(
            minutes,
            DEFAULT_DURATION_MINUTES,
            MAX_DURATION_MINUTES,
            minimum=maintenance.TICK_INTERVAL_MINUTES,
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    state = _load_state(custom_store=chmod_state.store)
    users = _ensure_guild_users(state, member.guild.id)
    existing = users.get(str(member.id))
    if existing is not None and "original_nick" in existing:
        original_nick = existing["original_nick"]
    else:
        original_nick = member.nick

    new_nick = add_mode_prefix(member.display_name, char)
    try:
        await member.edit(nick=new_nick)
    except discord.HTTPException as exc:
        await interaction.response.send_message(
            _edit_error_message(exc), ephemeral=True
        )
        return

    users[str(member.id)] = {
        "char": char,
        "expires_at": int(time.time()) + duration * 60,
        "original_nick": original_nick,
    }
    _save_state(state, custom_store=chmod_state.store)
    await interaction.response.send_message(
        f"You're in {char} mode for the next {duration} minutes.", ephemeral=True
    )


async def _turn_off(interaction: discord.Interaction, member: discord.Member) -> None:
    state = _load_state(custom_store=chmod_state.store)
    users = _users_in_guild(state, member.guild.id)
    record = users.get(str(member.id))

    if record is None:
        await interaction.response.send_message(
            "You don't have a mode marker on right now.", ephemeral=True
        )
        return

    char = record["char"]

    restored_nick = _nick_to_restore(record, member, char)
    try:
        await member.edit(nick=restored_nick)
    except discord.HTTPException as exc:
        await interaction.response.send_message(
            _edit_error_message(exc), ephemeral=True
        )
        return

    _drop_user(state, member.guild.id, str(member.id))
    _save_state(state, custom_store=chmod_state.store)
    await interaction.response.send_message(f"{char} mode off.", ephemeral=True)


async def _swap(
    interaction: discord.Interaction,
    member: discord.Member,
    char: str,
    minutes: int | None,
) -> None:
    """Toggle the marker: off if currently active in this guild, on if not.

    "Active" is decided by the persisted state record *for this guild* — its
    presence is what :func:`_turn_on` writes and :func:`_turn_off`/the sweep clear,
    so the toggle stays consistent with the rest of the state model. ``char``/
    ``minutes`` apply only when turning on; turning off restores the prior nickname
    from the record. Scoping the check to this guild is the multi-guild fix: a
    marker active in another guild no longer causes a swap here to turn the wrong
    guild off.
    """
    state = _load_state(custom_store=chmod_state.store)
    if str(member.id) in _users_in_guild(state, member.guild.id):
        await _turn_off(interaction, member)
    else:
        await _turn_on(interaction, member, char, minutes)


async def apply(
    interaction: discord.Interaction,
    member: discord.Member,
    enable: bool | None,
    char: str,
    minutes: int | None,
) -> None:
    """Route a mode-change request from its ``enable`` option to on/off/swap.

    ``True`` forces on, ``False`` forces off, and ``None`` (the option was omitted)
    swaps the caller's current state. This is the shared entry point for actually
    applying a mode change: the ``/chmod`` cog calls it directly, and the deprecated
    ``/showmymode`` shim (in :mod:`bot.commands.showmymode`) maps its old on/off choice
    onto a ``bool`` and calls it too. Splitting it out keeps the routing unit-testable
    without a real ``discord.Member`` (the cog methods own only the in-server guard and
    the marker-character extraction).
    """
    if enable is True:
        await _turn_on(interaction, member, char, minutes)
    elif enable is False:
        await _turn_off(interaction, member)
    else:
        await _swap(interaction, member, char, minutes)


class Chmod(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    @app_commands.command(
        name="chmod",
        description="Toggle a marker (default 🙊) on your nickname to show your current mode.",
    )
    @app_commands.describe(
        enable="Turn the marker on (true) or off (false); omit to swap the current state.",
        mode="A single character to use instead of 🙊 (optional).",
        minutes="Minutes until the marker auto-removes (optional, default 90).",
    )
    async def chmod(
        self,
        interaction: discord.Interaction,
        enable: bool | None = None,
        mode: str | None = None,
        minutes: int | None = None,
    ) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        char = (mode or DEFAULT_CHAR)[0]
        await apply(interaction, member, enable, char, minutes)


async def setup(bot: commands.Bot) -> None:
    maintenance.register_startup(f"{STATE_NAMESPACE}-scan", _scan_on_first_boot)
    maintenance.register_periodic(f"{STATE_NAMESPACE}-sweep", _sweep_expired)
    await bot.add_cog(Chmod(bot))
