# Deploying the dashboard

The dashboard normally needs no deployment at all: set `dashboard_port` in
`config.ini` and the bot serves it itself, same-origin, on the same process that
is already talking to Discord. That is the setup everything defaults to, it is
the one with the fewest ways to go wrong, and if it is what you want then
[docs/dashboard.md](dashboard.md) is the whole story and you can stop here.

This document is about the other shape: the **frontend hosted somewhere else**
while the API stays with the bot. GitHub Pages, a CDN, an S3 bucket, a laptop —
anywhere that serves static files. It is worth doing when you want the interface
in front of people before the bot has a public hostname, when a CDN is closer to
your users than your bot host is, or when you would rather not expose the bot
process to the open internet at all.

Two things make it possible, and both are configuration rather than code:

* the frontend has **no build step and no hardcoded URLs** — it reads one file,
  `assets/config.json`, for the API origin and the path it is served under;
* the API has an **origin allow-list** (`dashboard_allowed_origins`) that, when
  set, permits credentialed cross-origin calls from exactly the sites you name.

---

## The three configurations

| | Frontend served by | `apiBase` | `basePath` | API needs |
|---|---|---|---|---|
| **Production (default)** | the bot | empty (same origin) | empty | nothing |
| **Split hosting** | Pages / CDN | `https://bot.example.com` | `/NanoBot` | `dashboard_allowed_origins` |
| **Development** | `python -m http.server` | `http://localhost:8080` | empty | `dashboard_allowed_origins` |

Nothing is compiled, so "switching configuration" means writing a different
`assets/config.json`. `scripts/build_dashboard.sh` writes it for you from two
environment variables; `.env.example` documents every variable in the project.

---

## Publishing to GitHub Pages

### 1. Give the bot a public URL

The frontend is static; the API is not. The bot has to be reachable from a
browser over **https** — a VPS behind Caddy or nginx, a small PaaS, a Cloudflare
tunnel, whatever you already run it on. Cross-origin sessions require `Secure`
cookies, and browsers do not send those to plain http (except to `localhost`),
so an http-only API works in development and nowhere else.

### 2. Configure the bot

In `config.ini` (or the matching `NANOBOT_*` environment variables — see
`.env.example`):

```ini
[dashboard]
dashboard_port = 8080
dashboard_host = 127.0.0.1
; The API's own public URL.
dashboard_base_url = https://bot.example.com
; Where the browser app is served from.
dashboard_frontend_url = https://yourname.github.io/NanoBot
dashboard_client_secret = <OAuth2 → Client Secret>
dashboard_session_secret = <python -c "import secrets; print(secrets.token_urlsafe(48))">
dashboard_allowed_origins = https://yourname.github.io
```

Three details that catch people out:

* **`dashboard_base_url` is the API, `dashboard_frontend_url` is the app.**
  Same-origin they are one URL and only the first is set. Split, they are two,
  and the login flow needs both: the OAuth callback has to land on the API
  (only the API holds the client secret, so only the API can exchange the
  code), and the browser is sent on to the app once the session cookie is set.
* **The OAuth redirect is the API's URL**, therefore — not the Pages one.
* `dashboard_allowed_origins` takes an **origin** — scheme and host, no path.
  `https://yourname.github.io`, not `https://yourname.github.io/NanoBot`. An
  entry with a path is ignored and logged, because a half-understood entry in a
  security allow-list is worse than a missing one. (`dashboard_frontend_url` is
  the opposite: it *may* carry a path, since a Pages project site has one.)

