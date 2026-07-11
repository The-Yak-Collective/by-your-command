"""/chmod maintenance sweep: first-boot adoption scan and periodic expiry cleanup.

These actions are registered with the bot's central sweep (:mod:`bot.maintenance`)
from :func:`bot.commands.chmod.setup`. They live in their own module so the
dependency chain stays acyclic: the sweep imports only from chmod_state and utils,
never from chmod itself.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import discord

from .. import utils
from .chmod_state import (
    DEFAULT_CHAR,
    DEFAULT_DURATION_MINUTES,
    STATE_FILE,
    _empty_state,
    _ensure_guild_users,
    _load_state,
    _save_state,
    store,
)

log = logging.getLogger(__name__)


def _nick_to_restore(
    record: dict[str, Any] | None, member: discord.Member, char: str
) -> str | None:
    """Decide what nickname to restore when removing a marker.

    Prefer the original nickname captured when the mode was turned on; this may be
    ``None``, which deliberately means "they had no nickname, so clear it" rather than
    leaving an explicit nickname behind. For records predating that field, or members
    we never tracked, fall back to simply stripping the marker from the display name.
    """
    if record is not None and "original_nick" in record:
        return record["original_nick"]
    return utils.remove_mode_prefix(member.display_name, char)


async def _scan_on_first_boot(bot) -> None:
    """One-time scan: if there's no state yet, adopt anyone already wearing the
    default marker character (:data:`DEFAULT_CHAR`).

    Without this, a marker added before the bot ever ran (or while a previous state
    file was lost) would have no expiry and linger forever. We can only detect the
    *default* marker here, since we have no record of past custom characters. The
    file is written even if nobody is found, so the scan never runs again.

    NOTE: On very large servers ``fetch_members(limit=None)`` loads every member
    into the gateway cache and can make many paginated API requests. This is a
    one-time cost at first startup. If the bot serves a guild above ~10 000 members
    you may want to initialise the state file by hand instead.
    """
    if store.exists(STATE_FILE):
        return

    state = _empty_state()
    expires_at = int(time.time()) + DEFAULT_DURATION_MINUTES * 60
    adopted = 0
    for guild in bot.guilds:
        guild_users: dict[str, Any] | None = None
        async for member in guild.fetch_members(limit=None):
            if member.display_name.startswith(DEFAULT_CHAR):
                original_nick = (
                    utils.remove_mode_prefix(member.nick, DEFAULT_CHAR)
                    if member.nick
                    else None
                )
                if guild_users is None:
                    guild_users = _ensure_guild_users(state, guild.id)
                guild_users[str(member.id)] = {
                    "char": DEFAULT_CHAR,
                    "expires_at": expires_at,
                    "original_nick": original_nick,
                }
                adopted += 1
    _save_state(state)
    log.info("first-boot scan adopted %d member(s) wearing %s", adopted, DEFAULT_CHAR)


async def _sweep_expired(bot) -> None:
    """Remove the marker from anyone whose timeout has elapsed.

    Walked per guild so markers in different servers are cleared independently: a
    user whose mode expired in guild A is restored there while their (still-active)
    marker in guild B is untouched. The pure :func:`expired_user_ids` helper is
    called once per guild on its inner ``users`` dict and is unchanged by this
    layout change — it only ever knew about a ``{user_id: record}`` mapping.
    """
    state = _load_state()
    now = int(time.time())
    swept = 0
    empty_guilds: list[str] = []

    for guild_id_str, guild_state in state["guilds"].items():
        users = guild_state.get("users") if isinstance(guild_state, dict) else None
        if not isinstance(users, dict):
            continue
        expired = utils.expired_user_ids(users, now)
        for user_id in expired:
            record = users[user_id]
            guild = bot.get_guild(int(guild_id_str))
            if guild is not None:
                try:
                    member = await guild.fetch_member(int(user_id))
                    await member.edit(
                        nick=_nick_to_restore(record, member, record["char"])
                    )
                except discord.HTTPException as exc:
                    log.info("could not clear marker for user %s: %s", user_id, exc)
            del users[user_id]
            swept += 1
        if not users:
            empty_guilds.append(guild_id_str)

    for guild_id_str in empty_guilds:
        state["guilds"].pop(guild_id_str, None)

    if swept:
        _save_state(state)
        log.info("swept %d expired mode marker(s)", swept)
