"""Small, mostly side-effect-free helpers shared across commands.

These functions hold the bits of logic worth testing in isolation — URL parsing,
message chunking, nickname-prefix toggling, expiry selection, duration validation —
kept separate from Discord I/O so the unit tests need no network or Discord objects.
The single exception is :func:`splitsend`, the thin async wrapper that actually
sends the chunks produced by :func:`chunk_message`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:  # imported only for type hints; avoids a runtime discord dependency
    import discord

# Discord rejects messages longer than 2000 characters. We chunk at a lower
# threshold to leave headroom (e.g. for code-fence wrapping) and avoid edge cases.
DISCORD_CHUNK_LIMIT = 1900

# The only hosts a genuine Discord message link can use. Parsing strictly against
# this set (rather than trusting the tail of an arbitrary URL) is what stops a
# crafted link like ``https://evil.example/a/1/2/3`` from being treated as valid.
DISCORD_MESSAGE_LINK_HOSTS = frozenset(
    {
        "discord.com",
        "www.discord.com",
        "ptb.discord.com",
        "canary.discord.com",
        "discordapp.com",
        "www.discordapp.com",
    }
)


def parse_message_link(url: str) -> tuple[int, int, int]:
    """Parse a Discord message link into ``(guild_id, channel_id, message_id)``.

    A guild message link looks like::

        https://discord.com/channels/<guild_id>/<channel_id>/<message_id>

    Parsing is strict: the URL must use http(s), a known Discord host, and exactly
    the path ``/channels/<guild>/<channel>/<message>`` with three integer IDs. Any
    query string or fragment is ignored, and a trailing slash is tolerated, but a
    wrong host, a missing ``/channels/`` prefix, extra path segments, or a non-integer
    ID (such as the ``@me`` guild of a DM link) raises ``ValueError``. This strictness
    matters for security: ``/tfurl`` relies on it to refuse links to channels the bot
    should not disclose.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"Not a Discord message link: {url!r}")
    host = (parts.hostname or "").lower()
    if host not in DISCORD_MESSAGE_LINK_HOSTS:
        raise ValueError(f"Not a Discord message link: {url!r}")

    # Drop empty segments so a single trailing slash is tolerated, then require the
    # exact ``channels/<guild>/<channel>/<message>`` shape — no more, no less.
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) != 4 or segments[0] != "channels":
        raise ValueError(f"Not a Discord message link: {url!r}")
    try:
        guild_id, channel_id, message_id = (int(segment) for segment in segments[1:])
    except ValueError as exc:
        raise ValueError(f"Not a Discord message link: {url!r}") from exc
    return guild_id, channel_id, message_id


def chunk_message(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters.

    Breaks on the last newline before the limit so chunks split on line boundaries
    where possible. A single line longer than ``limit`` (no newline to break on) is
    hard-split exactly at the limit — this avoids the unbounded recursion the legacy
    implementation hit on very long unbroken lines.
    """
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            # No newline within the limit: hard-split at the limit, keep every char.
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]
        else:
            # Break on the newline, and drop the newline itself.
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 1 :]
    chunks.append(remaining)
    return chunks


def add_mode_prefix(name: str, char: str) -> str:
    """Return ``name`` with ``char`` prepended, unless it already starts with it.

    Idempotent: repeated calls never stack the marker (fixing a legacy bug where
    repeatedly turning the mode "on" produced 🙊🙊🙊...).
    """
    if name.startswith(char):
        return name
    return char + name


def remove_mode_prefix(name: str, char: str) -> str:
    """Return ``name`` with a single leading ``char`` removed, if present."""
    if name.startswith(char):
        return name[len(char) :]
    return name


def expired_user_ids(users: dict, now: int) -> list[str]:
    """Return the user IDs whose mode expiry time is at or before ``now``.

    ``users`` maps user-id strings to records containing an integer ``expires_at``
    (a Unix timestamp). Records without a valid ``expires_at`` are treated as
    not-yet-expired and skipped.
    """
    return [
        user_id
        for user_id, record in users.items()
        if isinstance(record.get("expires_at"), int) and record["expires_at"] <= now
    ]


def resolve_duration_minutes(
    minutes: int | None, default: int, maximum: int | None = None
) -> int:
    """Validate an optional, user-supplied duration in minutes.

    Returns ``default`` when ``minutes`` is None and ``minutes`` itself when it is a
    positive integer within range. Raises ``ValueError`` — with a user-facing message —
    for zero or negative values, or for values above ``maximum`` when one is given
    (``maximum`` of None means "no upper bound", preserving the original behaviour).
    """
    if minutes is None:
        return default
    if minutes <= 0:
        raise ValueError("Please give a positive number of minutes.")
    if maximum is not None and minutes > maximum:
        raise ValueError(
            f"That's too long — please choose at most {maximum} minutes "
            f"({maximum // (24 * 60)} day(s))."
        )
    return minutes


async def splitsend(
    channel: discord.abc.Messageable,
    text: str,
    *,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> None:
    """Send ``text`` to a Discord ``channel``, split into message-sized chunks.

    This is the only I/O helper here; the chunking decision it relies on lives in
    the pure, unit-tested :func:`chunk_message`. ``allowed_mentions`` is forwarded to
    every chunk so a caller can suppress pings (e.g. ``AllowedMentions.none()`` when
    reposting untrusted content); ``None`` leaves Discord's default behaviour intact.
    """
    for chunk in chunk_message(text):
        if not chunk:
            continue  # never attempt to send an empty message (Discord rejects it)
        # Only forward allowed_mentions when the caller set it: discord.py treats an
        # explicit None differently from "argument omitted", so omitting it preserves
        # the library's default behaviour for callers that don't care about mentions.
        if allowed_mentions is None:
            await channel.send(chunk)
        else:
            await channel.send(chunk, allowed_mentions=allowed_mentions)
