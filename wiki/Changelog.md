# Changelog

Every NanoBot release, newest first. Full release notes on [GitHub Releases](https://github.com/therealjustsnow/NanoBot/releases).

## v2.6.0 — Music *(May 22, 2026)*

Introduces a full-featured voice music player supporting YouTube and Spotify streams. Playback commands (`n!play`, `n!pause`, `n!skip`), queue management, audio filters (bassboost, nightcore, vaporwave, and more), and a persistent per-guild autoplaylist. Includes stability fixes for YouTube format selection and stream blocking.

Also adds a Debug cog for remote administration and last-banned-user tracking for easier mobile unbans.

**New commands:** play, playnext, playnow, stream, shuffleplay, search, join, pause, resume, skip, forceskip, jump, stop, queue, move, remove, clear, shuffle, pldump, volume, speed, filter, loop, seek, replay, nowplaying, lyrics, grab, autoplay, autoplaylist, follow

## v2.5.0 — AutoMod Escalation & Help Overhaul *(April 30, 2026)*

AutoMod gains `kick` and `softban` escalation actions, configurable timeout durations (1–10,080 minutes), and a compound `attachment_word` rule for scam detection. DM notifications now fire for all rule actions.

Help system receives major fixes including direct page jumping, improved category navigation, and corrected DM command responsiveness. Test suite expanded from 66 to 234 tests.

## v2.4.0 — Bug Fixes & More *(April 16, 2026)*

New mobile-first commands: `n!modcheck` combines user info and warnings, `n!firstmsg` jumps to oldest messages, `n!mc` shows member count, and `n!id` displays copyable identifiers. Adds Rock-Paper-Scissors game (`n!rps`).

Welcome embeds gain customization: hex colors, thumbnail options, footer text, and text overlay on banners via Pillow.

## v2.3.0 — New Help *(March 28, 2026)*

Launches four new cogs: **Fun** (59 social/reaction commands plus ship and magic 8-ball), **Images** (anime image pulls), **ELI5** (Groq-powered plain-language explanations), and **Recurring Reminders** (persistent scheduled reminders). Adds custom regex filtering to AutoMod and discord.bots.gg bot list support. Dynamic help system replaces static dictionary. Implements tag import/export functionality.

## v2.2.0 — Four New Cogs *(March 13, 2026)*

Introduces **AutoMod** with six toggleable rules (spam, invites, links, caps, mentions, badwords) and three actions (delete, warn, timeout). Adds **Audit Log** for twelve event types, **Role Panels** with persistent button-based self-assignment and autogen presets, and **Bot List Integration** supporting top.gg and discordbotlist.com with vote tracking and webhooks.

## v2.1.1 — Overhaul *(March 10, 2026)*

Migrates storage from JSON flat files to SQLite for concurrent access and performance. Introduces **Warnings** cog with configurable auto-kick/auto-ban thresholds and **Welcome & Leave** cog with template variables (`{user}`, `{mention}`, `{server}`, `{count}`). Adds seven moderation commands: softban, massban, tempban, snailpurge, nuke, hide/unhide, and echo. Implements centralized permission decorators and enhanced help with category browsing.

## v1.0.0 — Initial Release *(March 6, 2026)*

Foundation release featuring moderation tools (cban, ban, unban, kick, freeze, slow, lock, purge), mod notes system, personal and global tag system with image support, reminders (25 per user max), and info commands (server, user, avatar, banner, roleinfo). Includes owner/admin tools for cog reloading, restarts, and live log management. JSON-based storage with atomic writes and restart-safe timed actions.
