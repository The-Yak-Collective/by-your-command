"""Shared test fixtures and fakes for the by-your-command test suite.

Fixtures here are available to all test files without explicit imports (pytest
auto-discovers conftest.py). The fakes model Discord objects with the minimal
surface-area each command actually touches, keeping tests fast and offline.
"""

from __future__ import annotations

import types
from typing import Any

import discord
import pytest

from bot import state


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point /mode's module-level store at a throwaway directory.

    Creates a fresh JSONStore under a temp XDG_STATE_HOME and patches
    ``mode_state.store`` so all state operations (including the maintenance
    sweep) land in the isolated directory.
    """
    from bot.commands import mode_state

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(
        mode_state, "store", state.JSONStore(mode_state.STATE_NAMESPACE)
    )


def _http_error(status=413):
    """Build a discord.HTTPException like the API raises when an upload is too big."""
    response = types.SimpleNamespace(status=status, reason="Payload Too Large")
    return discord.HTTPException(response, "request entity too large")


class FakeMember:
    """A member whose nickname can be edited; ``display_name`` mirrors Discord's rule.

    ``guild_id`` defaults to a test constant so single-guild tests work unchanged;
    multi-guild tests pass a different id to model the same user in another server
    (a member is per-guild in Discord, so each gets its own FakeMember).
    """

    def __init__(self, member_id, *, nick=None, username="User", guild_id=None):
        if guild_id is None:
            guild_id = 7  # default for backward-compatible single-guild tests
        self.id = member_id
        self.guild = types.SimpleNamespace(id=guild_id)
        self.nick = nick
        self._username = username
        self.edits: list[Any] = []

    @property
    def display_name(self):
        return self.nick if self.nick is not None else self._username

    async def edit(self, nick=None):
        self.edits.append(nick)
        self.nick = nick


class FakeResponse:
    """Collects ``send_message`` calls so tests can inspect what was sent."""

    def __init__(self):
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


def make_interaction():
    """Return a minimal ``discord.Interaction`` stand-in for command tests.

    ``_turn_on`` / ``_turn_off`` / ``apply`` only touch ``interaction.response``
    (they send the result); the invoking member is passed separately, so this
    interaction carries no user.
    """
    return types.SimpleNamespace(response=FakeResponse())
