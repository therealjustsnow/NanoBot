# The NanoBot Dashboard

A web front end for NanoBot: configure your server, and play the whole economy,
from a phone. Off by default — set `dashboard_port` in `config.ini` to switch it
on.

It is not a second bot. Every setting it changes is a row a Discord command
already changes, and every cast, dig and daily goes through the same atomic
claim `/fish`, `/mine` and `/daily` go through. There is no web cooldown and no
web wallet.

---

## What it does

**Configure** — AutoMod (a rule builder with plain-language descriptions and
examples), welcome and leave messages (a designer with a live preview), logging
(grouped by what the events *are*, not thirteen loose toggles), leveling and its
role rewards, your currency and shop, the on/off switches for fishing, the
activities and the casino, and the four setup-heavy features that used to mean a
dozen slash commands each: support tickets, birthdays, the new-account
gatekeeper, and music.

**Order things by dragging them** — self-role panels are the one feature that
exists here mostly *because* it's a web page. A panel's buttons are laid out in
one dimension, and in Discord the only way to move one was to remove it and add
it back, losing its label, emoji and style. The editor drags (Pointer Events, so
it works on touch as well as with a mouse), moves rows with ↑/↓ for keyboard
users, saves the whole order in one request, and re-renders the already-posted
message so what's in the channel is never a stale version of what's here.

**Diagnose** — a permission checklist per feature, so "the bot is broken" gets
answered on the page that configures it rather than at 3am. A channel the bot
can't post in is refused when you pick it, not discovered when the first message
silently fails.

**Moderate, carefully** — warn, timeout, kick, ban, unban and purge, behind a
framework rather than six buttons. Every action runs the same six checks before
anything happens: the feature is enabled, you hold the Discord permission, the
bot holds it too, Discord's role hierarchy allows it for *both* of you, the
target is a legal one (not yourself, not the bot, not the owner), and a reason
of real length has been given. Then it is confirmed — by typing the member's
name for a ban, and by a second confirmation for a bulk purge — and written to
the audit log naming the moderator and the dashboard as the source. Nothing here
can do something the same person couldn't do by typing the command.

**Play** — fishing (casting, the bag, the map, tackle, the dex), the adventure
loop (work, mining, hunting, exploring, encounters, Collect all), all five
casino games including a blackjack hand you can start in the browser and finish
in Discord, crafting, the inventory, the shop, the daily, leaderboards, and the
profile card.

**Manage what you're carrying** — search, filter and sort the inventory, then
use, sell, gift, bulk-sell behind a preview, or open a container. Two things the
chat list can't do: an item says *where it comes from*, derived from the drop
tables and recipes rather than written down, and a chest says it needs a key —
and how many you have — before you tap it rather than after it refuses.

**Dress the card while looking at it** — the wardrobe stages a whole loadout
across every cosmetic slot and previews it by rendering *your actual card*
through the same renderer Discord uses, then writes only the slots that changed.
What a banner looks like with your name, your avatar and your stats on top is
the entire thing being chosen, and it is the one question a text command can't
answer.

**Celebrate progress** — a progression page with both levels, prestige, titles,
the trophy case, weekly objectives, and what you are closest to earning next.

**Read the room** — an analytics page for admins over 7, 30 or 90 days, built
only from data the database genuinely holds. Where there is no history to plot,
it says so rather than inventing a trend line.

---

## Setting it up

1. **Register the redirect.** Discord Developer Portal → your application →
   OAuth2 → Redirects, add exactly:

   ```
   https://your-domain.example/api/auth/callback
   ```

2. **Fill in `[dashboard]`** in `config.ini`:

   ```ini
   [dashboard]
   dashboard_port = 8080
   dashboard_host = 127.0.0.1
   dashboard_base_url = https://your-domain.example
   dashboard_client_secret = <Developer Portal → OAuth2 → Client Secret>
   dashboard_session_secret = <python -c "import secrets; print(secrets.token_urlsafe(48))">
   ```

3. **Put it behind TLS.** The session cookie is `HttpOnly` + `SameSite=Lax`, and
   `Secure` is set automatically when `dashboard_base_url` is `https://`. Over
   plain `http://` the cookie travels in the clear; the config validator warns
   about this, and `http://localhost` is exempted so a dev instance works.

