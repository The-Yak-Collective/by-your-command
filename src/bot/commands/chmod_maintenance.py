"""/chmod maintenance sweep: first-boot adoption scan and periodic expiry cleanup.

These actions are registered with the bot's central sweep (:mod:`bot.maintenance`)
from :func:`bot.commands.chmod.setup`. They live in their own module so the
dependency chain stays acyclic: the sweep imports only from chmod_state and utils,
never from chmod itself.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import discord

from .. import utils
from .chmod_state import (
    DEFAULT_CHAR,
    DEFAULT_DURATION_MINUTES,
    STATE_FILE,
    _ensure_guild_users,
    _load_state,
    _save_state,
    store,
)

log = logging.getLogger(__name__)

# How many members to fetch per guild per maintenance tick during the first-boot
# scan. The Discord REST API returns up to 1000 members per request, and the
# one-minute tick cadence provides a natural cool-down between batches. 1000 per
# guild per minute keeps the scan fast on small servers while staying well under
# rate limits on large ones. A guild with 50 000 members scans in ~50 ticks (~50
# minutes), and the progress file makes that fully resumable across restarts.
SCAN_BATCH_SIZE = 1000

# Progress file written alongside the main state file while the scan is in flight.
# Its presence means the scan was interrupted and should resume; its absence in the
# presence of a state file means the scan completed. Stored under the same
# directory as modes.json so atomic-write semantics and quarantining apply.
SCAN_PROGRESS_FILE = "scan_progress.json"


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


def _scan_complete() -> bool:
    """True when the first-boot scan has finished and there is no progress to resume."""
    return store.exists(STATE_FILE) and not store.exists(SCAN_PROGRESS_FILE)


def _load_scan_progress() -> dict[str, Any]:
    """Load the first-boot scan progress cursor, or return an empty dict."""
    if not store.exists(SCAN_PROGRESS_FILE):
        return {}
    progress = store.load(SCAN_PROGRESS_FILE, default={})
    return progress if isinstance(progress, dict) else {}


def _save_scan_progress(progress: dict[str, Any]) -> None:
    """Persist scan progress so it can resume after a restart or crash."""
    store.save(SCAN_PROGRESS_FILE, progress)


def _clear_scan_progress() -> None:
    """Remove the scan progress file once the scan is fully complete."""
    try:
        os.remove(store._path(SCAN_PROGRESS_FILE))
    except OSError:
        pass


async def _scan_on_first_boot(bot) -> None:
    """Resumable first-boot scan: adopt members already wearing the default marker.

    Members are fetched in batches of :data:`SCAN_BATCH_SIZE` per guild per
    maintenance tick, using a progress file to track the cursor (last-seen user ID)
    so a crash or restart mid-scan can resume where it left off rather than
    starting over. Each batch's adopted members are saved immediately, so no work
    is lost on interruption, and the natural one-minute cadence of the maintenance
    loop provides cool-down between API calls. When all guilds have been fully
    scanned the progress file is deleted, and the function becomes a no-op.

    Without this scan, a marker added before the bot ever ran (or while a previous
    state file was corrupted and quarantined) would have no expiry and linger
    forever. We can only detect the *default* marker here, since we have no record
    of past custom characters.
    """
    if _scan_complete():
        return

    # If the state file was lost (corrupted + quarantined) but progress still
    # exists, reset and start fresh — the old cursor is meaningless without the
    # matching state it was built against.
    if not store.exists(STATE_FILE):
        _clear_scan_progress()

    progress = _load_scan_progress()
    state = _load_state()
    now = int(time.time())
    expires_at = now + DEFAULT_DURATION_MINUTES * 60

    # Prune guilds the bot has left since the last tick so they can't block the
    # "all done" check forever.
    active_guild_ids = {str(g.id) for g in bot.guilds}
    for stale in list(progress.keys()):
        if stale not in active_guild_ids:
            del progress[stale]

    for guild in bot.guilds:
        guild_id_str = str(guild.id)
        gp = progress.setdefault(
            guild_id_str, {"after": None, "done": False, "adopted": 0}
        )

        if gp.get("done"):
            continue

        after_id = int(gp["after"]) if gp.get("after") else None
        batch: list[discord.Member] = []

        try:
            async for member in guild.fetch_members(
                limit=SCAN_BATCH_SIZE, after=after_id
            ):
                batch.append(member)
        except discord.HTTPException as exc:
            log.warning("scan batch failed for guild %s: %s", guild_id_str, exc)
            continue

        guild_users = _ensure_guild_users(state, guild.id)
        for member in batch:
            if member.display_name.startswith(DEFAULT_CHAR):
                uid = str(member.id)
                if uid not in guild_users:
                    original_nick = (
                        utils.remove_mode_prefix(member.nick, DEFAULT_CHAR)
                        if member.nick
                        else None
                    )
                    guild_users[uid] = {
                        "char": DEFAULT_CHAR,
                        "expires_at": expires_at,
                        "original_nick": original_nick,
                    }
                    gp["adopted"] += 1

        _save_state(state)

        if len(batch) < SCAN_BATCH_SIZE:
            gp["done"] = True
            log.info(
                "scan complete for guild %s: adopted %d",
                guild_id_str,
                gp["adopted"],
            )
        elif batch:
            gp["after"] = str(batch[-1].id)

    if all(p.get("done") for p in progress.values()):
        _clear_scan_progress()
        log.info("first-boot scan fully complete")
    else:
        _save_scan_progress(progress)


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
