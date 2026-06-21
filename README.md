# By Your Command

> [!important]
> This code is under development, and has ***not*** yet been tested!

A small, modular Discord bot that (currently) provides two slash commands:

- **`/tfurl`** — "unfurl" a Discord message link by reposting that message's text in
  the current channel, attributed to its author and source channel.
- **`/showmymode`** — toggle a "listen mode" marker emoji (default 🙊) on your server
  nickname. The marker auto-removes after a timeout (default 90 minutes).

The codebase is built so that **adding a new slash command is as simple as
copying one file**.

## Requirements

- A Linux host with `cron` (for boot-start and the nightly refresh).
- [`uv`](https://docs.astral.sh/uv/) — installed automatically by `init.sh` if
  missing. Python itself is provided/managed by `uv`.
- A Discord application + bot token (see below).

## Discord application setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   and **New Application**.
2. Open the **Bot** tab:
   - **Reset Token** and copy it — this is your `DISCORD_BOT_TOKEN`.
   - Under **Privileged Gateway Intents**, enable both:
     - **Message Content Intent** (required by `/tfurl`).
     - **Server Members Intent** (required by `/showmymode`).
3. Invite the bot to your server (**OAuth2 → URL Generator**):
   - Scopes: `bot` and `applications.commands`.
   - Bot permissions: **View Channels**, **Send Messages**, **Read Message History**
     (so `/tfurl` can fetch linked messages), and **Manage Nicknames** (so
     `/showmymode` can edit nicknames).
   - Open the generated URL and add the bot to your server.

> **Nickname caveat:** Discord never lets *anyone* change the **server owner's**
> nickname, and a bot can only edit nicknames of members whose highest role is
> *below* the bot's highest role. If `/showmymode` reports it couldn't change your
> nickname, that role ordering (or a missing permission) is almost always why.

## Configuration

All configuration lives in a git-ignored `.env` file. Copy the template and fill it
in (or let `init.sh` create it for you):

```sh
cp .env.example .env
$EDITOR .env
```

| Variable             | Required | Description                                                                 |
| -------------------- | -------- | --------------------------------------------------------------------------- |
| `DISCORD_BOT_TOKEN`  | yes      | The bot token from the Developer Portal.                                     |
| `DISCORD_GUILD_ID`   | no       | A server ID. If set, commands sync instantly to that one server; otherwise they sync globally (can take ~1h). |

## Install and run

### The easy way (deployment)

```sh
./init.sh
```

`init.sh` is **idempotent** — safe to run repeatedly. It will:

1. ensure `uv` is installed,
2. install dependencies into a local `.venv` (`uv sync`),
3. create `.env` from `.env.example` **only if you don't already have one** (it never
   overwrites your configuration),
4. install two cron jobs (without ever duplicating them):
   - `@reboot` → start the bot when the machine boots,
   - `0 3 * * *` → run the nightly refresh (`scripts/update.sh`),
5. start the bot.

### Manually (development)

```sh
uv sync                  # create .venv and install dependencies
uv run python -m bot     # run the bot in the foreground
```

## Operations

The bot is managed as a single detached process via `scripts/bot.sh`:

```sh
scripts/bot.sh start     # start (no-op if already running)
scripts/bot.sh status    # report running / not running
scripts/bot.sh restart   # stop then start
scripts/bot.sh stop      # stop
```

The **nightly refresh** (`scripts/update.sh`, run by cron at 03:00) does a
fast-forward-only `git pull` from `origin`, runs `uv sync` to pick up any new
dependencies, and restarts the bot.

### Where runtime files live

