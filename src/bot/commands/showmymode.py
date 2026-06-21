"""/showmymode — toggle a "listen mode" marker emoji on your nickname.

Turning the mode on prepends a marker (default 🙊) to your server nickname so others
can see you're in listen-only mode; it auto-removes after a timeout (default 90
minutes). This is a rewrite of the legacy slashayak ``showmymode`` command with the
timeout *actually implemented*, backed by persistent state so it survives the bot's
nightly restart.

State model
-----------
Per user we persist the marker character they used and the absolute Unix time at
which it should be removed (``expires_at``). Two maintenance actions, registered with
the bot's central sweep (:mod:`bot.maintenance`), act on this state:

* a one-time **startup scan** that, on the very first run (no state file yet), adopts
  everyone already wearing the default marker and gives them a fresh timeout; and
* a **periodic sweep** that removes the marker once a user's time has elapsed.

The state file is never deleted: its existence is how we know the startup scan has
already run and need not repeat after a restart.
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from .. import maintenance
from ..state import JSONStore
from ..utils import (
    add_mode_prefix,
    expired_user_ids,
    remove_mode_prefix,
    resolve_duration_minutes,
)

log = logging.getLogger(__name__)

COMMAND_NAME = "showmymode"
STATE_FILE = "modes.json"
DEFAULT_CHAR = "🙊"
DEFAULT_DURATION_MINUTES = 90

# This command's private persistent store: .../by-your-command/showmymode/modes.json
store = JSONStore(COMMAND_NAME)


def _empty_state() -> dict:
    return {"version": 1, "users": {}}


def _load_state() -> dict:
    return store.load(STATE_FILE, default=_empty_state())


def _save_state(state: dict) -> None:
    store.save(STATE_FILE, state)


def _edit_error_message(exc: Exception) -> str:
    """A friendly explanation for why editing a nickname might have failed."""
    return (
        "I couldn't change your nickname. I need the **Manage Nicknames** permission "
        "and a role above yours, and Discord never lets anyone edit the server "
        f"owner's nickname. (Details: {exc})"
    )


class ShowMyMode(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="showmymode",
        description="Toggle a 🙊 marker on your nickname to show you're in listen mode.",
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

        if onoff.value == 1:
            await self._turn_on(interaction, member, char, minutes)
        else:
            await self._turn_off(interaction, member)

    async def _turn_on(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        char: str,
        minutes: int | None,
    ) -> None:
        # Validate the optional duration before changing anything.
        try:
            duration = resolve_duration_minutes(minutes, DEFAULT_DURATION_MINUTES)
        except ValueError:
            await interaction.response.send_message(
                "Please give a positive number of minutes.", ephemeral=True
            )
            return

        new_nick = add_mode_prefix(member.display_name, char)
        try:
            await member.edit(nick=new_nick)
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                _edit_error_message(exc), ephemeral=True
            )
            return

        state = _load_state()
        state["users"][str(member.id)] = {
            "guild_id": member.guild.id,
            "char": char,
            "expires_at": int(time.time()) + duration * 60,
        }
        _save_state(state)
        await interaction.response.send_message(
            f"You're in listen mode for {duration} minutes. {char}", ephemeral=True
        )

    async def _turn_off(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        state = _load_state()
        record = state["users"].get(str(member.id))
        # Strip the character we recorded for this user, falling back to the default.
        char = record["char"] if record else DEFAULT_CHAR

        new_nick = remove_mode_prefix(member.display_name, char)
        try:
            await member.edit(nick=new_nick)
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                _edit_error_message(exc), ephemeral=True
            )
            return

        if record is not None:
            del state["users"][str(member.id)]
            _save_state(state)
        await interaction.response.send_message("Listen mode off.", ephemeral=True)


# --------------------------------------------------------------------------------
# Maintenance actions, registered with the bot's central sweep (bot.maintenance).
# --------------------------------------------------------------------------------


async def _scan_on_first_boot(bot) -> None:
    """One-time scan: if there's no state yet, adopt anyone already wearing 🙊.

    Without this, a marker added before the bot ever ran (or while a previous state
    file was lost) would have no expiry and linger forever. We can only detect the
    *default* marker here, since we have no record of past custom characters. The
    file is written even if nobody is found, so the scan never runs again.
    """
    if store.exists(STATE_FILE):
        return

    state = _empty_state()
    expires_at = int(time.time()) + DEFAULT_DURATION_MINUTES * 60
    for guild in bot.guilds:
        async for member in guild.fetch_members(limit=None):
            if member.display_name.startswith(DEFAULT_CHAR):
                state["users"][str(member.id)] = {
                    "guild_id": guild.id,
                    "char": DEFAULT_CHAR,
                    "expires_at": expires_at,
                }
    _save_state(state)
    log.info(
        "first-boot scan adopted %d member(s) wearing %s",
        len(state["users"]),
        DEFAULT_CHAR,
    )


async def _sweep_expired(bot) -> None:
    """Remove the marker from anyone whose timeout has elapsed."""
    state = _load_state()
    users = state["users"]
    now = int(time.time())

    expired = expired_user_ids(users, now)
    for user_id in expired:
        record = users[user_id]
        guild = bot.get_guild(record["guild_id"])
        if guild is not None:
            try:
                member = await guild.fetch_member(int(user_id))
                await member.edit(
                    nick=remove_mode_prefix(member.display_name, record["char"])
                )
            except discord.HTTPException as exc:
                # Member may have left, or our permissions changed — drop them anyway.
                log.info("could not clear marker for user %s: %s", user_id, exc)
        del users[user_id]

    if expired:
        _save_state(state)
        log.info("swept %d expired listen-mode marker(s)", len(expired))


async def setup(bot: commands.Bot) -> None:
    maintenance.register_startup(f"{COMMAND_NAME}-scan", _scan_on_first_boot)
    maintenance.register_periodic(f"{COMMAND_NAME}-sweep", _sweep_expired)
    await bot.add_cog(ShowMyMode(bot))
