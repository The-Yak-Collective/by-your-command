"""Entry point for ``python -m bot`` (and the ``by-your-command`` console script).

Kept deliberately thin: configure logging, construct the bot, run it. A missing or
invalid configuration is reported as a single clear line rather than a traceback.
"""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    log = logging.getLogger("bot")

    # Import inside main so a configuration error (raised at config import time)
    # surfaces as a clean message and non-zero exit rather than an import crash.
    # ConfigError subclasses RuntimeError.
    try:
        from . import config
        from .client import ByYourCommandBot
    except RuntimeError as exc:
        log.error("configuration error: %s", exc)
        raise SystemExit(1) from exc

    bot = ByYourCommandBot()
    # log_handler=None: keep our basicConfig instead of discord.py installing its own.
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
