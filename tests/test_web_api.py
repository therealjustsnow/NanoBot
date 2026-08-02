"""
Tests for the dashboard's HTTP surface — routing, auth, CSRF, and the gate.

Driven against a real bound aiohttp server with a duck-typed bot standing in for
the gateway, which is the same trick the repo's view tests use where dpytest
can't build the real object. What is being asserted is the stuff that is only
ever wrong once: that an unauthenticated request can't reach anything, that a
member can't reach an admin endpoint, that a POST without a CSRF token is
refused, and that the SPA's deep links survive a reload without letting anyone
read files outside the static directory.
"""

import json

import aiohttp
import aiosqlite
import pytest

import utils.db as db
from web import permissions as perms
from web import security
from web.app import Dashboard

SECRET = "s" * 48
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

OWNER_ID = 1
ADMIN_ID = 2
MEMBER_ID = 3
OUTSIDER_ID = 4
GUILD_ID = 555


# ── the stand-in gateway ──────────────────────────────────────────────────────
class FakePermissions:
    """Stands in for `discord.Permissions`: an int plus the named attributes.

    The flag names are derived from `web.permissions.PERMISSION_LABELS` rather
    than typed out, so a feature that starts checking a new permission gets a
    working fake for free instead of an AttributeError deep in a handler.
    Administrator implies everything, exactly as Discord's own object does.
    """

    _BITS = {
        label.lower().replace(" ", "_"): bit
        for bit, label in perms.PERMISSION_LABELS.items()
    }
    # A couple the dashboard checks that aren't in the feature map's labels.
    _BITS.update(
        {
            "administrator": perms.ADMINISTRATOR,
            "manage_guild": perms.MANAGE_GUILD,
            "timeout_members": perms.MODERATE_MEMBERS,
            "moderate_members": perms.MODERATE_MEMBERS,
        }
    )

    def __init__(self, value):
        self.value = value

    def __getattr__(self, name):
        bit = FakePermissions._BITS.get(name)
        if bit is None:
            raise AttributeError(name)
        value = self.__dict__.get("value", 0)
        return bool(value & perms.ADMINISTRATOR) or bool(value & bit)


class FakeRole:
    def __init__(self, name="role", position=1, managed=False, default=False):
        self.id = 900 + position
        self.name = name
        self.position = position
        self.managed = managed
        self._default = default
        self.color = type("C", (), {"value": 0})()
        self.members = []

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position


class FakeMember:
    def __init__(self, uid, permissions=0, bot=False, top_role=5):
        self.id = uid
        self.name = f"user{uid}"
        self.display_name = f"User {uid}"
        self.bot = bot
        self.guild_permissions = FakePermissions(permissions)
        self.nick = None
        self.joined_at = None
        # Where they sit in the role hierarchy. Moderation is the only thing
        # that reads it, and it reads it for the actor, the target and the bot
        # separately.
        self.top_role = FakeRole("top", position=top_role)
        self.display_avatar = type("A", (), {"url": "https://example.invalid/a.png"})()
        self.kicked = None
        self.timed_out = None

    async def kick(self, reason=None):
        self.kicked = reason

    async def timeout(self, until, reason=None):
        self.timed_out = (until, reason)


class FakeChannel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name
        self.category = None
        self.position = 0

    def is_nsfw(self):
        return False

    def permissions_for(self, member):
        return type(
            "P", (), {"send_messages": True, "view_channel": True, "embed_links": True}
        )()


class FakeGuild:
    def __init__(self):
        self.id = GUILD_ID
        self.name = "Test Server"
        self.icon = None
        self.banner = None
        self.member_count = 3
        self.owner_id = OWNER_ID
        self.created_at = type("D", (), {"timestamp": lambda self: 1_600_000_000.0})()
        self.premium_subscription_count = 0
        self.premium_tier = 0
        self.me = FakeMember(99, permissions=(1 << 3))  # bot has Administrator
        self.members = [
            FakeMember(OWNER_ID, permissions=0),
            # Manage Server *plus* the moderation permissions, and a role below
            # the bot's but above the member's — the ordinary shape of a
            # moderator. They are separate bits on purpose: Manage Server is
            # what opens the dashboard, and it is deliberately not what lets
            # anyone ban from it.
            FakeMember(
                ADMIN_ID,
                permissions=(
                    perms.MANAGE_GUILD
                    | perms.MANAGE_MESSAGES
                    | perms.KICK_MEMBERS
                    | perms.BAN_MEMBERS
                    | perms.MODERATE_MEMBERS
                ),
                top_role=4,
            ),
            FakeMember(MEMBER_ID, permissions=0, top_role=1),
        ]
        self.bans = []
        self.text_channels = [FakeChannel(10, "general")]
        # The birthday setup guesses a timezone from voice-region hints, so an
        # empty list is a real state it has to cope with.
        self.voice_channels = []
        self.roles = [
            FakeRole("@everyone", position=0, default=True),
            FakeRole("member", 1),  # id 901 — below the bot's top role, assignable
            FakeRole("bot", 5),  # id 905 — the bot's own, never assignable
        ]

    def get_member(self, uid):
        return next((m for m in self.members if m.id == uid), None)

    async def fetch_member(self, uid):
        member = self.get_member(uid)
        if member is None:
            raise LookupError
        return member

    def get_channel(self, cid):
        return next((c for c in self.text_channels if c.id == cid), None)

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == rid), None)

    async def ban(self, user, reason=None, delete_message_days=0):
        self.bans.append((user.id, reason, delete_message_days))


