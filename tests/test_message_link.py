"""Tests for parse_message_link (Discord message URL -> integer IDs)."""

import pytest

from bot.utils import parse_message_link


def test_parses_standard_link():
    url = "https://discord.com/channels/111/222/333"
    assert parse_message_link(url) == (111, 222, 333)


def test_tolerates_trailing_slash():
    url = "https://discord.com/channels/111/222/333/"
    assert parse_message_link(url) == (111, 222, 333)


def test_rejects_dm_link():
    # DM links use the literal "@me" as the guild segment, which is not an int.
    with pytest.raises(ValueError):
        parse_message_link("https://discord.com/channels/@me/222/333")


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_message_link("not a link")
