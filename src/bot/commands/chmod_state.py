"""/chmod persistent state model: constants, loading, validation, normalization.

Separated from :mod:`bot.commands.chmod` so the state shape and its on-disk
representation stay decoupled from the Discord I/O and nickname logic. The core
slash command imports from here; the maintenance sweep imports from here too,
avoiding a circular dependency between chmod and chmod_maintenance.

State shape
-----------
The v2 nested layout keys by guild *and* user::

    {"version": 2, "guilds": {gid_str: {"users": {uid_str: record}}}}

Each record carries ``char`` (the marker), ``expires_at`` (absolute Unix timestamp),
and optionally ``original_nick`` (may be ``null``, meaning "no nickname"). The v1
flat layout (keyed by user alone) is migrated to v2 on first load.
"""

from __future__ import annotations

import logging
from typing import Any

from ..state import JSONStore

log = logging.getLogger(__name__)

# The state namespace is also the on-disk subdirectory name (.../by-your-command/chmod/
# modes.json). It is decoupled from the slash-command name so renaming the command
# never orphans persisted state.
STATE_NAMESPACE = "chmod"
STATE_FILE = "modes.json"
DEFAULT_CHAR = "🙊"
DEFAULT_DURATION_MINUTES = 90

# Module-level store. The cog and maintenance actions may override it by passing
# their own store to _load_state() / _save_state(); tests create an isolated store
# and pass it directly, removing the need to monkeypatch.
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


def _load_state(custom_store: JSONStore | None = None) -> dict[str, Any]:
    """Load and normalize the persisted state.

    If ``custom_store`` is provided it is used instead of the module-level ``store``.
    This lets tests supply an isolated store directly without monkeypatching,
    and lets the cog or sweep pass its own JSONStore instance.
    """
    s = custom_store if custom_store is not None else store
    return _normalize_state(s.load(STATE_FILE, default=_empty_state()))


def _save_state(state: dict[str, Any], custom_store: JSONStore | None = None) -> None:
    """Persist ``state`` atomically."""
    s = custom_store if custom_store is not None else store
    s.save(STATE_FILE, state)


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


async def setup(bot) -> None:
    """No-op: all functionality is imported by chmod.py and chmod_maintenance.py."""
