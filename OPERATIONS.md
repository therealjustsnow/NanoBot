# Operational Guarantees

What NanoBot promises about persistence, restarts, and failure behavior. Read
this before changing how a subsystem stores state or recovers — the guarantees
here are what users and other code rely on.

All persistent state lives in two local SQLite files (`data/nanobot.db` for bot
data, `data/cache.db` for external content cache). No cloud, no external store.

## Restart / shutdown

- **Graceful shutdown** (`close()`): fire-and-forget background tasks are
  cancelled; cog-owned tasks are cancelled by each cog's `cog_unload` on reload
  and otherwise stop with the event loop. Open SQLite connections are closed.
- **Crash / hard kill**: no cleanup runs. WAL mode means committed writes are
  durable; in-flight uncommitted writes are lost. Subsystems below describe what
  is reconstructed on the next start.
- **Cog reload** (`n!reload`): the cog's `cog_unload` cancels its tasks, then the
  fresh instance restores state from SQLite. Reload is safe and non-destructive.

## What survives a restart

| State | Survives restart? | Notes |
|---|---|---|
| Tags, notes, prefixes, warnings | Yes | `data/nanobot.db`, written synchronously. |
| Welcome/leave, automod, auditlog, role-panel config | Yes | `data/nanobot.db`. |
| Timed bans / mutes / slowmode (`unban_schedules`, `slow_schedules`) | Yes | Re-scheduled on startup; overdue actions fire immediately on restore. |
| Reminders & recurring reminders | Yes | Re-scheduled from `reminders` / `recurring_reminders` on startup. |
| Vote history | Yes | `data/nanobot.db`. |
| Music autoplaylist, 24/7 (`radio`) setting | Yes | `data/nanobot.db`, per guild. |
| Music queue | Only if `music_persist_queue` (default on) | Bot rejoins the last voice channel; the current track restarts from `0:00`; the rest of the queue is intact. |
| Music live playback position | No | In-memory; the current track restarts from the beginning. |
| External content cache (anime images, stories) | Yes, but disposable | `data/cache.db` — safe to delete; scrapers rebuild it. |
| `last_senders` / `last_banned` (mobile last-target state) | No | In-memory only; lost on restart. |
| Correlation ids / `logs/events.jsonl` | N/A (logs) | Rotating, ephemeral; safe to delete. |

## Delivery guarantees

- **Reminders / recurring reminders** — fire once under normal operation. The DB
  row is removed in a `finally` after the delivery attempt, so:
  - A **handled delivery error** (e.g. the channel is gone) drops the reminder —
    not retried (effectively at-most-once on error).
  - A **crash between sending and the DB delete** leaves the row, so it re-fires
    on the next start — so a hard crash can double-deliver (at-least-once).
  - Net: exactly-once in the common path; at-least-once across a crash.
- **Timed moderation** (unban / unmute / slowmode reset) — survive restart. On
  startup, anything already past due is applied immediately; future actions are
  re-scheduled. Re-applying an already-applied action is harmless (idempotent).

## Cache guarantees

- `data/cache.db` is a pure cache. Deleting it loses no bot state; the scrapers
  refill it on their normal schedule. It can be removed to reclaim disk.
- The music download cache (`data/music_cache/`) is disposable; entries are
  pruned by age/size when `music_save_videos` is on, and predownloaded files are
  deleted after they play.

## Expected failure behavior

- **Background tasks** retain strong references (no GC mid-flight) and log
  exceptions instead of failing silently. A failed background task does not take
  down the command that spawned it.
- **AutoMod regex** runs in a worker thread under a wall-clock timeout with input
  truncation and a backtracking heuristic, so a pathological pattern can't hang
  the event loop — it is skipped and logged.
- **YouTube rate-limit (HTTP 429)** triggers a configurable back-off
  (`music_ratelimit_cooldown`); optionally the bot leaves voice
  (`music_ratelimit_leave`).
- **Schema migrations** are forward-only and tracked by `PRAGMA user_version`;
  the version advances only after a migration succeeds, so a failed migration is
  retried on the next start (migrations must be written to be safe to re-run).
- **Config errors**: fatal issues (e.g. missing token) abort startup with a clear
  message; non-fatal issues log a warning and the affected key falls back to its
  default.
