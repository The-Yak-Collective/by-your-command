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

log = logging.getLogger(__name__)

# How often bot.client's maintenance loop ticks. This is the granularity of every
# periodic action: an expiry can only be noticed at the next tick, so it also sets the
# floor on any user-facing timeout (see utils.resolve_duration_minutes' ``minimum``).
# One minute is the finest whole-minute cadence discord.ext.tasks offers; the sweep
# itself is cheap (a local state read), reaching the Discord API only when something
# has actually expired.
TICK_INTERVAL_MINUTES = 1

# A maintenance action receives the bot/client and returns an awaitable. The bot is
# typed loosely as ``object`` to avoid importing (and depending on) bot.client here.
Action = Callable[[object], Awaitable[None]]

_startup: list[tuple[str, Action]] = []
_periodic: list[tuple[str, Action]] = []


def register_startup(name: str, action: Action) -> None:
    """Register an action to run once, the first time the bot becomes ready."""
    _startup.append((name, action))


def register_periodic(name: str, action: Action) -> None:
    """Register an action to run on every maintenance tick."""
    _periodic.append((name, action))


async def _run(actions: list[tuple[str, Action]], bot: object) -> None:
    for name, action in actions:
        try:
            await action(bot)
        except Exception:
            # One failing action must never take down the loop or the others.
            log.exception("maintenance action %r failed", name)


async def run_startup(bot: object) -> None:
    """Run all registered startup actions (intended to run once)."""
    await _run(_startup, bot)


async def run_periodic(bot: object) -> None:
    """Run all registered periodic actions (intended to run every tick)."""
    await _run(_periodic, bot)