class FakeBot:
    def __init__(self):
        self.config = {
            "dashboard_port": PORT,
            "dashboard_host": "127.0.0.1",
            "dashboard_base_url": BASE,
            "dashboard_client_id": "1234",
            "dashboard_client_secret": "secret",
            "dashboard_session_secret": SECRET,
        }
        self.guild = FakeGuild()
        self.guilds = [self.guild]
        self.user = type(
            "U",
            (),
            {
                "id": 99,
                "name": "NanoBot",
                "display_avatar": type("A", (), {"url": ""})(),
            },
        )()
        self.owner_ids = set()
        self.owner_id = None
        self.application_id = 1234
        self.latency = 0.05

    def get_guild(self, gid):
        return self.guild if gid == GUILD_ID else None

    def is_ready(self):
        return True

    def get_cog(self, name):
        return None


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
async def server(monkeypatch):
    from utils.webserver import HttpServer

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    for setup in (
        db._ensure_economy_tables,
        db._ensure_fishing_tables,
        db._ensure_items_tables,
        db._ensure_activities_tables,
        db._ensure_identity_tables,
        db._ensure_settings_tables,
        db._ensure_automod_tables,
        db._ensure_welcome_tables,
        db._ensure_auditlog_tables,
        db._ensure_leveling_tables,
        db._ensure_casino_tables,
        db._ensure_progression_tables,
        db._ensure_social_tables,
        db._ensure_role_panels_tables,
        db._ensure_ticket_tables,
        db._ensure_birthday_tables,
        db._ensure_gatekeeper_tables,
        db._ensure_music_tables,
        db._ensure_warnings_tables,
    ):
        await setup()

    dash = Dashboard(FakeBot())
    http = HttpServer(bind_attempts=8, bind_delay=0.25)
    http.register("dash", "127.0.0.1", PORT, dash.routes(), dash.middlewares())
    await http.restart()
    try:
        yield dash
    finally:
        await http.stop()
        await dash.close()
        await conn.close()


def cookie_for(uid: int) -> dict:
    token = security.sign_session(
        {"uid": str(uid), "name": f"User {uid}"}, SECRET, 3600
    )
    return {security.SESSION_COOKIE: token}


def csrf_for(uid: int) -> str:
    token = security.sign_session(
        {"uid": str(uid), "name": f"User {uid}"}, SECRET, 3600
    )
    return security.csrf_for(security.verify_session(token, SECRET), SECRET)


class Client:
    """A tiny session helper that carries one user's cookie and CSRF token."""

    def __init__(self, session, uid=None):
        self.session = session
        self.uid = uid
        self.token = None
        self.cookies = {}
        if uid is not None:
            signed = security.sign_session(
                {"uid": str(uid), "name": f"User {uid}"}, SECRET, 3600
            )
            self.cookies = {security.SESSION_COOKIE: signed}
            self.token = security.csrf_for(
                security.verify_session(signed, SECRET), SECRET
            )

    async def get(self, path, **kwargs):
        return await self.session.get(f"{BASE}{path}", cookies=self.cookies, **kwargs)

    async def post(self, path, body=None, *, csrf=True):
        headers = {"Content-Type": "application/json"}
        if csrf and self.token:
            headers[security.CSRF_HEADER] = self.token
        return await self.session.post(
            f"{BASE}{path}",
            data=json.dumps(body or {}),
            headers=headers,
            cookies=self.cookies,
        )

    async def patch(self, path, body=None, *, csrf=True):
        headers = {"Content-Type": "application/json"}
        if csrf and self.token:
            headers[security.CSRF_HEADER] = self.token
        return await self.session.patch(
            f"{BASE}{path}",
            data=json.dumps(body or {}),
            headers=headers,
            cookies=self.cookies,
        )


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


# ══════════════════════════════════════════════════════════════════════════════
#  Auth
# ══════════════════════════════════════════════════════════════════════════════
async def test_me_answers_when_nobody_is_signed_in(server, http):
    """The app's first call has to work signed-out, or the login screen can't
    tell the user why they can't sign in."""
    async with await Client(http).get("/api/auth/me") as response:
        assert response.status == 200
        body = await response.json()
    assert body["user"] is None
    assert body["configured"] is True
    assert "csrf" not in body


async def test_me_carries_identity_and_a_csrf_token(server, http):
    async with await Client(http, MEMBER_ID).get("/api/auth/me") as response:
        body = await response.json()
    assert body["user"]["id"] == str(MEMBER_ID)
    assert body["csrf"]


async def test_login_redirects_to_discord(server, http):
    async with await Client(http).get(
        "/api/auth/login", allow_redirects=False
    ) as response:
        assert response.status == 302
        location = response.headers["Location"]
    assert location.startswith("https://discord.com/oauth2/authorize")
    assert "scope=identify+guilds" in location or "identify%20guilds" in location
    assert "redirect_uri=" in location


