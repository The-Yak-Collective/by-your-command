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
import os
from pathlib import Path
from typing import Any


def base_dir() -> Path:
    """Return the bot's state root, honoring ``XDG_STATE_HOME`` (default ~/.local/state)."""
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "by-your-command"


class JSONStore:
    """Reads and writes JSON files within one command's private state directory."""

    def __init__(self, command_name: str) -> None:
        # e.g. ${XDG_STATE_HOME}/by-your-command/showmymode/
        self.dir = base_dir() / command_name

    def _path(self, filename: str) -> Path:
        return self.dir / filename

    def exists(self, filename: str) -> bool:
        """True if the named state file has been written at least once."""
        return self._path(filename).is_file()

    def load(self, filename: str, default: Any = None) -> Any:
        """Return the parsed JSON contents, or ``default`` if the file is absent."""
        path = self._path(filename)
        if not path.is_file():
            return default
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

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
