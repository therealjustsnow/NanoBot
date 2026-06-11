# Setup guide

The fastest path from a fresh clone to a working NanoBot instance, in six steps.

> Prefer scripts? `./install.sh` (Linux/macOS) or `install.bat` (Windows) automates steps 1–3, then `./run.sh` / `run.bat` launches the bot.

## 1. Clone and enter the repo

```bash
git clone https://github.com/therealjustsnow/NanoBot.git
cd NanoBot
```

## 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Create `config.ini`

Copy `example_config.ini` to `config.ini`, then fill in at least the bot token.

```ini
[bot]
token = YOUR_BOT_TOKEN_HERE
default_prefix = n!
owner_id =

[groq]
groq_api_key =
```

See [Configuration](Configuration) for every available key.

## 4. Enable Discord intents

In the [Discord Developer Portal](https://discord.com/developers/applications), enable:

- **Server Members Intent**
- **Message Content Intent**

Without both intents, prefix commands and several moderation workflows will fail.

## 5. Run preflight

```bash
python run.py
```

The repository includes a preflight check (Python version, dependencies, config schema, directory structure) before a full bot launch.

## 6. Launch the bot

```bash
python main.py
```

Logs write to `logs/nanobot.log`.

---

## Minimal success path

What is required:

- Python 3.11 or newer.
- Discord bot token.
- Enabled Server Members and Message Content intents.
- Installed requirements from `requirements.txt`.

## Optional now, useful later

Features that need extra config:

- `GROQ_API_KEY` or `[groq] groq_api_key` for `/eli5`.
- Bot-list tokens for vote webhooks and posting.
- `[bot] owner_id` if you want explicit owner override.

## If upgrading: old JSON data migration

The repository includes an idempotent migration path for older JSON storage:

```bash
python migrate.py
```