async def test_callback_refuses_an_unsigned_state(server, http):
    """The state proves the browser started this login. Without that check the
    callback is a free-standing 'log me in as whatever Discord says' endpoint."""
    async with await Client(http).get(
        "/api/auth/callback?code=abc&state=forged", allow_redirects=False
    ) as response:
        assert response.status == 302
        assert "error=state" in response.headers["Location"]


async def test_callback_reports_a_cancelled_login(server, http):
    async with await Client(http).get(
        "/api/auth/callback?error=access_denied", allow_redirects=False
    ) as response:
        assert "error=denied" in response.headers["Location"]


# ══════════════════════════════════════════════════════════════════════════════
#  The gate
# ══════════════════════════════════════════════════════════════════════════════
async def test_everything_needs_a_session(server, http):
    anon = Client(http)
    for path in (
        f"/api/guilds/{GUILD_ID}/overview",
        f"/api/guilds/{GUILD_ID}/settings/automod",
        f"/api/guilds/{GUILD_ID}/fishing",
        "/api/me",
    ):
        async with await anon.get(path) as response:
            assert response.status == 401, path
            body = await response.json()
        assert body["error"]["code"] == "unauthorized"


async def test_a_member_can_read_the_overview(server, http):
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/overview"
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert body["guild"]["name"] == "Test Server"
    assert body["can_manage"] is False


async def test_a_member_cannot_reach_settings(server, http):
    """The gate is Manage Server, exactly as it is for `/level set`."""
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/settings/automod"
    ) as r:
        assert r.status == 403
        body = await r.json()
    assert "Manage Server" in body["error"]["message"]


async def test_an_admin_can_reach_settings(server, http):
    async with await Client(http, ADMIN_ID).get(
        f"/api/guilds/{GUILD_ID}/settings/automod"
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert "rules" in body["meta"]


async def test_the_guild_owner_counts_as_a_manager(server, http):
    """Owner ids beat the permission integer — Discord's own rule."""
    async with await Client(http, OWNER_ID).get(
        f"/api/guilds/{GUILD_ID}/settings/automod"
    ) as r:
        assert r.status == 200


async def test_someone_outside_the_server_is_refused(server, http):
    async with await Client(http, OUTSIDER_ID).get(
        f"/api/guilds/{GUILD_ID}/fishing"
    ) as r:
        assert r.status == 403


async def test_an_unknown_guild_says_so(server, http):
    async with await Client(http, ADMIN_ID).get("/api/guilds/99999/overview") as r:
        assert r.status == 404
        body = await r.json()
    assert "isn't in that server" in body["error"]["message"]


# ══════════════════════════════════════════════════════════════════════════════
#  CSRF
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_write_without_a_csrf_token_is_refused(server, http):
    """SameSite=Lax stops a cross-site form post; this is the actual policy."""
    client = Client(http, MEMBER_ID)
    async with await client.post(
        f"/api/guilds/{GUILD_ID}/fishing/cast", csrf=False
    ) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"]["hint"] == "csrf"


async def test_a_write_with_another_sessions_token_is_refused(server, http):
    client = Client(http, MEMBER_ID)
    client.token = csrf_for(ADMIN_ID)
    async with await client.post(f"/api/guilds/{GUILD_ID}/fishing/cast") as r:
        assert r.status == 403


async def test_a_write_with_the_right_token_goes_through(server, http):
    client = Client(http, MEMBER_ID)
    async with await client.post(f"/api/guilds/{GUILD_ID}/fishing/cast") as r:
        assert r.status == 200
        body = await r.json()
    assert body["outcome"] in ("catch", "snag")


async def test_reads_do_not_need_a_csrf_token(server, http):
    client = Client(http, MEMBER_ID)
    client.token = None
    async with await client.get(f"/api/guilds/{GUILD_ID}/fishing") as r:
        assert r.status == 200


# ══════════════════════════════════════════════════════════════════════════════
#  Play refusals
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_cooldown_is_a_409_carrying_retry_after(server, http):
    """A refusal is a normal outcome of asking, not a malformed request — and
    the button needs the number to show a countdown."""
    client = Client(http, MEMBER_ID)
    await (await client.post(f"/api/guilds/{GUILD_ID}/fishing/cast")).release()
    async with await client.post(f"/api/guilds/{GUILD_ID}/fishing/cast") as r:
        assert r.status == 409
        body = await r.json()
    assert body["error"]["code"] == "cooldown"
    assert body["error"]["retry_after"] > 0


async def test_play_can_be_switched_off_instance_wide(server, http, monkeypatch):
    monkeypatch.setattr(server, "play_enabled", False)
    client = Client(http, MEMBER_ID)
    async with await client.post(f"/api/guilds/{GUILD_ID}/fishing/cast") as r:
        assert r.status == 403
    # Reads still work, so the dashboard stays useful.
    async with await client.get(f"/api/guilds/{GUILD_ID}/fishing") as r:
        assert r.status == 200


# ══════════════════════════════════════════════════════════════════════════════
#  Reads that assemble a lot of state
#
#  Each of these fans out over a dozen accessors and registries, which is exactly
#  where a signature that quietly returns three values instead of two hides. A
#  200 is most of the assertion.
# ══════════════════════════════════════════════════════════════════════════════
async def test_account_summary_reads(server, http):
    async with await Client(http, MEMBER_ID).get("/api/me") as r:
        assert r.status == 200
        body = await r.json()
    assert body["level"] == 0
    assert body["into"] == 0 and body["need"] > 0
    assert body["balance"] == 0


async def test_profile_reads_both_levels(server, http):
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/me/profile"
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert body["global_level"]["scope"] == "Every server"
    assert body["server_level"]["scope"] == "Test Server"
    assert body["wallet"]["currency"]["name"]
    assert body["cosmetics"]["slots"]["badge"]["max"] > 1


async def test_the_wardrobe_lists_locked_cosmetics_too(server, http):
    """A wardrobe that only showed what you own would answer no questions."""
    async with await Client(http, MEMBER_ID).get("/api/me/cosmetics") as r:
        assert r.status == 200
        body = await r.json()
    assert body["cosmetics"]
    assert any(not row["owned"] for row in body["cosmetics"])
    for row in body["cosmetics"]:
        assert row["unlock"], f"{row['key']} doesn't say how to get it"


async def test_the_wardrobe_says_what_you_can_afford(server, http):
    """A row marked only with its price reads as an offer, and most of them
    aren't one — the balance is what lets the listing tell those apart."""
    async with await Client(http, MEMBER_ID).get("/api/me/cosmetics") as r:
        body = await r.json()
    assert "balance" in body
    assert isinstance(body["balance"], int)
    assert any(row["for_sale"] and row["price"] > 0 for row in body["cosmetics"])


async def test_account_summary_rank_is_a_position(server, http):
    """`get_econ_rank` answers (position, coins). The summary card prints the
    rank straight, so the pair came out as "#0 richest"."""
    await db.add_coins(MEMBER_ID, 1234)
    async with await Client(http, MEMBER_ID).get("/api/me") as r:
        body = await r.json()
    assert body["rank"] == 1
    assert isinstance(body["rank"], int)


async def test_the_profile_survives_a_member_with_contribution(server, http):
    """The contribution title is derived from the rank, so the leaked tuple
    didn't just render wrong — it took the whole endpoint down for anyone who
    had ever run /squad."""
    await db.add_coins(MEMBER_ID, 500)
    await db.add_contribution(MEMBER_ID, 300)
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/me/profile"
    ) as r:
        assert r.status == 200
        body = await r.json()
    wallet = body["wallet"]
    assert isinstance(wallet["rank"], int)
    assert isinstance(wallet["contribution"]["rank"], int)
    assert isinstance(wallet["contribution"]["title"], str)