4. **Restart the bot.** If something's missing, the site still loads and the
   login screen names the config keys and shows the exact redirect URL to paste
   — that is the failure mode this is designed around.

The dashboard mounts on the same shared HTTP server as `/health` and the vote
webhook, so registrations that share a `(host, port)` are merged into one
listener. A host that grants a single inbound port can run all three.

Every one of these keys can also be set as `NANOBOT_<KEY>` in the environment
(see [`.env.example`](../.env.example)), which is the easier route on a container
or a PaaS. To host the *frontend* somewhere else entirely — GitHub Pages, a CDN,
a laptop — while the API stays with the bot, see
[docs/deployment.md](deployment.md); it needs no code changes, only
`dashboard_allowed_origins` and one JSON file on the static host.

---

## How it's built

```
web/
  security.py     signed sessions, CSRF, OAuth state   (pure, unit-tested)
  permissions.py  who may configure what + the permission map (pure)
  moderation.py   the six checks every moderation action passes (pure)
  oauth.py        the Discord OAuth round trip
  http.py         one error shape, the guards, the middleware
  app.py          the Dashboard object, guild resolution, route assembly
  engine/         the economy resolved without Discord
  api/            auth · guilds · settings · roles · modules
                  play · games · me · moderation · analytics
  static/         the front end (no build step)
```

### The engine, and why it exists

NanoBot's game logic already lives in two clean halves: pure roll-taking helpers
(`cogs/*/helpers.py`, deliberately Discord-free) and atomic database claims
(`utils/db/*.try_claim_*`). What sits between them in a cog is *presentation* —
building a `discord.Embed`. `web/engine/` re-orchestrates the same two halves and
returns plain dicts.

So the parts where divergence would actually hurt cannot diverge:

* **Cooldowns and one-time claims are the same rows.** A browser cast calls
  `db.try_claim_cast`, the statement `/fish` calls. You cannot cast on your phone
  and again on the site.
* **Every roll goes through the cogs' own helpers** — `rarity_odds`,
  `pick_spot_rarity`, `roll_vein`, `apply_streak` — imported, never copied.
  Rebalancing a table in `cogs/fishing/constants.py` moves the web with it.

What *is* restated is the order of operations. `tests/test_web_engine.py` pins
the behaviour and `tests/test_web_engine_parity.py` pins the *shape of the
dependency*: it walks the AST and fails if an engine module defines its own
`CAST_COOLDOWN`, stops calling a shared claim, imports `discord`, or builds an
embed. A copy-paste fix in one place fails CI rather than quietly making the
website a different game.

Two places the web deliberately differs, both asserted so they stay decisions:

* **Web casts never start a random fishing event.** Events are guild-wide and get
  announced in a channel; a browser cast has no channel, and a faucet that turns
  itself on where nobody can see it is worse than one that only starts in chat.
  Running events are still honoured.
* **`/rob` has no one-tap button**, for the same reason it has none in Discord:
  it takes a target, and a button can't ask for one. It has its own picker.

### Authorisation

The gate is Discord's own permission bits, read **live off the gateway** rather
than from the OAuth token. The OAuth guild list's `permissions` field is a
snapshot from whenever the token was minted; the bot is in the server with a live
member cache, so `assert_manager` asks the same object `/level set` asks. The
OAuth list only decides which servers to *offer*, which is a menu, not a gate.

There is no dashboard-only role model, and nothing here can grant anyone a power
they don't already have in Discord.

### Moderation: the framework before the buttons

Moderation is the one place where being convenient is not the goal, so
`web/moderation.py` is a pure decision layer that every action goes through
before anything happens, and `authorise()` runs its checks in a fixed order:

1. **Feature** — is this action even offered here.
2. **Actor permission** — the live gateway bit, not the OAuth snapshot.
3. **Bot permission** — refused *before* the confirmation dialog, not after the
   API call fails.
4. **Hierarchy** — Discord's own rule, checked for the moderator and the bot
   separately, because "I can ban them but the bot can't" is a different
   sentence from "you can't ban them".
5. **Target** — never yourself, never the bot, never the server owner.
6. **Reason** — required, length-bounded, and stamped with who did it and that
   it came from the dashboard, so the audit log reads the same as a command's.

