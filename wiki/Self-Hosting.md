# Self-hosting guide

Everything after first launch: run paths, data directories, logs, Docker, and bot-owner maintenance commands.

## Run paths

### Direct local run (Python)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

### Docker

```bash
docker compose up --build -d
```

Docker Compose mounts `config.ini`, `data/`, and `logs/` from the host.

## Files and directories the repository expects

SQLite and logs stay local. No cloud database required.

| Path | Purpose |
|---|---|
| `data/` | SQLite data store and other runtime state. |
| `logs/` | Rotating logs write to `logs/nanobot.log`. |
| `config.ini` | Primary config file. Owner commands can reload and edit values. See [Configuration](Configuration). |
| `run.py` | Preflight checker: validates Python version, deps, structure, config, and token format before launch. |

## Runtime packages

`discord.py >= 2.3.0, aiosqlite >= 0.19.0, aiohttp >= 3.9.0, psutil >= 5.9.0, Pillow >= 10.0.0`

## YouTube PO-token provider (optional, fixes music 403s)

YouTube gates audio formats behind a **GVS PO token**. Without one, yt-dlp returns `Requested format is not available` or `HTTP 403` and music won't play — common when running from a flagged/datacenter IP. The fix is a local **[bgutil PO-token provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)** that the music cog's yt-dlp pulls tokens from.

Two pieces:

1. **Plugin** — already in `requirements.txt` (`bgutil-ytdlp-pot-provider`); installed by `install.sh` / `!upgrade`.
2. **Provider server** — a separate process on `127.0.0.1:4416`. Set it up once:

   ```bash
   # clone the matching version next to the bot
   git clone --single-branch --branch 1.3.1 \
     https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git ~/bgutil-pot
   # build with Deno (no Node needed; canvas pulls a prebuilt binary)
   cd ~/bgutil-pot/server && deno install --allow-scripts=npm:canvas --frozen
   ```

   Docker and Node setups are in the provider's README; the Deno path above avoids a native `canvas` compile, which is handy on minimal/ARM containers.

**Auto-start:** `run.sh` launches the provider before handing off to the bot, so it survives restarts. It is a **no-op unless installed** (checks for `$HOME/bgutil-pot/server/node_modules` and `$HOME/.deno/bin/deno`) and skips launch if something already answers on the port. Override the defaults with env vars if your layout differs:

| Env var | Default | Purpose |
|---|---|---|
| `BGUTIL_POT_DIR` | `$HOME/bgutil-pot/server` | Provider `server/` directory. |
| `BGUTIL_POT_PORT` | `4416` | Port the provider listens on. |
| `DENO_BIN` | `$HOME/.deno/bin/deno` | Deno binary used to run it. |

The plugin auto-detects a provider at `127.0.0.1:4416`. For a different host/port, set `music_pot_provider_url` (see [Configuration](Configuration)). Verify with `yt-dlp -v <url>` — look for `PO Token Providers: bgutil:http-… (external)`.

## Owner maintenance commands

Commands in `cogs/admin/` and `cogs/debug.py` require bot ownership. They are prefix-only by design (slash admin commands would appear in every user's slash menu).

### `cachestats`

Show cache DB statistics for FML, WYR, and image pools.

- **Usage:** `n!cachestats`
- **Aliases:** `cs`
- **Access:** Owner · Prefix

### `config`

DM-only: show, get, or set config values without restarting.

- **Usage:** `n!config <action> [key] [value]`
- **Example:** `n!config set default_prefix !`
- **Aliases:** `cfg`
- **Access:** Owner · Prefix

### `fmlpurge`

Wipe all cached FML stories and force a fresh scrape on next run.

- **Usage:** `n!fmlpurge`
- **Access:** Owner · Prefix

### `logs`

Print the last N lines of the log file directly in Discord. Defaults to 20 lines.

- **Usage:** `n!logs [lines]`
- **Example:** `n!logs 50`
- **Aliases:** `log`
- **Access:** Owner · Prefix

### `reload`

Hot-reload one cog by name, or reload every cog if no argument given.

- **Usage:** `n!reload [cog]`
- **Example:** `n!reload moderation`
- **Aliases:** `rl`
- **Access:** Owner · Prefix

### `reloadconfig`

Re-read config.ini at runtime without restarting the bot.

- **Usage:** `n!reloadconfig`
- **Aliases:** `rlc`, `rlconfig`
- **Access:** Owner · Prefix

### `restart`

Graceful shutdown then re-exec the process — equivalent to stop + start.

- **Usage:** `n!restart`
- **Aliases:** `reboot`, `rs`
- **Access:** Owner · Prefix

### `scrape`

Manually trigger the daily content cache scrape without waiting for the scheduler.

- **Usage:** `n!scrape`
- **Access:** Owner · Prefix

### `servers`

List all servers the bot is in, with member counts and IDs. Paginated.

- **Usage:** `n!servers [page]`
- **Example:** `n!servers 2`
- **Aliases:** `guilds`, `serverlist`
- **Access:** Owner · Prefix

### `setloglevel`

Change the active log level immediately and persist the new value to config.ini.

- **Usage:** `n!setloglevel <level>`
- **Example:** `n!setloglevel DEBUG`
- **Aliases:** `loglevel`, `loglvl`
- **Access:** Owner · Prefix

### `shutdown`

Flush logs and close the Discord connection before exiting the process.

- **Usage:** `n!shutdown`
- **Aliases:** `die`, `stop`
- **Access:** Owner · Prefix

### `sync`

Push slash command definitions to Discord — globally or scoped to a single guild.

- **Usage:** `n!sync [target] [guild_id]`
- **Example:** `n!sync guild 123456789012345678`
- **Access:** Owner · Prefix

### `unload`

Unload a single cog by name without restarting the bot.

- **Usage:** `n!unload <cog>`
- **Example:** `n!unload fun`
- **Aliases:** `ul`
- **Access:** Owner · Prefix

### `update`

Pull latest code from git and reload all cogs. Does not re-sync slash commands.

- **Usage:** `n!update`
- **Aliases:** `pull`
- **Access:** Owner · Prefix

### `upgrade`

Full upgrade: git pull, pip install, spawn a new process, then shut down the old one.

- **Usage:** `n!upgrade`
- **Aliases:** `deploy`, `ud`
- **Access:** Owner · Prefix

### `sh`

Run a shell command and see stdout + stderr in Discord. 60-second timeout by default — pass --disable-timeout for long jobs like downloads. Output truncated at 900 chars per stream.

- **Usage:** `n!sh [--disable-timeout] <command>`
- **Example:** `n!sh df -h`
- **Aliases:** `shell`, `exec`
- **Access:** Owner · Prefix

### `py`

Evaluate Python code inside the running bot process. Top-level await works. 60-second timeout by default — pass --disable-timeout to remove it. Output truncated at 900 chars.

- **Usage:** `n!py [--disable-timeout] <code>`
- **Example:** `n!py len(bot.guilds)`
- **Aliases:** `eval`, `python`
- **Access:** Owner · Prefix
