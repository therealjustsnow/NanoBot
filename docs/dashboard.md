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
role rewards, your currency and shop, and the on/off switches for fishing, the
activities and the casino.

**Diagnose** — a permission checklist per feature, so "the bot is broken" gets
answered on the page that configures it rather than at 3am. A channel the bot
can't post in is refused when you pick it, not discovered when the first message
silently fails.

**Play** — fishing (casting, the bag, the map, tackle, the dex), the adventure
loop (work, mining, hunting, exploring, encounters, Collect all), the inventory,
the shop, the daily, leaderboards, and the profile card.

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

---

## How it's built

```
web/
  security.py     signed sessions, CSRF, OAuth state   (pure, unit-tested)
  permissions.py  who may configure what + the permission map (pure)
  oauth.py        the Discord OAuth round trip
  http.py         one error shape, the guards, the middleware
  app.py          the Dashboard object, guild resolution, route assembly
  engine/         the economy resolved without Discord
  api/            auth · guilds · settings · play · me
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
* **Equip cosmetics.** The wardrobe shows the whole catalogue and how to get each
  one; equipping stays on `/profile equip`, which already handles picking several
  at once.
* **Replace Discord.** Everything here is reachable from a command, and the
  terminology, cooldowns, prices and permissions are the same on both.