Being pure means the rules are tested without a socket or a gateway, and being a
separate layer means a new action inherits all six by construction rather than by
someone remembering. On top of it the UI adds friction proportional to damage: a
plain confirmation for a warn, typed confirmation of the member's name for a ban,
and a second confirmation for a bulk purge. Discord's own limits are enforced
server-side too (28 days for a timeout, 100 messages for a bulk purge).

### Sessions and CSRF

A session is a JSON payload signed with HMAC-SHA256 in one `HttpOnly`,
`SameSite=Lax` cookie. Signed rather than stored, so a restart doesn't sign
everyone out — which matters for a bot that restarts on `!upgrade`. Every
mutating request additionally echoes a CSRF token derived from the session id
under a separate HMAC key (double-submit, no server-side table).

`verify_session` returns `None` for *every* failure mode — bad signature, wrong
version, expired, truncated, not JSON — so a caller that checks for `None` cannot
accidentally trust a malformed session.

### The front end

Plain ES modules, no build step: the files served are the files in the
repository. That is partly taste and partly the CSP, which forbids inline script
and external script hosts.

* `core/dom.js` — an ~80-line hyperscript helper. Text is set as `textContent`,
  never `innerHTML`: every string on the page comes from a server name, a
  nickname or an admin's own template, and none of those are trusted markup.
* `core/api.js` — the single door to the server. Carries the CSRF token,
  de-duplicates GETs, and turns an error into the server's own sentence.
* `core/ui.js` — the component vocabulary. `actionButton` owns one in-flight
  request at a time; `autoSave` debounces and *rolls back* on a refusal, so the
  screen never shows a state the database doesn't hold.
* `core/router.js` — real paths over the History API, so `/g/123/fishing` is a
  link you can send. A response that arrives after you've navigated away is
  dropped.

Mobile first, and not as a slogan: single column with a bottom tab bar, widening
to a side rail at 900px. 44px minimum touch targets, focus rings kept, a skip
link, `prefers-reduced-motion` honoured wholesale, and status never conveyed by
colour alone.

**No build step means no content hash, which means nothing may be cached by
age.** The modules import each other by relative path, so the graph is only
correct as a *set* — and a browser expires each file on its own clock. Cached
for an hour, an upgrade lands the new `views/adventure.js` next to the previous
deploy's `core/ui.js`, the view calls a helper that version doesn't export, and
the page dies on load (`ui.countdownUntil is not a function`) for anyone who had
opened the dashboard before the upgrade. So `/assets/*` is served `no-cache` —
kept in the cache, but revalidated, which aiohttp answers as a bodiless 304 from
the file's ETag. If a bundler with hashed filenames ever lands here, that is the
point at which long `max-age` becomes correct, and not before.

---

## Security notes

* **Read the whole file list before exposing this.** The dashboard serves
  `web/static/` and nothing else; the SPA fallback normalises the path and
  checks it still starts with the static directory before serving anything.
* **Nothing secret reaches the browser.** The session carries the user's OAuth
  access token (needed to re-read their guild list) and nothing else; the bot
  token, the client secret and the signing secret never leave the process.
* **An unexpected exception is a generic 500.** The detail goes to the log, never
  to the browser.
* **Every settings value is re-validated server-side.** A client-side check is a
  courtesy to the user, never a guarantee about the database. Regex patterns are
  additionally checked for catastrophic backtracking before they can be saved.
* **`dashboard_play_enabled = false`** makes the dashboard read-only for members
  while leaving it fully configurable for admins, if you'd rather the economy
  stayed in Discord.

---

## What the dashboard deliberately doesn't do

* **Change bot-wide settings.** Reward amounts and activity cooldowns mint into,
  and gate claims on, a *global* account, so they belong to the bot owner
  (`!econ`, `!cooldown`). They're shown read-only, with the reason, because
  hiding them makes the dashboard look incomplete and showing them without
  explaining makes it look broken.
* **Invent features Discord doesn't have.** No premium tier, no web-only
  currency, no mechanic that exists on one side only. The two deliberate
  exceptions are both *presentational* and both listed above: dragging a role
  panel into order, and previewing a cosmetic loadout before wearing it.
* **Fabricate history.** Analytics plots what the database records and no more.
  Where a number has no history behind it, the chart says so instead of drawing
  a shape.
* **Replace Discord.** Everything here is reachable from a command, and the
  terminology, cooldowns, prices and permissions are the same on both.
