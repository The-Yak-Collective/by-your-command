"""/chmod — toggle a mode marker emoji on your nickname.

Turning the mode on prepends a marker (default 🙊) to your server nickname so others
can see your current mode; it auto-removes after a timeout (default 90 minutes). This
is a rewrite of the legacy slashayak ``showmymode`` command with the timeout
*actually implemented*, backed by persistent state so it survives the bot's nightly
restart.

This module is the shared core for the feature: it owns the persistent state model,
the on/off/swap logic, and the maintenance sweep, and it registers the primary
``/chmod`` slash command. The deprecated ``/showmymode`` command — kept only so
existing users get a nudge to switch — is a thin shim in :mod:`bot.commands.showmymode`
that maps its old on/off choice onto :func:`apply` here. When ``/showmymode`` is
eventually removed, deleting that one file leaves this core untouched.

State model
-----------
Per (guild, user) pair we persist the marker character they used, the absolute Unix
time at which it should be removed (``expires_at``), and the nickname they had
*before* the marker was applied (``original_nick``, which may be ``null`` to mean "no
nickname") so cleanup can restore their exact prior state. Keying by guild *and* user
is what makes one bot instance safe across multiple servers: a user's mode in one
guild is wholly independent of their mode in another, so turning it on/off in one
never touches (or leaks a nickname into) the other. On disk this nests as
``guilds -> {guild_id} -> users -> {user_id} -> record`` (the guild id is the key, so
no ``guild_id`` field is stored inside each record). Two maintenance actions,
registered with the bot's central sweep (:mod:`bot.maintenance`), act on this state:

* a one-time **startup scan** that, on the very first run (no state file yet), adopts
  everyone already wearing the default marker and gives them a fresh timeout; and
* a **periodic sweep** that removes the marker once a user's time has elapsed.

The state file is never deleted: its existence is how we know the startup scan has
already run and need not repeat after a restart. The on-disk shape is versioned; the
flat ``version: 1`` layout (records keyed by user id alone, with ``guild_id`` stored
inside each record) is migrated to the nested ``version: 2`` layout on first load, so
an existing single-guild deployment upgrades transparently.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import discord
from discord import app_commands
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

# The state namespace is also the on-disk subdirectory name (.../by-your-command/chmod/
# modes.json). It is decoupled from the slash-command name so renaming the command
# never orphans persisted state; it just happens to match here.
STATE_NAMESPACE = "chmod"
STATE_FILE = "modes.json"
DEFAULT_CHAR = "🙊"
DEFAULT_DURATION_MINUTES = 90
# Upper bound on the auto-remove timeout. Without a cap, a very large value would
# leave a marker effectively forever and keep stale state hanging around indefinitely.
MAX_DURATION_MINUTES = 7 * 24 * 60  # one week

# This command's private persistent store: .../by-your-command/chmod/modes.json
store = JSONStore(STATE_NAMESPACE)


def _empty_state() -> dict[str, Any]:
    return {"version": 2, "guilds": {}}


def _clean_record(record: object) -> dict[str, Any] | None:
    """Validate one user record, returning the clean (guild-id-free) dict or None.

    A well-formed record has a non-empty ``char`` string and an integer
    ``expires_at``; the optional ``original_nick`` may be ``null`` (meaning "they
    had no nickname") or a string. ``guild_id`` is deliberately not carried: in the
    v2 nested layout the guild id is the outer key, so storing it inside the record
    too would be redundant. Malformed records are dropped (logged by the caller)
    rather than allowed to raise deep in the on/off handlers or the sweep.
    """
    if not isinstance(record, dict):
        return None
    if not (
        isinstance(record.get("char"), str)
        and record["char"]
        and isinstance(record.get("expires_at"), int)
    ):
        return None
    clean: dict[str, Any] = {
        "char": record["char"],
        "expires_at": record["expires_at"],
    }
    if "original_nick" in record:
        nick = record["original_nick"]
        if nick is None or isinstance(nick, str):
            clean["original_nick"] = nick
    return clean


def _normalize_state(raw: object) -> dict[str, Any]:
    """Return a structurally valid v2 state dict, dropping or migrating bad data.

    Persisted state can be hand-edited or partially written, so we never trust its
    shape. Two source layouts are accepted:

    * **v2** (nested): ``{"version": 2, "guilds": {gid: {"users": {uid: record}}}}``.
      Each record is validated by :func:`_clean_record`; the guild id is the key, so
      any ``guild_id`` field inside a record is ignored and dropped.
    * **v1** (flat, migrated): ``{"version": 1, "users": {uid: {guild_id, ...}}}`` —
      or any state lacking a ``guilds`` dict. Each record is validated and then
      relocated under ``guilds[str(guild_id)]`` using the ``guild_id`` it carries.
      This is lossless for live data (every v1 record stores its guild id) and lets
      an existing single-guild deployment upgrade transparently on first load.

    Anything that isn't a well-formed record — or a v1 record without a valid
    ``guild_id`` to place it under — is discarded with a log entry rather than being
    allowed to raise ``KeyError``/``TypeError`` deep inside the on/off handlers or
    the maintenance sweep. The optional ``original_nick`` may legitimately be
    ``null`` (the user had no nickname) and is preserved as-is.
    """
    state = _empty_state()
    if not isinstance(raw, dict):
        log.warning("mode state is not an object; using empty state")
        return state

    # v2 nested layout: validate in place.
    guilds = raw.get("guilds")
    if isinstance(guilds, dict):
        for guild_id_str, guild_state in guilds.items():
            if not isinstance(guild_state, dict):
                continue
            users = guild_state.get("users")
            if not isinstance(users, dict):
                continue
            for user_id, record in users.items():
                clean = _clean_record(record)
                if clean is None:
                    log.warning(
                        "dropping malformed mode record for guild %r user %r",
                        guild_id_str,
                        user_id,
                    )
                    continue
                state["guilds"].setdefault(str(guild_id_str), {"users": {}})["users"][
                    str(user_id)
                ] = clean
        return state

    # v1 flat layout (or version-less): migrate to nested using each record's
    # stored guild_id. Without a guild_id a record can't be placed under a guild
    # key, so it is dropped — the v2 shape keys by guild.
    users = raw.get("users")
    if isinstance(users, dict):
        for user_id, record in users.items():
            clean = _clean_record(record)
            if clean is None:
                log.warning("dropping malformed mode record for %r", user_id)
                continue
            guild_id = record.get("guild_id") if isinstance(record, dict) else None
            if not isinstance(guild_id, int):
                log.warning(
                    "dropping v1 record %r with no valid guild_id to place it under",
                    user_id,
                )
                continue
            state["guilds"].setdefault(str(guild_id), {"users": {}})["users"][
                str(user_id)
            ] = clean
    return state


def _load_state() -> dict[str, Any]:
    return _normalize_state(store.load(STATE_FILE, default=_empty_state()))


def _save_state(state: dict[str, Any]) -> None:
    store.save(STATE_FILE, state)


def _users_in_guild(state: dict[str, Any], guild_id: int) -> dict[str, Any]:
    """Return the per-guild ``users`` dict, or an empty dict if this guild has none.

    Read-only in intent: returns the live dict (so mutations are visible in
    ``state``), but for writes prefer :func:`_ensure_guild_users` which creates the
    guild entry when absent. The result is always a ``dict`` (never ``None``) so
    callers can ``in``/``.get`` it without guarding for a missing guild entry.
    """
    guild_state = state["guilds"].get(str(guild_id))
    if not isinstance(guild_state, dict):
        return {}
    users = guild_state.get("users")
    return users if isinstance(users, dict) else {}


def _ensure_guild_users(state: dict[str, Any], guild_id: int) -> dict[str, Any]:
    """Return the per-guild ``users`` dict, creating the guild entry if absent.

    For a guild we are about to write a record into; the entry it creates is what
    :func:`_save_state` then persists. Mutating the returned dict mutates ``state``.
    """
    return state["guilds"].setdefault(str(guild_id), {"users": {}})["users"]


def _drop_user(state: dict[str, Any], guild_id: int, user_id: str) -> None:
    """Remove a user's record, and prune the guild entry if it is now empty.

    Pruning keeps the state file from accumulating empty ``guilds`` entries (one
    per server anyone ever toggled in) over the bot's lifetime.
    """
    users = _users_in_guild(state, guild_id)
    users.pop(user_id, None)
    if not users:
        state["guilds"].pop(str(guild_id), None)


def _edit_error_message(exc: Exception) -> str:
    """A friendly explanation for why editing a nickname might have failed."""
    return (
        "I couldn't change your nickname. I need the **Manage Nicknames** permission "
        "and a role above yours, and Discord never lets anyone edit the server "
        f"owner's nickname. (Details: {exc})"
    )


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
    return remove_mode_prefix(member.display_name, char)


async def _turn_on(
    interaction: discord.Interaction,
    member: discord.Member,
    char: str,
    minutes: int | None,
) -> None:
    # Validate the optional duration before changing anything. The helper raises
    # with a user-facing message for an over-the-cap value or one shorter than the
    # maintenance tick — the marker can't be removed before the next sweep, so we
    # refuse a timeout finer than that sweep interval.
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

    # Capture the nickname to restore when the mode is turned off later. We read
    # ``member.nick`` (the real server nickname, ``None`` if unset) rather than the
    # display name, so cleanup can restore the exact prior state — including "no
    # nickname at all". If the mode is already on *in this guild*, keep the value we
    # first stored instead of re-capturing the already-marked nick. (Scoping the
    # lookup to this guild is the multi-guild fix: a marker active in another guild
    # must not leak its original_nick here.)
    state = _load_state()
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
    _save_state(state)
    await interaction.response.send_message(
        f"You're in {char} mode for the next {duration} minutes.", ephemeral=True
    )


async def _turn_off(interaction: discord.Interaction, member: discord.Member) -> None:
    state = _load_state()
    users = _users_in_guild(state, member.guild.id)
    record = users.get(str(member.id))

    if record is None:
        # No active marker in *this* guild. In multi-guild this is now reachable:
        # the user may have the mode on in another guild, but swapping or forcing
        # off here must not touch a nickname we never marked (which would either
        # clobber it or restore another guild's stored nick). Tell them and stop.
        await interaction.response.send_message(
            "You don't have a mode marker on right now.", ephemeral=True
        )
        return

    # Strip the character we recorded for this user.
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
    _save_state(state)
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
    state = _load_state()
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
            # Nicknames only exist within a server, not in DMs.
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        # Use only the first character, so a pasted multi-character string still
        # yields a single marker.
        char = (mode or DEFAULT_CHAR)[0]
        await apply(interaction, member, enable, char, minutes)


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
    adopted = 0
    for guild in bot.guilds:
        # Create this guild's entry lazily — only once we find a matching member —
        # so guilds where nobody wears the marker don't accumulate empty entries.
        # A user wearing the default in two guilds is recorded in both, independently
        # (the multi-guild fix).
        guild_users: dict[str, Any] | None = None
        async for member in guild.fetch_members(limit=None):
            if member.display_name.startswith(DEFAULT_CHAR):
                # The marker predates our tracking, so we don't truly know their
                # pre-marker nickname; best effort is their current nick with the
                # marker stripped (None if they had no server nickname at all).
                original_nick = (
                    remove_mode_prefix(member.nick, DEFAULT_CHAR)
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
        expired = expired_user_ids(users, now)
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
                    # Member may have left, or our permissions changed — drop them
                    # anyway rather than retrying a failing clear forever.
                    log.info("could not clear marker for user %s: %s", user_id, exc)
            del users[user_id]
            swept += 1
        if not users:
            empty_guilds.append(guild_id_str)

    # Prune guild entries that now have no users so the state file doesn't
    # accumulate one entry per server anyone ever toggled in.
    for guild_id_str in empty_guilds:
        state["guilds"].pop(guild_id_str, None)

    if swept:
        _save_state(state)
        log.info("swept %d expired mode marker(s)", swept)


async def setup(bot: commands.Bot) -> None:
    maintenance.register_startup(f"{STATE_NAMESPACE}-scan", _scan_on_first_boot)
    maintenance.register_periodic(f"{STATE_NAMESPACE}-sweep", _sweep_expired)
    await bot.add_cog(Chmod(bot))
