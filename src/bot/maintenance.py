"""A tiny registry that lets command modules opt into background work.

The bot core deliberately knows nothing about any specific command's maintenance
needs. Instead a command registers callbacks here (typically inside its ``setup``
function), and :mod:`bot.client`'s maintenance loop runs them:

* **startup** actions run exactly once, after the gateway is ready (so guild/member
  data is available) — driven by the maintenance loop's ``before_loop`` hook.
* **periodic** actions run on every tick of the maintenance loop (see
  :data:`TICK_INTERVAL_MINUTES`).

Each action is an ``async`` callable taking the bot instance. Exceptions are logged
and swallowed so that one misbehaving action can never stop the loop or crash the
bot, nor prevent the other registered actions from running.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

# How often bot.client's maintenance loop ticks. This is the granularity of every
# periodic action: an expiry can only be noticed at the next tick, so it also sets the
# floor on any user-facing timeout (see utils.resolve_duration_minutes' ``minimum``).
# One minute is the finest whole-minute cadence discord.ext.tasks offers; the sweep
# itself is cheap (a local state read), reaching the Discord API only when something
# has actually expired.
TICK_INTERVAL_MINUTES = 1


class _BotLike(Protocol):
    """The minimal bot interface that maintenance actions call through.

    At runtime the bot is always a ``commands.Bot`` subclass, but this protocol keeps
    ``maintenance.py`` free of any import dependency on ``bot.client`` and documents
    exactly what surface-area the registered actions are allowed to touch. The
    attributes are typed as ``Any`` deliberately — the real types live in
    ``discord.py`` and declaring them precisely here would create a hard dependency.
    """

    guilds: Any  # iterable of guild objects (each with .id, .fetch_members)

    def get_guild(self, guild_id: int, /) -> Any | None: ...


# A maintenance action receives the bot and returns an awaitable. The bot parameter
# is typed via _BotLike (a Protocol) instead of plain ``object`` so that type-checkers
# can verify the call sites pass something guild-aware, while still keeping
# maintenance.py free of any import of bot.client or discord.py.
Action = Callable[[_BotLike], Awaitable[None]]

_startup: list[tuple[str, Action]] = []
_periodic: list[tuple[str, Action]] = []


def register_startup(name: str, action: Action) -> None:
    """Register an action to run once, the first time the bot becomes ready."""
    _startup.append((name, action))


def register_periodic(name: str, action: Action) -> None:
    """Register an action to run on every maintenance tick."""
    _periodic.append((name, action))


async def _run(actions: list[tuple[str, Action]], bot: Any) -> None:
    for name, action in actions:
        try:
            await action(bot)
        except Exception:
            log.exception("maintenance action %r failed", name)


async def run_startup(bot: Any) -> None:
    """Run all registered startup actions (intended to run once)."""
    await _run(_startup, bot)


async def run_periodic(bot: Any) -> None:
    """Run all registered periodic actions (intended to run every tick)."""
    await _run(_periodic, bot)
