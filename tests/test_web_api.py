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

    Named flags are derived from the value rather than hard-coded, so a test
    granting Administrator gets `manage_roles` for free, exactly as Discord's
    own object does.
    """

    _BITS = {
        "administrator": 1 << 3,
        "manage_guild": 1 << 5,
        "manage_roles": 1 << 28,
        "manage_messages": 1 << 13,
        "kick_members": 1 << 1,
        "ban_members": 1 << 2,
        "send_messages": 1 << 11,
        "embed_links": 1 << 14,
    }

    def __init__(self, value):
        self.value = value

    def __getattr__(self, name):
        bit = FakePermissions._BITS.get(name)
        if bit is None:
            raise AttributeError(name)
        value = self.__dict__.get("value", 0)
        return bool(value & (1 << 3)) or bool(value & bit)


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
    def __init__(self, uid, permissions=0, bot=False):
        self.id = uid
        self.name = f"user{uid}"
        self.display_name = f"User {uid}"
        self.bot = bot
        self.guild_permissions = FakePermissions(permissions)
        self.nick = None
        self.joined_at = None
        self.top_role = FakeRole("bot", position=5)
        self.display_avatar = type("A", (), {"url": "https://example.invalid/a.png"})()


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
            FakeMember(ADMIN_ID, permissions=(1 << 5)),  # Manage Server
            FakeMember(MEMBER_ID, permissions=0),
        ]
        self.text_channels = [FakeChannel(10, "general")]
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
            "U", (), {"name": "NanoBot", "display_avatar": type("A", (), {"url": ""})()}
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
