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


def test_accepts_discordapp_host():
    # The historical discordapp.com host is still a real Discord link.
    url = "https://discordapp.com/channels/111/222/333"
    assert parse_message_link(url) == (111, 222, 333)


def test_accepts_ptb_and_canary_subdomains():
    assert parse_message_link("https://ptb.discord.com/channels/1/2/3") == (1, 2, 3)
    assert parse_message_link("https://canary.discord.com/channels/1/2/3") == (1, 2, 3)


def test_ignores_query_string_and_fragment():
    # Trailing tracking params or a fragment must not change the parsed IDs.
    assert parse_message_link("https://discord.com/channels/1/2/3?foo=bar") == (1, 2, 3)
    assert parse_message_link("https://discord.com/channels/1/2/3#frag") == (1, 2, 3)


def test_rejects_wrong_host():
    # The crux of the security fix: a non-Discord host that merely *ends* in the
    # right-looking path must not be treated as a valid message link.
    with pytest.raises(ValueError):
        parse_message_link("https://evil.example/channels/111/222/333")
    with pytest.raises(ValueError):
        parse_message_link("https://evil.example/a/111/222/333")


def test_rejects_missing_channels_prefix():
    # Right host, right number of segments, but not under /channels/.
    with pytest.raises(ValueError):
        parse_message_link("https://discord.com/guilds/111/222/333")


def test_rejects_extra_path_segments():
    with pytest.raises(ValueError):
        parse_message_link("https://discord.com/channels/111/222/333/444")


def test_rejects_too_few_path_segments():
    with pytest.raises(ValueError):
        parse_message_link("https://discord.com/channels/111/222")


def test_rejects_non_https_scheme():
    with pytest.raises(ValueError):
        parse_message_link("ftp://discord.com/channels/111/222/333")


def test_rejects_malformed_numeric_ids():
    with pytest.raises(ValueError):
        parse_message_link("https://discord.com/channels/111/2x2/333")
