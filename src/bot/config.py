"""Configuration and secrets, loaded lazily from the environment.

All runtime configuration comes from environment variables. ``python-dotenv``
populates those from a local ``.env`` file (see ``.env.example``) when present;
real environment variables always take precedence. Centralizing this here means the
rest of the code never calls ``os.getenv`` directly, and the bot fails fast with a
clear message if a required value is missing or malformed.

Configuration is loaded on first access (not at import time) so that tests or
alternative entry points can manipulate the environment before calling the accessors,
and a configuration error surfaces as a clear message rather than an import crash.
"""

from __future__ import annotations

import os
from typing import cast

from dotenv import load_dotenv

_load_dotenv_called = False
_config_cache: dict[str, object] = {}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    """Return a required environment variable, or raise a helpful error."""
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name!r}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


def _optional_int(name: str) -> int | None:
    """Return an optional integer environment variable, or None if unset."""
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name!r} must be an integer, got {raw!r}."
        ) from exc


def _ensure_env_loaded() -> None:
    """Load .env once, on first access to any configuration value."""
    global _load_dotenv_called
    if not _load_dotenv_called:
        load_dotenv()
        _load_dotenv_called = True


def get_token() -> str:
    """Return the Discord bot token (required).

    Loads ``.env`` on first call and caches the result, so repeated calls are cheap.
    """
    _ensure_env_loaded()
    if "token" not in _config_cache:
        _config_cache["token"] = _require("DISCORD_BOT_TOKEN")
    return cast(str, _config_cache["token"])


def get_guild_id() -> int | None:
    """Return the optional single-server (guild) ID, or ``None``.

    When set, slash commands sync instantly to that one server; when unset they sync
    globally (which can take up to ~1 hour to appear).
    """
    _ensure_env_loaded()
    if "guild_id" not in _config_cache:
        _config_cache["guild_id"] = _optional_int("DISCORD_GUILD_ID")
    return cast("int | None", _config_cache["guild_id"])