async def test_state_reads_work_on_a_brand_new_account(server, http):
    member = Client(http, MEMBER_ID)
    for path in ("fishing", "adventure", "inventory", "wallet", "shop"):
        async with await member.get(f"/api/guilds/{GUILD_ID}/{path}") as r:
            assert r.status == 200, path


async def test_the_leaderboard_marks_your_own_row(server, http):
    member = Client(http, MEMBER_ID)
    await (await member.post(f"/api/guilds/{GUILD_ID}/wallet/daily")).release()
    async with await member.get(
        f"/api/guilds/{GUILD_ID}/leaderboard/coins?scope=server"
    ) as r:
        body = await r.json()
    assert body["rows"], "the claim should have put someone on the board"
    assert body["rows"][0]["is_me"] is True
    assert body["rows"][0]["name"] == f"User {MEMBER_ID}"


# ══════════════════════════════════════════════════════════════════════════════
#  Settings validation
# ══════════════════════════════════════════════════════════════════════════════
async def test_settings_validate_server_side(server, http):
    """A client-side check is a courtesy to the user, never a guarantee about
    the database."""
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/leveling", {"xp_min": 99, "xp_max": 5}
    ) as r:
        assert r.status == 400
        body = await r.json()
    assert "minimum" in body["error"]["message"].lower()


async def test_a_bad_channel_is_refused_with_a_reason(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/logging", {"channel_id": 999999}
    ) as r:
        assert r.status == 400
        body = await r.json()
    assert "isn't in this server" in body["error"]["message"]


async def test_logging_refuses_to_switch_on_without_a_channel(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/logging", {"enabled": True}
    ) as r:
        assert r.status == 400


async def test_a_greeting_cannot_be_enabled_with_nowhere_to_go(server, http):
    """The single most common 'it doesn't work' report, refused at the source."""
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/greeting/welcome", {"enabled": True}
    ) as r:
        assert r.status == 400
        body = await r.json()
    assert "channel" in body["error"]["message"].lower()


async def test_a_greeting_saves_and_reads_back(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/greeting/welcome",
        {"channel_id": 10, "content": "Hi {user}", "enabled": True},
    ) as r:
        assert r.status == 200
    async with await admin.get(
        f"/api/guilds/{GUILD_ID}/settings/greeting/welcome"
    ) as r:
        body = await r.json()
    assert body["config"]["content"] == "Hi {user}"
    assert body["config"]["enabled"] is True


async def test_a_dangerous_regex_is_refused_before_it_reaches_live_chat(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/automod/lists/regex",
        {"value": "(a+)+$", "action": "add"},
    ) as r:
        assert r.status == 400
        body = await r.json()
    assert body["error"]["hint"] == "catastrophic-backtracking"


