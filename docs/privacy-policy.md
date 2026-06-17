# NanoBot — Privacy Policy

**Effective date:** June 17, 2026

This Privacy Policy explains what data the NanoBot Discord application
("NanoBot", "the bot", "we", "us", or "our") collects, why, how it is stored,
and how you can have it removed. By using NanoBot you agree to this policy.

We collect the **minimum data needed to provide the bot's features**. NanoBot
stores its data in a single local SQLite database on the host operator's
machine. There is no cloud database and no selling of data.

## 1. What We Store

NanoBot stores data tied to Discord IDs (user IDs, server/guild IDs, channel
IDs, role IDs, message IDs). It does **not** collect real names, email
addresses, phone numbers, payment information, or IP addresses of command users.

Depending on which features a server enables, the bot may store:

| Feature | Data stored |
|---|---|
| Moderation | Moderator notes on users, timed action schedules (auto-unban / auto-unslow), target/moderator user IDs |
| Warnings | Warning records: target user ID, issuing moderator ID, reason text, timestamp |
| AutoMod | Per-guild rule configuration, custom bad-word / regex / attachment-word lists |
| Gatekeeper | Pending verification records for newly joined members (user ID, join/schedule timestamps) until verified or kicked |
| Leveling | Per-guild message XP and level totals per user, level/reward configuration |
| Economy | Per-guild NanoCoin balances, daily/streak timestamps |
| Music | Per-guild queue and play history (track titles/URLs and the requesting user's ID), guild playlist, song/user block lists |
| Tags | Personal and global text snippets and their author IDs |
| Reminders | Reminder and recurring-reminder content, target user/channel, due times |
| Roles / Welcome / Audit log | Per-guild configuration (panel definitions, message templates, log channel IDs) |
| Votes | Vote history (voter user ID, timestamp) from bot-list webhooks |
| Prefixes | Per-guild command prefix |

**Message content:** NanoBot processes message content only to detect commands,
fire tag shortcuts, award leveling XP, and enforce AutoMod rules. It does
**not** store the content of ordinary messages. AutoMod evaluates messages in
memory and stores only rule configuration and (where an action is taken) audit
log entries you have configured. The bot keeps the most recent message author
per channel **in memory only** (for "last sender" mod targeting); this is not
written to disk and is lost on restart.

## 2. Privileged Gateway Intents

NanoBot uses Discord **privileged intents** (such as Server Members and Message
Content) to provide its features:

- **Message Content** — required to read command text, fire tag shortcuts, award
  leveling XP, and run AutoMod. Content is processed transiently and not stored
  as described above.
- **Server Members / Presence** — required for moderation targeting, gatekeeper
  account-age and avatar checks, welcome/leave messages, and role assignment.

We request only the intents needed for the features above.

## 3. How Data Is Used

Stored data is used **solely** to operate the bot's features for the server it
belongs to — for example, to remember a user's level, balance, or warnings, to
restore timed actions after a restart, or to honor a server's configuration. We
do **not** sell, rent, or share your data with third parties for advertising or
marketing.

## 4. Data Sharing

Data is not shared except:

- As needed to operate the service through Discord's API.
- With third-party providers strictly to deliver a requested feature (for
  example, sending track titles to music sources, or posting aggregate server
  counts to bot-listing sites such as top.gg). These requests do not include
  your message content.
- Where required by law.

## 5. Data Retention

Data is retained while the relevant feature is in use and removed when no longer
needed:

- Gatekeeper pending records are cleared once a member verifies or is kicked.
- Removing NanoBot from a server, or deleting the server, makes that server's
  data eligible for deletion.
- The host operator may periodically purge data for servers the bot is no longer
  in.

## 6. Encryption at Rest

NanoBot supports optional database encryption at rest (SQLCipher). Whether it is
enabled depends on the host operator's configuration.

## 7. Your Rights and Choices

- **Access / deletion:** You may request a copy or deletion of the data NanoBot
  holds about you. Server-scoped data (warnings, notes) is typically managed by
  that server's moderators; contact them first where appropriate. For data the
  bot holds about you across features, contact us via the support server.
- **Server admins** can reset leveling, economy, warnings, and other data for
  their server using the bot's admin commands.
- **Opt-out:** You can stop generating new data by not interacting with the bot.
  Leaving servers where the bot is present, or having an admin remove the bot,
  stops further collection.

We will action verified deletion requests within a reasonable time, subject to
legal or operational retention needs.

## 8. Children's Privacy

NanoBot is not directed to children under 13 (or the minimum age of digital
consent in your country). We do not knowingly collect data from anyone below
that age. If you believe a child has provided data, contact us for removal.

## 9. Changes to This Policy

We may update this policy from time to time. Material changes will be reflected
by updating the "Effective date" above and, where practical, announced in the
support server. Continued use after changes take effect constitutes acceptance.

## 10. Contact

Privacy questions or data access / deletion requests:

- **Support server:** https://discord.gg/M7fjxNg72s
- **GitHub:** https://github.com/therealjustsnow/NanoBot

---

See also the [Terms of Service](./terms-of-service.md).
