# NanoBot Privacy Policy

**Last updated: June 11, 2026**

This policy explains what data NanoBot ("the bot", "we") collects when it is
added to a Discord server, how that data is used, where it is stored, and how
you can have it removed.

NanoBot is an open-source Discord moderation bot. Its source code is publicly
available at <https://github.com/therealjustsnow/NanoBot>. This policy covers
the official hosted instance of NanoBot. Because the code is open source,
anyone can run their own copy; self-hosted instances are operated
independently and are not covered by this policy.

## The short version

- All data lives in a single local SQLite database on the machine that hosts
  the bot. There are **no cloud databases, no analytics services, and no data
  sales** — ever.
- NanoBot **does not store the content of your messages**. Message text is
  scanned in memory for commands and moderation rules, then discarded.
- NanoBot **does not store presence (online status / activity) data**. It is
  only displayed live when someone runs the `/user` command.
- Data that *is* stored (warnings, levels, reminders, etc.) is limited to what
  the bot's features need, and you or your server admins can ask for it to be
  deleted at any time.

## Data we collect and why

### Identifiers

NanoBot stores Discord **user IDs, server (guild) IDs, channel IDs, role IDs,
and message IDs** where a feature needs them. These are numeric identifiers
assigned by Discord, not personal information you typed.

### Per-feature data

| Feature | What is stored |
|---|---|
| Moderation | Timed-action schedules (e.g. "unban user X at time Y"): user ID, server ID, expiry time, and the reason a moderator typed. |
| Warnings | User ID, the reason text written by the moderator, the moderator's ID and username, and a timestamp. |
| Notes | Free-text notes a moderator writes about a user, with the user's ID. |
| Gatekeeper | For members pending verification: server ID, user ID, and scheduled unmute/kick times. Member avatars are downloaded **transiently** to compute a perceptual hash for stock-avatar detection and are not saved. The reference avatar catalog contains only images explicitly uploaded by server staff. |
| Welcome / audit log / automod / role panels | Server configuration only (channel IDs, toggles, rule settings, badword lists set by admins). |
| Reminders & recurring reminders | The reminder text you typed, your user ID, the channel ID, and when to fire. |
| Tags | The tag name and text snippet a user chose to save, with their user ID. |
| Leveling | Per-server XP totals and levels keyed by user ID. Message *counts* contribute to XP; message *content* is not stored. |
| Economy | Per-server coin balances, daily-claim timestamps, and streak counters keyed by user ID. |
| Music | Per-server settings, the saved server playlist, song/user block lists, recently played track titles/URLs with the requester's user ID (trimmed to the most recent 200), and — if queue persistence is enabled — the current queue. |
| Votes | When you vote for NanoBot on a bot list (e.g. top.gg), the list sends us your user ID and the time of the vote, which we store to thank voters and track vote history. |

### Message content

NanoBot requests Discord's Message Content intent because it must read
messages to:

- detect and run prefix commands (`n!ban`, custom prefixes, `@NanoBot ...`),
- run AutoMod rules (spam, invite links, excessive caps/mentions, banned
  words, regex filters),
- fire tag shortcuts (`n!tagname`),
- support "last sender" moderation targeting (we keep an **in-memory** note of
  the last message author per channel — never written to disk).

Message content is processed **in memory and immediately discarded**. It is
never written to the database, never sent to third parties, and never used to
train machine-learning or AI models.

One nuance: if a server enables audit logging for deleted or edited messages,
a short preview of the deleted/edited message is posted to that server's own
log channel. That content stays inside Discord, in a channel the server's
admins chose.

### Presence (online status)

NanoBot requests the Presence intent for exactly one feature: the `/user`
info command, which shows whether a member is online and what activity they
have set, at the moment the command is run. Presence data is **never logged,
stored, or tracked over time**.

### Diagnostic logs

The bot writes rotating log files on its host machine for debugging. These
contain command usage records (which command, which user ID, which server ID,
how long it took, and errors). They do **not** contain message content. Logs
rotate and old entries are overwritten automatically.

## Third-party services

Some features call external APIs. Here is exactly what leaves the bot:

| Service | What is sent |
|---|---|
| Groq (AI) | Only when you use `/eli5`: the question text you typed is sent to Groq's API to generate an explanation. Subject to [Groq's privacy policy](https://groq.com/privacy-policy/). |
| nekos.best / Nekosia | Nothing about you — the bot fetches image URLs for image/GIF commands. |
| YouTube / yt-dlp, Spotify (page metadata), iTunes Search, SponsorBlock | Song titles/URLs requested in music commands. No Discord user data is sent. |
| Bot lists (top.gg, Discord Bot List, discord.bots.gg) | The bot's server count. When you vote on those sites, they send us your user ID (see Votes above). |

NanoBot never sends your messages, member lists, or presence data to any
third party.

## Where data lives and how it's protected

All persistent data is stored in a local SQLite database file on the machine
that hosts the bot, operated by the bot owner. There is no cloud replication
and no third-party hosting of bot data. Access is limited to the bot operator.

## Data retention and deletion

- Most records are kept until a moderator or user deletes them through bot
  commands (e.g. clearing warnings, deleting tags, cancelling reminders).
- Music history is automatically trimmed to the most recent 200 tracks per
  server.
- If NanoBot is removed from a server, its stored data for that server stops
  being used and can be purged on request.
- **To request deletion of your data** (or all data for your server), contact
  the bot owner by opening an issue at
  <https://github.com/therealjustsnow/NanoBot/issues> or via the support
  contact listed on the bot's profile. Requests are honored within 30 days.

## Children

NanoBot is intended for users who meet Discord's minimum age requirement
(13, or higher where local law requires). We do not knowingly collect data
from anyone below that age.

## Changes to this policy

If this policy changes, the new version will be published at this same URL
with an updated "Last updated" date.

## Contact

Questions about this policy or your data: open an issue at
<https://github.com/therealjustsnow/NanoBot/issues>.
