"""Per-command persistent state, stored as JSON under the XDG state directory.

Each command gets its own subdirectory so different commands' state files never
collide, and the filesystem layout is hidden behind :class:`JSONStore` so command
modules never hardcode paths. Writes are atomic (write a temp file, then
``os.replace``) so a crash or restart mid-write cannot corrupt an existing file.

The state root is::

    ${XDG_STATE_HOME:-$HOME/.local/state}/by-your-command/

and also holds the bot's PID file and logs (written by ``scripts/bot.sh``).

NOTE: ``scripts/bot.sh`` recomputes this same path in shell. If you change the
layout here, change it there too.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def base_dir() -> Path:
    """Return the bot's state root, honoring ``XDG_STATE_HOME`` (default ~/.local/state)."""
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "by-your-command"


def _require_simple_name(name: str) -> str:
    """Reject anything that isn't a single, safe path component.

    Command names and filenames must be plain relative names — no path separators,
    no ``..``, no leading ``.`` traversal, and nothing absolute. Validating here means
    even a future command that passes an untrusted name can never make ``JSONStore``
    read or write outside its own state directory.
    """
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or os.sep in name
        or (os.altsep is not None and os.altsep in name)
    ):
        raise ValueError(f"unsafe state path component: {name!r}")
    return name


class JSONStore:
    """Reads and writes JSON files within one command's private state directory."""

    def __init__(self, command_name: str) -> None:
        # e.g. ${XDG_STATE_HOME}/by-your-command/showmymode/
        self.dir: Path = base_dir() / _require_simple_name(command_name)

    def _path(self, filename: str) -> Path:
        # Validate every filename too, so a relative name can never escape self.dir.
        return self.dir / _require_simple_name(filename)

    def exists(self, filename: str) -> bool:
        """True if the named state file has been written at least once."""
        return self._path(filename).is_file()

    def load(self, filename: str, default: Any = None) -> Any:
        """Return the parsed JSON contents, or ``default`` if the file is absent.

        If the file exists but is not valid JSON (a partial manual edit, a disk
        problem, a truncated write), we do not let the error propagate and break the
        calling command on every run. Instead the unreadable file is renamed aside to
        ``<filename>.corrupt`` (so the bad data is preserved for inspection and a fresh
        file can be written next save) and ``default`` is returned.
        """
        path = self._path(filename)
        if not path.is_file():
            return default
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            quarantine = path.with_name(path.name + ".corrupt")
            log.error(
                "state file %s is unreadable (%s); quarantining to %s and using default",
                path,
                exc,
                quarantine,
            )
            try:
                os.replace(path, quarantine)
            except OSError:
                log.exception("could not quarantine corrupt state file %s", path)
            return default

    def save(self, filename: str, data: Any) -> None:
        """Atomically write ``data`` as JSON to the named state file."""
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self._path(filename)
        # Write to a sibling temp file first, then atomically replace the target, so
        # a reader (or a crash) never observes a half-written file.
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            # ensure_ascii=False keeps emoji (e.g. 🙊) readable in the file on disk.
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
