"""Slash command modules.

Every module in this package that defines an ``async def setup(bot)`` function is
auto-discovered and loaded at startup by :mod:`bot.client` (via
``pkgutil.iter_modules``). To add a new command, copy an existing module
(e.g. ``tfurl.py``), rename the cog class and the command, and edit the body —
no central registration list needs to be touched.
"""
