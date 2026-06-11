# FAQ

Short answers covering setup, hosting, permissions, persistence, and why NanoBot behaves the way it does.

### Does NanoBot support slash commands and prefix commands?

Yes. Unless noted otherwise, NanoBot commands work as slash commands, prefix commands, and @mentions.

### Which Discord intents are required?

You need **Server Members Intent** and **Message Content Intent**. Without them, prefix commands and several moderation workflows will fail.

### Do I need a cloud database?

No. NanoBot uses SQLite as portable local storage with zero cloud dependency.

### Does reminder and moderation state survive restarts?

Yes. Timed bans, reminders, recurring reminders, and role panels all persist across restarts through stored state.

### Why are admin commands prefix-only?

Slash admin commands would appear in every user's slash menu, so owner admin controls stay prefix-only by design.

### Do I need a Groq key for the whole bot?

No. A Groq key is optional and only needed for `/eli5` and daily WYR generation described in the README and `cogs/eli5.py`.

### Can I reload config without a restart?

Yes. The README and admin cog both document `reloadconfig` and `config set/get` workflows.

### Where do logs go?

Logs are written to `logs/nanobot.log`. `docker-compose.yml` also mounts the host logs directory into the container.

### Can older JSON installs be migrated?

Yes. Run `python migrate.py` to import old JSON data into SQLite.

### Is Docker supported?

Yes. The repository includes a `Dockerfile` and `docker-compose.yml`. Compose mounts `config.ini`, `data/`, and `logs/` from the host.

### What Python version is required?

Python 3.11 or newer.