async def test_an_invalid_regex_is_refused(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/automod/lists/regex",
        {"value": "([unclosed", "action": "add"},
    ) as r:
        assert r.status == 400


async def test_a_word_list_round_trips(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/automod/lists/badwords",
        {"value": "frobnicate", "action": "add"},
    ) as r:
        body = await r.json()
    assert "frobnicate" in body["values"]
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/settings/automod/lists/badwords",
        {"value": "frobnicate", "action": "remove"},
    ) as r:
        body = await r.json()
    assert "frobnicate" not in body["values"]


async def test_bot_wide_settings_are_shown_read_only(server, http):
    """Shown, because hiding them makes the dashboard look incomplete; read-only
    and labelled, because they mint into a global wallet."""
    async with await Client(http, ADMIN_ID).get(
        f"/api/guilds/{GUILD_ID}/settings/economy"
    ) as r:
        body = await r.json()
    assert body["bot_wide"]["editable"] is False
    assert body["bot_wide"]["why"]


async def test_a_malformed_body_is_a_400_not_a_500(server, http):
    admin = Client(http, ADMIN_ID)
    async with http.patch(
        f"{BASE}/api/guilds/{GUILD_ID}/settings/leveling",
        data="not json",
        headers={"Content-Type": "application/json", security.CSRF_HEADER: admin.token},
        cookies=admin.cookies,
    ) as r:
        assert r.status == 400


# ══════════════════════════════════════════════════════════════════════════════
#  Self-role panels
# ══════════════════════════════════════════════════════════════════════════════
async def test_panels_are_admin_only(server, http):
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/roles/panels"
    ) as r:
        assert r.status == 403


async def test_a_panel_can_be_created_and_filled(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels",
        {"title": "Colours", "mode": "single"},
    ) as r:
        assert r.status == 200
        body = await r.json()
    panel = body["panels"][0]
    assert panel["title"] == "Colours"
    assert panel["mode"] == "single"
    assert panel["posted"] is False

    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels/{panel['id']}/entries",
        {"role_id": 901, "label": "Red", "emoji": "🔴", "style": "danger"},
    ) as r:
        assert r.status == 200
        body = await r.json()
    entry = body["panels"][0]["entries"][0]
    assert entry["label"] == "Red"
    assert entry["assignable"] is True


async def test_a_panel_refuses_a_role_the_bot_cannot_assign(server, http):
    """A panel offering an unassignable role is a button that errors for every
    member who presses it — refused at the one moment somebody is looking."""
    admin = Client(http, ADMIN_ID)
    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels", {"title": "P"}
    ) as r:
        panel = (await r.json())["panels"][0]
    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels/{panel['id']}/entries",
        {"role_id": 905},  # the bot's own top role, position 5
    ) as r:
        assert r.status == 400
        body = await r.json()
    assert "highest role" in body["error"]["message"]


async def test_dragging_saves_the_whole_order(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels", {"title": "P"}
    ) as r:
        panel = (await r.json())["panels"][0]
    for role_id in (901, 902, 903):
        server.bot.guild.roles.append(FakeRole(f"r{role_id}", position=role_id - 900))
        await (
            await admin.post(
                f"/api/guilds/{GUILD_ID}/roles/panels/{panel['id']}/entries",
                {"role_id": role_id},
            )
        ).release()

    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/roles/panels/{panel['id']}/order",
        {"order": ["903", "901", "902"]},
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert [e["role_id"] for e in body["panels"][0]["entries"]] == ["903", "901", "902"]


async def test_a_panel_from_another_server_is_not_reachable(server, http):
    """Panel ids are short and global, so the guild check is load-bearing."""
    await db.create_role_panel("elsewhere", 999999, "Theirs", None, "toggle")
    admin = Client(http, ADMIN_ID)
    async with await admin.patch(
        f"/api/guilds/{GUILD_ID}/roles/panels/elsewhere", {"title": "Mine now"}
    ) as r:
        assert r.status == 404


async def test_an_empty_panel_cannot_be_posted(server, http):
    admin = Client(http, ADMIN_ID)
    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels", {"title": "Empty"}
    ) as r:
        panel = (await r.json())["panels"][0]
    async with await admin.post(
        f"/api/guilds/{GUILD_ID}/roles/panels/{panel['id']}/publish", {"channel_id": 10}
    ) as r:
        assert r.status == 409


# ══════════════════════════════════════════════════════════════════════════════
#  Static serving
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_deep_link_serves_the_app_shell(server, http):
    """`/g/123/fishing` has to survive a reload, or the router's real paths are
    a lie."""
    async with http.get(f"{BASE}/g/{GUILD_ID}/fishing") as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/html")
        assert '<div id="root">' in await r.text()


async def test_static_assets_are_served_with_their_own_type(server, http):
    async with http.get(f"{BASE}/assets/css/app.css") as r:
        assert r.status == 200
        assert "text/css" in r.headers["Content-Type"]


async def test_the_static_route_cannot_escape_its_directory(server, http):
    """normpath collapses the `..`, and the prefix check is what stops the
    collapsed path from pointing outside the static directory."""
    for attempt in (
        "/assets/../../main.py",
        "/..%2f..%2fmain.py",
        "/%2e%2e/%2e%2e/config.ini",
    ):
        async with http.get(f"{BASE}{attempt}") as r:
            text = await r.text()
        assert "DISCORD_TOKEN" not in text
        assert "class NanoBot" not in text


