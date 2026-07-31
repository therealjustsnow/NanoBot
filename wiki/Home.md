# NanoBot — Moderate from your phone

NanoBot is a lightweight Discord moderation bot designed around fast, thumb-friendly workflows — slash commands, prefix commands, and @mention support all in one.

> **Full docs site:** [therealjustsnow.github.io — NanoBot docs](https://github.com/therealjustsnow/portfolio-/blob/main/nanobot-docs.html) · **Invite:** [Add NanoBot to your server](https://discord.com/oauth2/authorize?client_id=1478550873457299603&scope=bot+applications.commands&permissions=1374675922134)

## Why mobile-first

- Big tap targets. Short, focused sections.
- Most moderation commands target the **last message sender** when no user is specified — no copying IDs on a phone keyboard.
- Slash, prefix (default `n!`), and @mention support across every command.
- Role panels are button-driven and persist across bot restarts.
- Tags work as both `n!tag hello` and shorthand `n!hello`.
- Reminders, timed bans, and recurring events all restore on boot.
- **No dashboard. SQLite local storage. Docker ready.**

## By the numbers

| | |
|---|---|
| **330** | Total command definitions (slash, prefix, and @mention variants) |
| **178** | Public commands — no elevated Discord permissions needed |
| **133** | Restricted commands — permission-gated for server staff |
| **19** | Owner admin commands — reload cogs, update, sync, manage config without restarting |
| **26** | Social actions — quick reaction-style prefix shortcuts |
| **33** | Fun one-liners — single-word commands for fast server banter |

## Find what you need

| Page | What's there |
|---|---|
| [Setup](Setup) | Six steps from a fresh clone to a running bot. No extra tooling required. |
| [Moderation Commands](Moderation-Commands) | Bans, kicks, timeouts, channel controls, warnings, AutoMod, audit log, role panels, welcome, gatekeeper. |
| [Utility Commands](Utility-Commands) | Info commands, reminders, tags, leveling, economy. |
| [Fun Commands](Fun-Commands) | Social actions, reaction GIFs, games, anime images. |
| [Music Commands](Music-Commands) | Full voice music player: playback, queue, filters, autoplay, playlists. |
| [Self-Hosting](Self-Hosting) | Docker path, data directories, logs, and every owner maintenance command. |
| [Configuration](Configuration) | Every `config.ini` key with defaults and descriptions. |
| [FAQ](FAQ) | Quick answers about intents, persistence, Groq setup, and Docker. |
| [Changelog](Changelog) | Every release, newest first. |

## What you need to run NanoBot

- Python 3.11 or newer.
- A Discord bot token with **Server Members** and **Message Content** intents enabled.
- Dependencies from `requirements.txt`.
- Optional: Groq API key for Would-You-Rather generation.
