"""Configuration and secrets, loaded once from the environment.

All runtime configuration comes from environment variables. ``python-dotenv``
populates those from a local ``.env`` file (see ``.env.example``) when present;
real environment variables always take precedence. Centralizing this here means the
rest of the code never calls ``os.getenv`` directly, and the bot fails fast with a
clear message if a required value is missing or malformed.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from the current working directory / nearest parent. The operational
# scripts always `cd` to the repo root first, so this finds the project's .env.
load_dotenv()


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


# The Discord bot token. Required — the bot cannot connect to Discord without it.
TOKEN: str = _require("DISCORD_BOT_TOKEN")

# Optional single-server (guild) ID. When set, slash commands sync instantly to that
# one server; when unset, they sync globally (which can take up to ~1 hour to appear).
GUILD_ID: int | None = _optional_int("DISCORD_GUILD_ID")
