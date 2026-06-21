"""Small, mostly side-effect-free helpers shared across commands.

These functions hold the bits of logic worth testing in isolation — URL parsing,
message chunking, nickname-prefix toggling, expiry selection, duration validation —
kept separate from Discord I/O so the unit tests need no network or Discord objects.
The single exception is :func:`splitsend`, the thin async wrapper that actually
sends the chunks produced by :func:`chunk_message`.
"""

from __future__ import annotations

# Discord rejects messages longer than 2000 characters. We chunk at a lower
# threshold to leave headroom (e.g. for code-fence wrapping) and avoid edge cases.
DISCORD_CHUNK_LIMIT = 1900


def parse_message_link(url: str) -> tuple[int, int, int]:
    """Parse a Discord message link into ``(guild_id, channel_id, message_id)``.

    A guild message link looks like::

        https://discord.com/channels/<guild_id>/<channel_id>/<message_id>

    We take the final three path segments and interpret them as integer IDs, which
    tolerates trailing slashes and either the discord.com or discordapp.com host.
    Raises ``ValueError`` if those segments are not all integers (for example a DM
    link, whose guild segment is the literal ``@me``, which is unsupported).
    """
    segments = [part for part in url.strip().split("/") if part]
    if len(segments) < 3:
        raise ValueError(f"Not a Discord message link: {url!r}")
    try:
        guild_id, channel_id, message_id = (int(segment) for segment in segments[-3:])
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


def resolve_duration_minutes(minutes: int | None, default: int) -> int:
    """Validate an optional, user-supplied duration in minutes.

    Returns ``default`` when ``minutes`` is None, returns ``minutes`` when it is a
    positive integer, and raises ``ValueError`` for zero or negative values.
    """
    if minutes is None:
        return default
    if minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    return minutes


async def splitsend(channel, text: str) -> None:
    """Send ``text`` to a Discord ``channel``, split into message-sized chunks.

    This is the only I/O helper here; the chunking decision it relies on lives in
    the pure, unit-tested :func:`chunk_message`.
    """
    for chunk in chunk_message(text):
        if chunk:  # never attempt to send an empty message (Discord rejects it)
            await channel.send(chunk)
