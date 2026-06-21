"""by-your-command — a small, modular Discord bot.

Architecture at a glance:

* ``bot.config``      — loads and validates configuration/secrets from the environment.
* ``bot.client``      — builds the Discord client, auto-loads command modules, syncs
                        the slash-command tree, and runs the periodic maintenance loop.
* ``bot.commands.*``  — one module per slash command (auto-discovered at startup).
* ``bot.utils``       — small, side-effect-free helpers (unit-tested in isolation).
* ``bot.state``       — per-command persistent JSON state under the XDG state dir.
* ``bot.maintenance`` — a registry that lets commands opt into startup/periodic work.

To add a new slash command, copy a module in ``bot.commands`` and edit it; nothing
else needs to change.
"""

__version__ = "0.1.0"
