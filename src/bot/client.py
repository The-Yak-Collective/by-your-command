"""The Discord client: builds the bot, auto-loads command modules, syncs the
slash-command tree, and drives the periodic maintenance loop.

We subclass ``commands.Bot`` (rather than the bare ``discord.Client``) to get the
cog/extension system — that is what makes the "one file per command" auto-discovery
in :meth:`ByYourCommandBot._load_command_modules` work cleanly.
"""

from __future__ import annotations

import logging
import pkgutil

import discord
from discord.ext import commands, tasks

from . import commands as command_package  # the bot.commands sub-package
from . import config, maintenance

log = logging.getLogger(__name__)


def _build_intents() -> discord.Intents:
    """Return the gateway intents the bot's commands require.

    Both ``members`` and ``message_content`` are *privileged* intents and must be
    enabled in the Discord Developer Portal (Bot -> Privileged Gateway Intents),
    or the bot will fail to log in.
    """
    intents = discord.Intents.default()
    # /showmymode: read & edit member nicknames, and fetch the member list for the
    # first-boot scan.
    intents.members = True
    # /tfurl: read the text content of the message being unfurled.
    intents.message_content = True
    return intents


class ByYourCommandBot(commands.Bot):
    """The bot instance. Commands are discovered and loaded in ``setup_hook``."""

    def __init__(self) -> None:
        # We only use slash commands, so the prefix is effectively unused; requiring
        # an @mention means the bot ignores ordinary chat messages.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=_build_intents(),
        )

    async def setup_hook(self) -> None:
        """Async startup hook: load commands, sync the tree, start maintenance."""
        await self._load_command_modules()
        await self._sync_commands()
        # The loop's before_loop waits until the bot is ready, so starting it here
        # (before the gateway connects) is safe.
        self.maintenance_loop.start()

    async def _load_command_modules(self) -> None:
        """Load every module in ``bot.commands`` as an extension.

        Any module there exposing ``async def setup(bot)`` is loaded. This is the
        mechanism behind "drop a file in commands/ to add a command" — there is no
        central registration list to keep in sync.
        """
        for module in pkgutil.iter_modules(command_package.__path__):
            qualified_name = f"{command_package.__name__}.{module.name}"
            await self.load_extension(qualified_name)
            log.info("loaded command module %s", qualified_name)

    async def _sync_commands(self) -> None:
        """Publish slash-command definitions to Discord.

        With a configured guild we sync to just that server (instant); otherwise we
        sync globally (which can take up to ~1 hour to propagate).
        """
        if config.GUILD_ID is not None:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("synced %d command(s) to guild %s", len(synced), config.GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("synced %d command(s) globally (may take up to ~1h)", len(synced))

    # The tick cadence lives in bot.maintenance (which owns the periodic-action
    # contract) so a command can derive its minimum timeout from the same source.
    @tasks.loop(minutes=maintenance.TICK_INTERVAL_MINUTES)
    async def maintenance_loop(self) -> None:
        """Run every registered periodic maintenance action, once per tick."""
        await maintenance.run_periodic(self)

    @maintenance_loop.before_loop
    async def _before_maintenance(self) -> None:
        """Run one-shot startup work after the gateway is ready.

        ``before_loop`` fires exactly once, before the first iteration, so it is the
        natural home for one-time startup actions (such as the /showmymode
        first-boot scan) and is not re-triggered by gateway reconnects.
        """
        await self.wait_until_ready()
        await maintenance.run_startup(self)

    async def on_ready(self) -> None:
        log.info("logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))
