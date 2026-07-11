# Configuration reference

Every key in `example_config.ini`, broken into sections. All keys are optional except `token` (or the `DISCORD_TOKEN` environment variable).

Live editing without a restart:

- `n!reloadconfig` — re-reads `config.ini` from disk.
- `n!config show|get|set|unset ...` — DM-only inspect/edit. Secrets are always masked when echoed back.
- `n!setloglevel <level>` — changes the log level and persists it to `config.ini`.

An old `config.json` is auto-migrated to `config.ini` on first start (the legacy file is renamed to `config.json.bak`).

## `[bot]`

| Key | Default | Description |
|---|---|---|
| `token` | `YOUR_BOT_TOKEN_HERE` | Bot token from the Discord Developer Portal. |
| `default_prefix` | `n!` | Default command prefix (max 5 chars, no spaces). |
| `owner_id` | *(blank)* | Discord user ID of the bot owner. Leave blank to use the application owner. |
| `db_encryption_key` | *(blank)* | Passphrase that encrypts the SQLite databases at rest via SQLCipher (blank = no encryption; needs `pip install sqlcipher3-binary`). The NANOBOT_DB_KEY environment variable overrides this. Losing the key loses the data. |
| `error_channel_id` | *(blank)* | Channel ID to receive Python warnings and unhandled asyncio errors. |
| `idle_status_message` | *(blank)* | Manual idle presence text. Blank = auto-rotate "Listening to /help \| /<command>" hourly. |
| `health_check_port` | `0` | Port for a plain-HTTP health-check endpoint (GET /health). 0 = disabled. |
| `health_check_host` | `0.0.0.0` | Bind address for the health endpoint. 0.0.0.0 = all interfaces; 127.0.0.1 = host-local only (the payload is unauthenticated). |

## `[logging]`

| Key | Default | Description |
|---|---|---|
| `log_level` | `INFO` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `log_http` | `false` | true = log every raw HTTP request (very noisy). |
| `log_events_jsonl` | `true` | Write structured command-lifecycle events to logs/events.jsonl. |
| `db_slow_query_ms` | `0` | Log any SQLite query slower than this many ms (0 = disabled). |

## `[votes]`

| Key | Default | Description |
|---|---|---|
| `topgg_token` | *(blank)* | top.gg AUTH token. |
| `topgg_v1_token` | *(blank)* | top.gg v1 API token used for commands sync. |
| `dbl_token` | *(blank)* | discordbotlist.com bot token. |
| `discordbotsgg_token` | *(blank)* | discord.bots.gg bot token. Server-count posting only — the site has no voting or vote-webhook API. |
| `vote_webhook_port` | `5000` | Local port the vote webhook listens on. |
| `vote_webhook_host` | `0.0.0.0` | Bind address for the vote webhook. 0.0.0.0 = all interfaces; 127.0.0.1 = host-local (e.g. behind a reverse proxy). |
| `vote_webhook_secret` | *(blank)* | Shared secret used by bot lists to authenticate webhooks. |
| `webhook_allowed_ips` | *(blank)* | Comma-separated IPs or CIDR ranges allowed to POST vote webhooks. Blank = allow all. |

## `[groq]`

| Key | Default | Description |
|---|---|---|
| `groq_api_key` | *(blank)* | Groq API key (free at console.groq.com). Powers /eli5 and WYR generation. The GROQ_API_KEY environment variable takes priority if set. |

## `[scraper]`

| Key | Default | Description |
|---|---|---|
| `fml_pages_per_scrape` | `500` | FML pages fetched per daily scrape (~5-10 stories each). |
| `wyr_requests_per_scrape` | `500` | Would-You-Rather API requests per rating per daily scrape. |
| `nekos_per_endpoint` | `400` | nekos.best images fetched per endpoint per daily scrape. |
| `nekosia_per_tag` | `400` | Nekosia images fetched per tag per daily scrape. |
| `revalidate_age` | `604800` | Age (seconds) before a cached URL is rechecked with HEAD. 604800 = 7 days. |
| `revalidate_batch` | `1000` | Max URLs to HEAD-check per 6-hour revalidation cycle. |
| `groq_wyr_system` | `You generate Would You Rather questions for a Discord bot. Return ONLY...` | System prompt used when Groq generates fresh WYR questions daily. |

## `[music]`