Then, in the [Discord developer portal](https://discord.com/developers/applications)
→ your application → OAuth2 → Redirects, add:

```
https://bot.example.com/api/auth/callback
```

Restart the bot. It logs the port it bound and the origins it will accept.

### 3. Configure the repository

Settings → Pages → Source: **GitHub Actions**.

Settings → Secrets and variables → Actions → **Variables**:

| Variable | Value | |
|---|---|---|
| `NANOBOT_API_BASE` | `https://bot.example.com` | required — the API's origin |
| `NANOBOT_BASE_PATH` | `/NanoBot` | optional — defaults to the repository name |

Neither is a secret. The frontend holds no credentials: it authenticates with a
cookie the API itself issued, and the OAuth client secret never leaves the bot.

### 4. Deploy

`.github/workflows/pages.yml` runs on any push to `main` that touches
`web/static/`, and on demand from Actions → *Deploy dashboard to Pages* → Run
workflow (which also takes a one-off API origin, for pointing a build at a
staging bot without editing the variable).

It copies `web/static/`, writes `assets/config.json`, copies `index.html` to
`404.html`, drops a `.nojekyll`, and rewrites the `<base href>`. That is the
entire build, and it is the same `scripts/build_dashboard.sh` you can run
locally — deliberately, so a local preview and a deployed site can't drift.

### Why `404.html`

The router uses real paths (`/g/123/fishing`), so links are shareable and the
back button behaves. GitHub Pages has no rewrite rules, so a deep link hits a
file that isn't there and Pages serves `404.html` — which, being a copy of the
app shell, boots the app, which then routes on the URL the browser asked for.
The user sees the page they asked for; the only trace is the status code.

---

## Developing against a local API

Two ways round, depending on which half you are working on.

### The frontend, against a bot on your machine

Run the bot with the dashboard on, and serve the frontend separately:

```bash
# Terminal 1 — the bot, with the API on 8000.
# config.ini:
#   dashboard_port = 8000
#   dashboard_base_url     = http://localhost:8000   (the API)
#   dashboard_frontend_url = http://localhost:8080   (the app)
#   dashboard_allowed_origins = http://localhost:8080
python run.py

# Terminal 2 — the frontend on 8080.
NANOBOT_API_BASE=http://localhost:8000 scripts/build_dashboard.sh dist
python -m http.server 8080 --directory dist
```

Add `http://localhost:8000/api/auth/callback` — the **API's** URL — to the
application's OAuth redirects. `localhost` is the one origin browsers exempt
from the `Secure` cookie requirement, so plain http works here and only here.

Editing `web/static/` means re-running the build script to copy the changes —
or skip the split entirely: drop `dashboard_frontend_url` and open the bot's own
port, which serves `web/static/` straight off disk with no copy in between. That
is the faster loop for most frontend work; the split setup is for testing the
split.

### The deployed Pages site, against a bot on your machine

Useful for reproducing something a user hit on the real site. Give the local
bot a public https URL — `cloudflared tunnel --url http://localhost:8000`, an
ngrok tunnel, anything — then run the Pages workflow by hand with that URL as
the `api_base` input. On the bot, point `dashboard_base_url` at the tunnel (and
register `<tunnel>/api/auth/callback` with Discord), set `dashboard_frontend_url`
to the Pages site, and allow its origin.

Deploy again with the variable's normal value when you are done. A published
site pointing at a tunnel that has closed is a page that loads and then reports
that it can't reach the API.

---

## Moving to a custom domain

The frontend was written so this costs a few lines of configuration.

### The frontend on a custom domain, API unchanged

1. Point a `CNAME` at `yourname.github.io` and set the domain in
   Settings → Pages → Custom domain. Wait for the certificate.
2. Set `NANOBOT_BASE_PATH` to `/` — a custom domain serves from the root, not
   from `/<repo>`.
3. Re-run the workflow.
4. On the bot: `dashboard_frontend_url = https://dash.example.com` and
   `dashboard_allowed_origins = https://dash.example.com`. `dashboard_base_url`
   does not change — the API hasn't moved, and it is still what the OAuth
   callback lands on.

That is the whole change, and the OAuth redirect stays exactly as it was. Only
the frontend moved, and Discord never knew where the frontend was.

### Both on one domain (and back to no CORS at all)

The tidiest end state. Put a reverse proxy in front: `/api/` to the bot,
everything else to the static files.

```nginx
server {
    server_name dash.example.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        root /srv/nanobot-dashboard;
        # Real paths again: fall back to the shell so deep links work.
        try_files $uri $uri/ /index.html;
    }
}
```

Then on the bot:

```ini
dashboard_base_url = https://dash.example.com
dashboard_frontend_url =
dashboard_allowed_origins =
```

Blanking those two is the point: the frontend and API are one origin again,
so the browser sends no `Origin` worth policing, the session cookie goes back to
`SameSite=Lax`, and the CORS middleware stops applying. Build the static files
with an empty `NANOBOT_API_BASE` (same-origin) and an empty `NANOBOT_BASE_PATH`.

At that point you may as well let the bot serve the files itself and drop the
static host — which is the default configuration, arrived at the long way round.

---

## Troubleshooting

**The page loads but everything says it can't reach the API.**
`assets/config.json` has the wrong `apiBase`, or the bot isn't reachable at it.
Open `<apiBase>/api/auth/me` directly — it is the one endpoint that answers
whether or not you are signed in, so JSON back means the API is up and reachable
and the problem is the origin the page was told to call.

**Signing in loops back to the login screen.**
The session cookie isn't sticking. Cross-origin needs `dashboard_allowed_origins`
set (which is what switches the cookie to `SameSite=None; Secure`) *and* https
on both ends. Check the browser console for a cookie rejection.

**"Invalid OAuth2 redirect_uri".**
`dashboard_base_url` + `/api/auth/callback` must appear verbatim in the
application's redirect list — the **API's** URL, not the frontend's. It is
exact: a trailing slash, `http` vs `https`, or `www.` all count as different.

**Login succeeds and lands on a blank page or a 404.**
`dashboard_frontend_url` is unset or wrong, so the callback sent the browser
back to the API's own host, where there is no page to show.

**Requests are refused with a CORS error in the console.**
The browser's `Origin` isn't in the allow-list. It is compared exactly — check
for a path on the entry, a trailing slash, or a `www.` mismatch. The bot logs
each entry it ignored and why.

**A write returns 403 with "Your session expired while you were away".**
That is the CSRF check, not CORS. Reload the page. CORS decides who may read an
answer; CSRF decides who may cause a write, and enabling one never disables the
other.

**A deep link 404s.**
`404.html` is missing, or the host isn't serving it for unknown paths. On Pages
the build makes it; behind nginx, `try_files … /index.html` is the equivalent.

**Assets 404 under a subpath.**
`NANOBOT_BASE_PATH` doesn't match where the site is actually served from. It is
what the `<base href>` is rewritten to, and every asset URL is relative to it.
