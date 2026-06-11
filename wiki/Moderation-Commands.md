# Moderation Commands

Raid control, member removal, channel control, warnings, AutoMod, and audit logging — built for fast phone moderation.

**90 commands.** Syntax: `[]` = optional, `<>` = required. Unless noted otherwise every command works as a slash command, a prefix command (default `n!`), and an @mention command.

**On this page:** [Banning](#banning) · [Kicking & Timeouts](#kicking--timeouts) · [Channel Controls](#channel-controls) · [Warnings](#warnings) · [AutoMod](#automod) · [Audit Log](#audit-log) · [Role Panels](#role-panels) · [Welcome & Leave](#welcome--leave) · [Gatekeeper](#gatekeeper)

## Banning

Raid control and member removal tools built for fast phone moderation.

### `ban`

Permanently ban a user with an optional DM.

- **Usage:** `/ban [user] [message]` · `n!ban [user] [message]`
- **Example:** `n!ban @user You have been permanently banned.`
- **Permission:** Ban Members
- **Access:** Restricted · Slash + prefix

### `cban`

Ban + delete message history. Optional timed unban & DM.

- **Usage:** `/cban [user] [days] [wait] [message]` · `n!cban [user] [days] [wait] [message]`
- **Example:** `n!cban @user 7 24h See you tomorrow.`
- **Permission:** Ban Members
- **Aliases:** `cleanban`
- **Access:** Restricted · Slash + prefix

### `massban`

Ban multiple users by ID. Paste IDs separated by spaces.

- **Usage:** `/massban <id1 id2 ...> [reason]` · `n!massban <id1 id2 ...> [reason]`
- **Example:** `n!massban 111 222 333 Raid cleanup`
- **Permission:** Ban Members
- **Access:** Restricted · Slash + prefix

### `tempban`

Ban a user for a set duration. Auto-unbans when it expires.

- **Usage:** `/tempban [user] [duration] [reason]` · `n!tempban [user] [duration] [reason]`
- **Example:** `n!tempban @user 3d Repeated rule violations`
- **Permission:** Ban Members
- **Access:** Restricted · Slash + prefix

### `unban`

Unban a user by their User ID.

- **Usage:** `/unban <user_id> [reason]` · `n!unban <user_id> [reason]`
- **Example:** `n!unban 123456789012345678`
- **Permission:** Ban Members
- **Access:** Restricted · Slash + prefix

## Kicking & Timeouts

Fast kick and timeout actions for live moderation.

### `freeze`

Timeout a user (default 10m). They can't speak, react, or join VCs.

- **Usage:** `/freeze [user] [duration] [reason]` · `n!freeze [user] [duration] [reason]`
- **Example:** `n!freeze @user 30m Please cool down.`
- **Permission:** Moderate Members
- **Access:** Restricted · Slash + prefix

### `kick`

Kick a user. Defaults to last message sender.

- **Usage:** `/kick [user] [message]` · `n!kick [user] [message]`
- **Example:** `n!kick @user Please review the rules.`
- **Permission:** Kick Members
- **Access:** Restricted · Slash + prefix

### `unfreeze`

Remove a timeout from a user early.

- **Usage:** `/unfreeze <user>` · `n!unfreeze <user>`
- **Example:** `n!unfreeze @user`
- **Permission:** Moderate Members
- **Access:** Restricted · Slash + prefix

## Channel Controls

Locks, slowmode, cleanup, channel visibility, and voice moves.

### `clean`

Delete recent NanoBot messages from this channel.

- **Usage:** `/clean [amount]` · `n!clean [amount]`
- **Example:** `n!clean 20`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash + prefix

### `echo`

Send a message as NanoBot.

- **Usage:** `/echo [channel] <message>` · `n!echo [channel] <message>`
- **Example:** `n!echo #announcements Server maintenance in 10 minutes!`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash + prefix

### `hide`

Hide a channel from @everyone.

- **Usage:** `/hide [channel]` · `n!hide [channel]`
- **Example:** `n!hide #staff-only`
- **Permission:** Manage Channels
- **Access:** Restricted · Slash + prefix

### `lock`

Toggle @everyone channel lock. Run again to unlock.

- **Usage:** `/lock [channel] [reason]` · `n!lock [channel] [reason]`
- **Example:** `n!lock #general Temporary lock during raid.`
- **Permission:** Manage Channels
- **Access:** Restricted · Slash + prefix

### `moveall`

Move all members from one voice channel to another.

- **Usage:** `/moveall <to_channel> [from_channel]` · `n!moveall <to_channel> [from_channel]`
- **Example:** `n!moveall #General`
- **Permission:** Move Members
- **Access:** Restricted · Slash + prefix

### `nuke`

Clone this channel and delete the original — permanently wipes all messages.

- **Usage:** `/nuke [reason]` · `n!nuke [reason]`
- **Example:** `n!nuke raid cleanup`
- **Permission:** Manage Channels
- **Access:** Restricted · Slash + prefix

### `purge`

Bulk delete messages with optional filters.

- **Usage:** `/purge <amount> [bots] [user] [contains] [starts_with] [ends_with]` · `n!purge <amount> [bots] [user] [contains] [starts_with] [ends_with]`
- **Example:** `n!purge 50 user:@spammer`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash + prefix

### `slow`

Toggle slowmode. No args = toggle. Add delay and optional timer.

- **Usage:** `/slow [delay] [length]` · `n!slow [delay] [length]`
- **Example:** `n!slow 2m 1h`
- **Permission:** Manage Channels
- **Access:** Restricted · Slash + prefix

### `snailpurge`

Slow delete of older messages (no 14-day limit). Requires confirmation.

- **Usage:** `/snailpurge <amount>` · `n!snailpurge <amount>`
- **Example:** `n!snailpurge 200`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash + prefix

### `unhide`

Restore @everyone visibility on a hidden channel.

- **Usage:** `/unhide [channel]` · `n!unhide [channel]`
- **Example:** `n!unhide #announcements`
- **Permission:** Manage Channels
- **Access:** Restricted · Slash + prefix

## Warnings

Warn, list, clear, and configure warning thresholds.

### `clearwarnings`

Wipe all warnings for a user. Admin only.

- **Usage:** `n!clearwarnings <user>`
- **Example:** `n!clearwarnings @Reformed`
- **Permission:** Administrator
- **Access:** Restricted · Prefix

### `warn`

Issue a warning to a user. Configurable auto-kick/ban thresholds apply.

- **Usage:** `n!warn <user> [reason]`
- **Example:** `n!warn @Troublemaker Spamming in general`
- **Permission:** Manage Messages
- **Access:** Restricted · Prefix

### `warn clear`

Clear all warnings for a user. Admin only.

- **Usage:** `/warn clear <user>`
- **Example:** `/warn clear <user>`
- **Permission:** Administrator, Manage Messages
- **Access:** Restricted · Slash

### `warn config`

Configure auto-actions for warnings.

- **Usage:** `/warn config [kick_at] [ban_at] [dm_user]`
- **Example:** `/warn config [kick_at] [ban_at] [dm_user]`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash

### `warn issue`

Issue a warning to a user.

- **Usage:** `/warn issue <user> [reason]`
- **Example:** `/warn issue <user> [reason]`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash

### `warn list`

View all warnings for a user.

- **Usage:** `/warn list <user>`
- **Example:** `/warn list <user>`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash

### `warnconfig`

Configure auto-kick/ban thresholds and DM behavior.

- **Usage:** `n!warnconfig [kick_at] [ban_at] [dm_user]`
- **Example:** `n!warnconfig 3 5 true | n!warnconfig`
- **Permission:** Administrator
- **Access:** Restricted · Prefix

### `warnings`

View all warnings for a user on this server.

- **Usage:** `n!warnings <user>`
- **Example:** `n!warnings @Troublemaker`
- **Permission:** Manage Messages
- **Access:** Restricted · Prefix

## AutoMod

Passive rule system for spam, caps, mentions, bad words, regex, attachment filtering, and ignores.

### `automod badword add`

Add a word to the filter.

- **Usage:** `/automod badword add <word>`
- **Example:** `/automod badword add <word>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod badword list`

List all filtered words (shown only to you).

- **Usage:** `/automod badword list`
- **Example:** `/automod badword list`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod badword remove`

Remove a word from the filter.

- **Usage:** `/automod badword remove <word>`
- **Example:** `/automod badword remove <word>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod caps`

Configure the caps-abuse filter.

- **Usage:** `/automod caps [percent] [min_length]`
- **Example:** `/automod caps [percent] [min_length]`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod disable`

Disable AutoMod for this server.

- **Usage:** `/automod disable`
- **Example:** `/automod disable`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod enable`

Enable AutoMod for this server.

- **Usage:** `/automod enable`
- **Example:** `/automod enable`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod ignore channel`

Toggle a channel exemption.

- **Usage:** `/automod ignore channel <channel>`
- **Example:** `/automod ignore channel <channel>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod ignore role`

Toggle a role exemption.

- **Usage:** `/automod ignore role <role>`
- **Example:** `/automod ignore role <role>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod mentions`

Set the max @mentions allowed in a single message.

- **Usage:** `/automod mentions [limit]`
- **Example:** `/automod mentions [limit]`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod regex add`

Add a regex pattern to the filter.

- **Usage:** `/automod regex add <pattern> [label]`
- **Example:** `/automod regex add <pattern> [label]`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod regex list`

List all regex patterns in the filter (shown only to you).

- **Usage:** `/automod regex list`
- **Example:** `/automod regex list`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod regex remove`

Remove a regex pattern from the filter.

- **Usage:** `/automod regex remove <pattern>`
- **Example:** `/automod regex remove <pattern>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod regex test`

Test a string against all active regex patterns (shown only to you).

- **Usage:** `/automod regex test <text>`
- **Example:** `/automod regex test <text>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod rule`

Toggle a rule on/off and set its action.

- **Usage:** `/automod rule <rule> <enabled> [action] [dm_message]`
- **Example:** `/automod rule spam true kick`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod spam`

Set the spam detection threshold (messages per time window).

- **Usage:** `/automod spam <count> <seconds>`
- **Example:** `/automod spam <count> <seconds>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod status`

Show the current AutoMod configuration.

- **Usage:** `/automod status`
- **Example:** `/automod status`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod attachments`

Set the minimum attachment count that triggers the word+attachment rule.

- **Usage:** `/automod attachments [min_attachments]`
- **Example:** `/automod attachments 2`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod timeout`

Set how long the timeout action lasts (1–10080 minutes, default 10).

- **Usage:** `/automod timeout <minutes>`
- **Example:** `/automod timeout 30`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod attachword add`

Add a word to the attachment-word filter. Messages containing this word and enough attachments trigger the rule.

- **Usage:** `/automod attachword add <word>`
- **Example:** `/automod attachword add nitro`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod attachword remove`

Remove a word from the attachment-word filter.

- **Usage:** `/automod attachword remove <word>`
- **Example:** `/automod attachword remove nitro`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `automod attachword list`

List all words in the attachment-word filter (shown only to you).

- **Usage:** `/automod attachword list`
- **Example:** `/automod attachword list`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

## Audit Log

Server event logging to one channel, tuned for moderation workflows.

### `auditlog channel`

Set the channel for audit log entries.

- **Usage:** `/auditlog channel <channel>`
- **Example:** `/auditlog channel <channel>`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `auditlog disable`

Disable the audit log.

- **Usage:** `/auditlog disable`
- **Example:** `/auditlog disable`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `auditlog enable`

Enable the audit log.

- **Usage:** `/auditlog enable`
- **Example:** `/auditlog enable`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `auditlog events`

Toggle which events get logged.

- **Usage:** `/auditlog events`
- **Example:** `/auditlog events`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

### `auditlog status`

Show the current audit log configuration.

- **Usage:** `/auditlog status`
- **Example:** `/auditlog status`
- **Permission:** Administrator, Manage Server
- **Access:** Restricted · Slash

## Role Panels

Self-assign role panels and autogen role packs.

### `roles add`

Add a role to a panel.

- **Usage:** `/roles add <panel_id> <role> [label] [emoji] [style]`
- **Example:** `/roles add <panel_id> <role> [label] [emoji] [style]`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles autogen age`

Generate age-range roles (13-17, 18-20, 21-25, 26-30, 31+) and a panel.

- **Usage:** `/roles autogen age <channel> [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Example:** `/roles autogen age <channel> [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Permission:** Administrator
- **Access:** Restricted · Slash

### `roles autogen colors`

Generate 18 cosmetic colour roles and a single-choice colour panel.

- **Usage:** `/roles autogen colors <channel> [prefix] [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Example:** `/roles autogen colors <channel> [prefix] [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Permission:** Administrator
- **Access:** Restricted · Slash

### `roles autogen pronouns`

Generate She/Her, He/Him, They/Them, It/Its, Any/All roles and a panel.

- **Usage:** `/roles autogen pronouns <channel> [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Example:** `/roles autogen pronouns <channel> [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Permission:** Administrator
- **Access:** Restricted · Slash

### `roles autogen region`

Generate 7 world-region roles (N. America, Europe, Asia…) and a panel.

- **Usage:** `/roles autogen region <channel> [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Example:** `/roles autogen region <channel> [extra_role_1] [extra_role_2] [extra_role_3] [extra_role_4] [extra_role_5]`
- **Permission:** Administrator
- **Access:** Restricted · Slash

### `roles panel create`

Create a new role panel (not posted yet).

- **Usage:** `/roles panel create <title> [description] [mode]`
- **Example:** `/roles panel create <title> [description] [mode]`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles panel delete`

Delete a panel and remove its message.

- **Usage:** `/roles panel delete <panel_id>`
- **Example:** `/roles panel delete <panel_id>`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles panel edit`

Edit a panel's title, description, or mode.

- **Usage:** `/roles panel edit <panel_id> [title] [description] [mode]`
- **Example:** `/roles panel edit <panel_id> [title] [description] [mode]`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles panel list`

List all role panels in this server.

- **Usage:** `/roles panel list`
- **Example:** `/roles panel list`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles panel post`

Post (or re-post) a panel to a channel.

- **Usage:** `/roles panel post <panel_id> [channel]`
- **Example:** `/roles panel post <panel_id> [channel]`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles panel reload`

Re-post all panels in this server to refresh their messages.

- **Usage:** `/roles panel reload`
- **Example:** `/roles panel reload`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

### `roles remove`

Remove a role from a panel.

- **Usage:** `/roles remove <panel_id> <role>`
- **Example:** `/roles remove <panel_id> <role>`
- **Permission:** Manage Roles
- **Access:** Restricted · Slash

## Welcome & Leave

Join and leave messages with template variables and previews.

### `leave`

Configure or view leave message settings.

- **Usage:** `/leave [set` · `test]` · `n!leave [set` · `test]`
- **Example:** `/leave set enabled:True channel:#goodbye | /leave test`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `leave set`

Configure the leave message.

- **Usage:** `/leave set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm] or n!leave set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm]`
- **Example:** `/leave set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm] | n!leave set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm]`
- **Permission:** Administrator
- **Access:** Restricted · Slash + prefix

### `leave test`

Preview the leave message as if you just left.

- **Usage:** `/leave test or n!leave test`
- **Example:** `/leave test | n!leave test`
- **Permission:** Administrator
- **Access:** Restricted · Slash + prefix

### `welcome`

Configure or view welcome message settings.

- **Usage:** `/welcome [set` · `test]` · `n!welcome [set` · `test]`
- **Example:** `/welcome set enabled:True channel:#welcome | /welcome test`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `welcome set`

Configure the welcome message.

- **Usage:** `/welcome set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm] or n!welcome set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm]`
- **Example:** `/welcome set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm] | n!welcome set [enabled] [channel] [title] [content] [image_url] [image_text] [footer_text] [thumbnail] [color] [dm]`
- **Permission:** Administrator
- **Access:** Restricted · Slash + prefix

### `welcome test`

Preview the welcome message as if you just joined.

- **Usage:** `/welcome test or n!welcome test`
- **Example:** `/welcome test | n!welcome test`
- **Permission:** Administrator
- **Access:** Restricted · Slash + prefix

## Gatekeeper

New-account muting and captcha verification to keep bots and raid accounts out.

### `gk setup`

Create the Muted role and lock it out of all channels.

- **Usage:** `/gk setup`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk enable`

Enable the gatekeeper.

- **Usage:** `/gk enable`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk disable`

Disable the gatekeeper.

- **Usage:** `/gk disable`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk status`

Show the current gatekeeper configuration.

- **Usage:** `/gk status`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk role`

Use an existing role as the mute role instead of creating a new one.

- **Usage:** `/gk role <role>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk channel`

Set the fallback quarantine channel for members who have DMs closed.

- **Usage:** `/gk channel <channel>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk logchannel`

Set the channel for gatekeeper mute, verify, and kick log entries.

- **Usage:** `/gk logchannel <channel>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk minage`

Mute accounts younger than the given age. Example: 7d, 30d.

- **Usage:** `/gk minage <duration>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk unmuteage`

Auto-unmute muted members once their account reaches this age. Example: 35d.

- **Usage:** `/gk unmuteage <duration>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk kicktimeout`

Kick muted members who do not verify within this duration. Example: 48h.

- **Usage:** `/gk kicktimeout <duration>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk newaccounts`

Toggle automatic muting of accounts younger than the configured minimum age.

- **Usage:** `/gk newaccounts`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk noavatar`

Toggle automatic muting of members who join with no avatar set.

- **Usage:** `/gk noavatar`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk stockavatar`

Toggle automatic muting of members whose avatar matches a catalogued stock avatar.

- **Usage:** `/gk stockavatar`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk verify`

Toggle the captcha verification prompt that muted members must pass to regain access.

- **Usage:** `/gk verify`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk message`

Set the text shown to muted members in the verification prompt.

- **Usage:** `/gk message <text>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk learnavatar`

Add a member's current avatar to the stock-avatar catalog for future detection.

- **Usage:** `/gk learnavatar <member>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk checkavatar`

Test whether a member's avatar would match the stock-avatar catalog.

- **Usage:** `/gk checkavatar <member>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk sensitivity`

Set how closely an avatar must match the catalog to trigger muting. 0 = exact match; higher values are looser.

- **Usage:** `/gk sensitivity <value>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk matchmode`

Set whether the account-age and avatar checks must both trigger (AND) or either one (OR) to mute a member.

- **Usage:** `/gk matchmode <and` · `or>`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash

### `gk ageunmute`

Toggle automatic unmuting of age-flagged members once their account is old enough.

- **Usage:** `/gk ageunmute`
- **Permission:** Manage Guild
- **Access:** Restricted · Slash