Everything the bot writes at runtime lives under a single state root — the
[XDG state directory](https://specifications.freedesktop.org/basedir-spec/latest/) —
so the git checkout stays pure source:

```
${XDG_STATE_HOME:-$HOME/.local/state}/by-your-command/
├── bot.pid                 # PID of the running bot
├── logs/
│   ├── bot.log             # bot stdout/stderr
│   └── update.log          # nightly refresh log
└── showmymode/
    └── modes.json          # /showmymode listen-mode state
```

## Project layout

```
.
├── init.sh                 # idempotent setup + cron install
├── pyproject.toml          # project metadata + dependencies (managed by uv)
├── scripts/
│   ├── bot.sh              # start|stop|restart|status
│   └── update.sh           # nightly git pull + uv sync + restart
├── src/bot/
│   ├── __main__.py         # entry point (`python -m bot`)
│   ├── config.py           # loads & validates env config
│   ├── client.py           # builds the bot, auto-loads commands, runs maintenance
│   ├── state.py            # per-command persistent JSON state (XDG)
│   ├── maintenance.py      # registry for startup/periodic background actions
│   ├── utils.py            # small, unit-tested helpers
│   └── commands/           # ← one file per slash command (auto-discovered)
│       ├── tfurl.py
│       └── showmymode.py
└── tests/                  # unit tests for the pure helpers
```

## Adding a new slash command

1. Copy an existing command module, e.g.
   `cp src/bot/commands/tfurl.py src/bot/commands/mycommand.py`.
2. Rename the cog class and the command, and edit the body. Keep the
   `async def setup(bot)` footer that calls `bot.add_cog(...)`.
3. Restart the bot. It will be auto-discovered — there is **no central list** to
   update. (`src/bot/client.py` walks the `commands/` package at startup.)

### Adding background maintenance

A command can opt into periodic or one-time background work by registering callbacks
in its `setup(bot)`:

```python
from .. import maintenance

async def setup(bot):
    maintenance.register_startup("mycommand-init", my_startup_action)   # runs once, when ready
    maintenance.register_periodic("mycommand-tick", my_periodic_action) # runs every 15 min
    await bot.add_cog(MyCommand(bot))
```

Each action is an `async` function taking the bot. Use `state.JSONStore("mycommand")`
for any state it needs to persist. This is exactly how `/showmymode` implements its
auto-expiring marker.

## Design decisions

- **`uv` for everything.** One tool manages the Python version, the virtual
  environment, and dependencies. The committed `uv.lock` pins the full dependency
  tree so the nightly `uv sync` on the server installs exactly what was tested.
- **One file per command, auto-discovered.** Commands are discord.py *cogs* loaded
  via `pkgutil.iter_modules` + `load_extension`, so the core never hardcodes a
  command list. This was the biggest gap in the legacy code (one 289-line file).
- **`/showmymode`'s timeout is real and survives restarts.** The legacy `timer`
  parameter was never implemented. Here the expiry is backed by a JSON state file and
  enforced by an in-process sweep (every 15 minutes), so the nightly restart can't
  lose track of pending removals. On the very first run (no state file yet), a
  one-time scan adopts anyone already wearing the default 🙊 and gives them a fresh
  timeout; the state file is then never deleted, which is how the bot knows not to
  re-scan after a restart. (That scan can only detect the *default* marker, since it
  has no record of past custom characters.)
- **In-process maintenance, not a separate cron.** A `discord.ext.tasks` loop runs
  the sweep inside the already-connected bot. A separate cron process would have to
  open its own Discord gateway connection — which collides with the main bot on the
  same token — or reimplement Discord calls over raw REST. The in-process loop avoids
  both, needs no extra dependency, and is the single writer to the state file.
- **State (incl. PID and logs) lives under `XDG_STATE_HOME`, not `/run`.** The
  spec-canonical place for a PID file is `XDG_RUNTIME_DIR`, but that is usually unset
  under cron's `@reboot`, which would split the PID path between cron- and
  shell-invoked `bot.sh`. The persistent path is identical in every context, and
  `bot.sh` validates liveness with `kill -0`, so a stale PID file is harmless.
- **Multi-guild-safe and idempotent toggles.** `/tfurl` resolves channels via the
  invoking guild (with an API fetch fallback) instead of the legacy `guilds[0]`, and
  the nickname marker is added/removed idempotently (no more stacked 🙊🙊🙊).

## Development

```sh
uv run pytest            # run the unit tests (pure helpers only — no network)
uv run ruff format       # format the Python code
uv run ruff check        # lint the Python code
shfmt -w  scripts/*.sh init.sh   # format the shell scripts
shellcheck scripts/*.sh init.sh  # lint the shell scripts
```

Only the side-effect-free logic in `src/bot/utils.py` and `src/bot/state.py` is unit
tested (URL parsing, message chunking, nickname toggling, expiry selection, duration
validation, state round-trips). The Discord I/O is intentionally kept as thin call
sites around that tested logic.