async def test_an_unknown_api_route_is_json_not_html(server, http):
    async with await Client(http, MEMBER_ID).get("/api/nope") as r:
        assert r.status == 404
        assert r.headers["Content-Type"].startswith("application/json")


async def test_security_headers_are_set_on_the_app_shell(server, http):
    async with http.get(f"{BASE}/") as r:
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        csp = r.headers["Content-Security-Policy"]
    # No inline script and no external script host — the reason the frontend
    # ships as plain ES modules rather than a bundle with a boot snippet.
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]
    assert "frame-ancestors 'none'" in csp


async def test_the_csp_still_allows_the_shell_its_own_base_tag(server, http):
    """`base-uri 'none'` breaks every deep link, silently and completely.

    index.html carries a `<base href>` (rewritten at deploy time for subpath
    hosting) and every asset URL is relative to it. Forbidding the tag outright
    means a reload on /g/123/fishing resolves `assets/css/app.css` against
    /g/123/, gets the SPA fallback's HTML back instead of CSS and JS, and the
    app never boots — which reads to a user as "the site is broken", because it
    is. 'self' still stops anyone pointing the base at another origin, which is
    the whole purpose of the directive.
    """
    async with http.get(f"{BASE}/") as r:
        csp = r.headers["Content-Security-Policy"]
        shell = await r.text()
    assert "base-uri 'none'" not in csp
    assert "base-uri 'self'" in csp
    # The directive only matters because the shell really does carry the tag.
    assert "<base href=" in shell


async def test_a_deep_link_serves_the_shell_and_its_assets(server, http):
    """The reload case end to end: the app shell at a nested path, and the
    stylesheet still reachable at the root-relative URL the base tag produces."""
    async with http.get(f"{BASE}/g/{GUILD_ID}/fishing") as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/html")
    async with http.get(f"{BASE}/assets/js/app.js") as r:
        assert r.status == 200
        assert "html" not in r.headers["Content-Type"]


# ══════════════════════════════════════════════════════════════════════════════
#  Cross-origin hosting
#
#  The GitHub Pages shape: the frontend on one origin, this API on another. The
#  allow-list is what makes it safe, so what is pinned here is that it is a
#  list and not a mirror.
# ══════════════════════════════════════════════════════════════════════════════
async def test_an_unlisted_origin_gets_no_cors_headers(server, http):
    async with http.get(
        f"{BASE}/api/me", headers={"Origin": "https://evil.example"}
    ) as r:
        assert "Access-Control-Allow-Origin" not in r.headers


async def test_a_listed_origin_may_read_the_answer(server, http):
    server.allowed_origins = {"https://pages.example"}
    try:
        async with await Client(http, MEMBER_ID).get(
            "/api/me", headers={"Origin": "https://pages.example"}
        ) as r:
            assert r.status == 200
            assert r.headers["Access-Control-Allow-Origin"] == "https://pages.example"
            # Credentials only ever go with an exact origin, never with "*".
            assert r.headers["Access-Control-Allow-Credentials"] == "true"
            assert "Origin" in r.headers.getall("Vary")
    finally:
        server.allowed_origins = set()


async def test_a_preflight_is_answered_without_a_session(server, http):
    """A preflight asks about a request that hasn't happened, so refusing it
    for want of a session would refuse the question rather than the action."""
    server.allowed_origins = {"https://pages.example"}
    try:
        async with http.options(
            f"{BASE}/api/guilds/{GUILD_ID}/settings/leveling",
            headers={
                "Origin": "https://pages.example",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type",
            },
        ) as r:
            assert r.status == 204
            assert "PATCH" in r.headers["Access-Control-Allow-Methods"]
            assert security.CSRF_HEADER.lower() in (
                r.headers["Access-Control-Allow-Headers"].lower()
            )
    finally:
        server.allowed_origins = set()


async def test_cors_does_not_replace_the_csrf_check(server, http):
    """CORS decides who may read an answer; CSRF decides who may cause a write.
    A cross-origin write with no token is still refused."""
    server.allowed_origins = {"https://pages.example"}
    try:
        async with http.patch(
            f"{BASE}/api/guilds/{GUILD_ID}/settings/leveling",
            data=json.dumps({"enabled": False}),
            headers={
                "Content-Type": "application/json",
                "Origin": "https://pages.example",
            },
            cookies=cookie_for(ADMIN_ID),
        ) as r:
            assert r.status == 403
    finally:
        server.allowed_origins = set()


# ══════════════════════════════════════════════════════════════════════════════
#  Moderation
#
#  The framework's own decisions are unit-tested in test_web_phase2.py. What is
#  pinned here is that the routes actually *go through* it — a handler that
#  skipped `authorise` would pass every one of those unit tests.
# ══════════════════════════════════════════════════════════════════════════════
async def test_moderation_is_manager_only(server, http):
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/moderation"
    ) as r:
        assert r.status == 403