| Key | Default | Description |
|---|---|---|
| `music_cookie_file` | *(blank)* | Path to a Netscape cookies file exported from a logged-in browser. Needed for age-restricted or login-only content. |
| `music_default_volume` | `50` | Default playback volume (0–200). |
| `music_idle_timeout` | `180` | Seconds the player waits while idle or alone before leaving the channel. Minimum 30. |
| `music_skip_ratio` | `50` | Percent of voice-channel listeners who must vote to skip (0–100). Requesters and Manage Server can always force-skip. |
| `music_max_queue` | `500` | Maximum tracks allowed in a single server's queue. |
| `music_js_runtime_path` | *(blank)* | Path to a deno, node, or bun binary for yt-dlp's JavaScript interpreter. Leave blank to auto-detect. |
| `music_use_opus` | `true` | Send audio as Opus (default, cheaper) or PCM (true = instant volume/speed changes). |
| `music_persist_queue` | `true` | Save queue to disk so it survives a restart. Bot reconnects and resumes on startup. |
| `music_predownload` | `true` | Download the next queued track while the current one plays for instant track switching. Live streams are never pre-downloaded. |
| `music_self_deafen` | `true` | Self-deafen when joining a voice channel. |
| `music_default_speed` | `1.0` | Default playback speed (0.5–3.0). |
| `music_search_service` | `ytsearch` | Search service for non-URL queries: ytsearch, ytmsearch, or scsearch. |
| `music_status_message` | *(blank)* | Presence text while playing; `{title}` = song. YouTube/Twitch tracks show a Watch button. Blank = song title. |
| `music_proxy` | *(blank)* | HTTP/HTTPS proxy URL for yt-dlp. Example: http://user:pass@host:port. |
| `music_user_agent` | *(blank)* | Static User-Agent header for yt-dlp. Blank = yt-dlp default. |
| `music_source_address` | `0.0.0.0` | Local IP address yt-dlp binds outbound requests to. |
| `music_request_throttle` | `0` | Seconds to sleep between yt-dlp HTTP requests (decimals OK). Paces the bot so a proxy IP gets rate-limited more slowly. 0 = off; try 1 if a residential proxy IP keeps getting 403'd. |
| `music_player_client` | *(blank)* | Force a yt-dlp YouTube player client (comma-separated for fallbacks). With cookies yt-dlp may pick the `tv` client, which serves HLS/m3u8 that 403s through some proxies; a progressive client (`web_safari`, `mweb`, `android_vr`, `web`) streams cleanly. Blank = yt-dlp chooses. |
| `music_pot_provider_url` | *(blank)* | Base URL of a running [bgutil PO-token provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider). YouTube gates good formats behind a GVS PO token; without it yt-dlp returns "Requested format is not available" / HTTP 403. Install the `bgutil-ytdlp-pot-provider` plugin (in requirements.txt) **and** run its provider server (Docker, or Node/Deno). The plugin auto-finds a provider at `http://127.0.0.1:4416`; set this only for a non-default host/port. |
| `music_autoplay_autoskip` | `true` | When autoplay is filling a quiet queue and a user queues a real song, skip the autoplay track immediately. |
| `music_save_videos` | `false` | Keep downloaded audio in data/music_cache/ for instant replays. Pair with cache limits below. |
| `music_cache_max_mb` | `0` | Max cache size in MB (0 = unlimited). Only used when music_save_videos is on. |
| `music_cache_max_age_days` | `0` | Delete cached audio older than this many days (0 = never). |
| `music_ratelimit_cooldown` | `600` | Back-off time (seconds; accepts 10m, 10 minutes) after a YouTube rate-limit (HTTP 429). |
| `music_ratelimit_leave` | `false` | Leave voice channels when YouTube rate-limits the bot. |
| `music_apl_prune_on_error` | `true` | Remove autoplaylist entries that fail to play. |
| `music_save_history` | `true` | Save a per-server played-track history (for the n!history command). |
| `music_metadata_lookup` | `true` | Fill missing artist info via Apple's free iTunes Search API. |
| `music_sponsorblock` | `false` | Skip sponsor/non-music segments using the crowd-sourced SponsorBlock database. Forces a download before playback; live streams are unaffected. |
| `music_sponsorblock_categories` | `music_offtopic` | Comma-separated SponsorBlock categories to remove. Valid: sponsor, intro, outro, selfpromo, preview, filler, interaction, music_offtopic, poi_highlight. |