async def test_capabilities_say_what_you_and_the_bot_can_do(server, http):
    async with await Client(http, ADMIN_ID).get(
        f"/api/guilds/{GUILD_ID}/moderation"
    ) as r:
        assert r.status == 200
        body = await r.json()
    actions = {a["key"]: a for a in body["actions"]}
    assert set(actions) == {"warn", "timeout", "kick", "ban", "unban", "purge"}
    # An action nobody could run is reported with the reason rather than hidden.
    assert all("why_not" in a for a in actions.values())
    assert body["limits"]["purge_max"] == 100


async def test_a_warn_needs_a_reason(server, http):
    client = Client(http, ADMIN_ID)
    async with await client.post(
        f"/api/guilds/{GUILD_ID}/moderation/warn",
        {"member": str(MEMBER_ID), "reason": " "},
    ) as r:
        assert r.status == 400
    assert await db.get_warnings(GUILD_ID, MEMBER_ID) == []


async def test_a_warn_lands_and_reports_the_count(server, http):
    client = Client(http, ADMIN_ID)
    async with await client.post(
        f"/api/guilds/{GUILD_ID}/moderation/warn",
        {"member": str(MEMBER_ID), "reason": "Spamming in general"},
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert body["warnings"] == 1
    stored = await db.get_warnings(GUILD_ID, MEMBER_ID)
    assert len(stored) == 1
    assert stored[0]["reason"] == "Spamming in general"


async def test_you_cannot_moderate_yourself(server, http):
    async with await Client(http, ADMIN_ID).post(
        f"/api/guilds/{GUILD_ID}/moderation/warn",
        {"member": str(ADMIN_ID), "reason": "testing"},
    ) as r:
        assert r.status == 400


async def test_nobody_can_moderate_the_server_owner(server, http):
    async with await Client(http, ADMIN_ID).post(
        f"/api/guilds/{GUILD_ID}/moderation/kick",
        {"member": str(OWNER_ID), "reason": "testing"},
    ) as r:
        assert r.status == 403


async def test_a_member_without_the_permission_is_refused_the_action(server, http):
    """`require_manager` is not the gate — the action's own Discord permission
    is, which is what stops Manage Server implying Ban Members."""
    async with await Client(http, MEMBER_ID).post(
        f"/api/guilds/{GUILD_ID}/moderation/ban",
        {"member": str(ADMIN_ID), "reason": "testing"},
    ) as r:
        assert r.status == 403


async def test_a_timeout_cannot_exceed_discords_own_limit(server, http):
    async with await Client(http, ADMIN_ID).post(
        f"/api/guilds/{GUILD_ID}/moderation/timeout",
        {"member": str(MEMBER_ID), "reason": "testing", "seconds": 40 * 86400},
    ) as r:
        assert r.status == 400


async def test_a_purge_is_capped_rather_than_silently_doing_less(server, http):
    async with await Client(http, ADMIN_ID).post(
        f"/api/guilds/{GUILD_ID}/moderation/purge",
        {"channel": "10", "amount": 5000, "reason": "testing"},
    ) as r:
        assert r.status == 400


async def test_a_ban_goes_through_and_records_the_audit_reason(server, http):
    guild = server.bot.guild
    async with await Client(http, ADMIN_ID).post(
        f"/api/guilds/{GUILD_ID}/moderation/ban",
        {"member": str(MEMBER_ID), "reason": "Repeated harassment"},
    ) as r:
        assert r.status == 200
    assert guild.bans, "the ban never reached Discord"
    uid, reason, days = guild.bans[-1]
    assert uid == MEMBER_ID
    # The audit log has to say where the action came from, not just who did it.
    assert "dashboard" in reason.lower()
    assert "Repeated harassment" in reason
    # Deleting somebody's history is a separate decision, so it defaults to off.
    assert days == 0


# ══════════════════════════════════════════════════════════════════════════════
#  Analytics
# ══════════════════════════════════════════════════════════════════════════════
async def test_analytics_is_manager_only(server, http):
    async with await Client(http, MEMBER_ID).get(
        f"/api/guilds/{GUILD_ID}/analytics"
    ) as r:
        assert r.status == 403


async def test_analytics_answers_for_a_server_with_no_history(server, http):
    async with await Client(http, ADMIN_ID).get(
        f"/api/guilds/{GUILD_ID}/analytics?range=30d"
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert body["range"]["key"] == "30d"
    assert "economy" in body


# ══════════════════════════════════════════════════════════════════════════════
#  Games
# ══════════════════════════════════════════════════════════════════════════════
async def test_the_casino_floor_reads_for_a_member(server, http):
    async with await Client(http, MEMBER_ID).get(f"/api/guilds/{GUILD_ID}/casino") as r:
        assert r.status == 200
        body = await r.json()
    assert len(body["games"]) == 5


async def test_playing_needs_coins_and_says_so_plainly(server, http):
    async with await Client(http, MEMBER_ID).post(
        f"/api/guilds/{GUILD_ID}/casino/play/flip", {"bet": 100, "side": "heads"}
    ) as r:
        assert r.status == 409
        body = await r.json()
    assert body["error"]["message"]


async def test_a_game_nobody_offers_is_a_404(server, http):
    async with await Client(http, MEMBER_ID).post(
        f"/api/guilds/{GUILD_ID}/casino/play/baccarat", {"bet": 10}
    ) as r:
        assert r.status == 404


async def test_play_routes_are_off_when_play_is_disabled(server, http):
    server.play_enabled = False
    try:
        await db.add_coins(MEMBER_ID, 1000)
        async with await Client(http, MEMBER_ID).post(
            f"/api/guilds/{GUILD_ID}/casino/play/flip", {"bet": 10, "side": "heads"}
        ) as r:
            assert r.status == 403
        # Reading is still fine — the setting makes the dashboard read-only, it
        # doesn't hide the feature.
        async with await Client(http, MEMBER_ID).get(
            f"/api/guilds/{GUILD_ID}/casino"
        ) as r:
            assert r.status == 200
    finally:
        server.play_enabled = True


async def test_crafting_and_progression_and_the_wardrobe_read(server, http):
    client = Client(http, MEMBER_ID)
    for path in ("crafting", "progression", "wardrobe"):
        async with await client.get(f"/api/guilds/{GUILD_ID}/{path}") as r:
            assert r.status == 200, path


async def test_you_cannot_wear_a_cosmetic_you_do_not_own(server, http):
    async with await Client(http, MEMBER_ID).post(
        f"/api/guilds/{GUILD_ID}/wardrobe/equip",
        {"loadout": {"banner": ["banner_prestige"]}},
    ) as r:
        assert r.status == 200
        body = await r.json()
    assert body["changed"] == []
    assert body["refused"]


# ══════════════════════════════════════════════════════════════════════════════
#  The four setup-heavy feature pages
# ══════════════════════════════════════════════════════════════════════════════
async def test_module_settings_read_and_write(server, http):
    client = Client(http, ADMIN_ID)
    for module in ("tickets", "birthdays", "gatekeeper", "music"):
        async with await client.get(f"/api/guilds/{GUILD_ID}/settings/{module}") as r:
            assert r.status == 200, module


async def test_a_module_setting_is_validated_server_side(server, http):
    """A timezone the standard library doesn't know would break the announce
    loop for that server every fifteen minutes, so it never reaches the row."""
    async with await Client(http, ADMIN_ID).patch(
        f"/api/guilds/{GUILD_ID}/settings/birthdays", {"timezone": "Mars/Olympus"}
    ) as r:
        assert r.status == 400


async def test_module_settings_are_manager_only(server, http):
    async with await Client(http, MEMBER_ID).patch(
        f"/api/guilds/{GUILD_ID}/settings/tickets", {"enabled": False}
    ) as r:
        assert r.status == 403


# ══════════════════════════════════════════════════════════════════════════════
#  Split hosting: the two URLs the login flow needs
#
#  The callback has to land on the API (only the API holds the client secret,
#  so only the API can exchange the code) and the browser has to end up at the
#  app. Same-origin they are one URL, which is exactly why conflating them is
#  easy and only breaks in the deployment that has two.
# ══════════════════════════════════════════════════════════════════════════════
async def test_the_oauth_redirect_is_always_the_api_not_the_frontend(server, http):
    server.frontend_url = "https://pages.example/NanoBot"
    try:
        async with await Client(http).get(
            "/api/auth/login", allow_redirects=False
        ) as response:
            location = response.headers["Location"]
        assert "pages.example" not in location
        assert (
            f"{BASE}/api/auth/callback".replace(":", "%3A").replace("/", "%2F")
            in location
            or f"{BASE}/api/auth/callback" in location
        )
    finally:
        server.frontend_url = ""


async def test_a_finished_login_sends_the_browser_to_the_frontend(server, http):
    """Landing back on the API's own host would be a blank page: it has no app
    to show when something else is serving one."""
    server.frontend_url = "https://pages.example/NanoBot"
    try:
        async with await Client(http).get(
            "/api/auth/callback?error=access_denied", allow_redirects=False
        ) as response:
            location = response.headers["Location"]
        assert location.startswith("https://pages.example/NanoBot/login")
    finally:
        server.frontend_url = ""


async def test_same_origin_keeps_a_plain_path_redirect(server, http):
    """Nothing about the default deployment changed."""
    async with await Client(http).get(
        "/api/auth/callback?error=access_denied", allow_redirects=False
    ) as response:
        assert response.headers["Location"].startswith("/login")


# ══════════════════════════════════════════════════════════════════════════════
#  What a failed token exchange says
#
#  Discord validates the redirect URI and the client id at the *authorize* step,
#  so by the time an exchange fails it has already accepted both. Blaming the
#  redirect URL here — which is what this used to do for every failure — sends
#  the operator to re-read something that just worked.
# ══════════════════════════════════════════════════════════════════════════════
def test_a_rejected_secret_says_to_check_the_secret():
    from web import oauth

    hint = oauth._exchange_hint('{"error": "invalid_client"}')
    assert "client_secret" in hint
    assert "redirect" not in hint.lower()


def test_a_stale_code_says_to_try_again():
    from web import oauth

    assert "again" in oauth._exchange_hint('{"error": "invalid_grant"}')


def test_a_malformed_request_is_the_one_that_names_the_redirect():
    from web import oauth

    assert "redirect" in oauth._exchange_hint('{"error": "invalid_request"}').lower()


def test_an_unreadable_error_body_guesses_at_nothing():
    from web import oauth

    for body in ("", "not json", "{}", '{"error": "something_new"}'):
        hint = oauth._exchange_hint(body)
        assert "log" in hint
        assert "client_secret" not in hint
