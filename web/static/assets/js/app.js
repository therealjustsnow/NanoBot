/**
 * app.js
 * Boot, the app shell, and the route table.
 *
 * The shell is a header, a content area, and one navigation control that is a
 * bottom tab bar on a phone and a side rail on a wide screen. Both are rendered
 * from the same list, so a destination can never exist in one and not the other
 * — the failure that makes a "responsive" dashboard feel like two half-built
 * apps.
 *
 * Views are imported lazily. Someone opening the fishing page shouldn't be made
 * to download the automod rule builder first, and on a phone connection that
 * difference is felt.
 */

import { h, fill } from "./core/dom.js";
import * as api from "./core/api.js";
import * as router from "./core/router.js";
import * as store from "./core/store.js";
import * as ui from "./core/ui.js";

const root = document.getElementById("root");

/* ── Navigation model ───────────────────────────────────────────────────────
   One list, two renderings. `tab: true` marks the five destinations that fit a
   phone's bottom bar; the rail shows everything, grouped.
   ────────────────────────────────────────────────────────────────────────── */
const NAV = [
  { group: "Server", items: [
    { key: "overview", label: "Home", glyph: "🏠", path: "", tab: true },
    { key: "settings", label: "Settings", glyph: "⚙️", path: "/settings", tab: true, manager: true },
  ]},
  { group: "Play", items: [
    { key: "fishing", label: "Fishing", glyph: "🎣", path: "/fishing", tab: true },
    { key: "adventure", label: "Adventure", glyph: "🧭", path: "/adventure", tab: true },
    { key: "inventory", label: "Items", glyph: "🎒", path: "/inventory" },
    { key: "shop", label: "Shop", glyph: "🛒", path: "/shop" },
  ]},
  { group: "You", items: [
    { key: "profile", label: "Profile", glyph: "👤", path: "/profile", tab: true },
    { key: "leaderboards", label: "Leaderboards", glyph: "🏆", path: "/leaderboards" },
  ]},
];

const ALL_NAV = NAV.flatMap((section) => section.items);

/* ── Routes ─────────────────────────────────────────────────────────────── */
const lazy = (loader, name = "default") => async (params, ctx) => {
  const module = await loader();
  return module[name](params, ctx);
};

router.route("/login", lazy(() => import("./views/login.js"), "login"));
router.route("/", lazy(() => import("./views/home.js"), "home"));
router.route("/g/:guildId", lazy(() => import("./views/overview.js"), "overview"));
router.route("/g/:guildId/settings", lazy(() => import("./views/settings.js"), "settingsIndex"));
router.route("/g/:guildId/settings/:feature", lazy(() => import("./views/settings.js"), "settingsPage"));
router.route("/g/:guildId/fishing", lazy(() => import("./views/fishing.js"), "fishing"));
router.route("/g/:guildId/adventure", lazy(() => import("./views/adventure.js"), "adventure"));
router.route("/g/:guildId/inventory", lazy(() => import("./views/inventory.js"), "inventory"));
router.route("/g/:guildId/shop", lazy(() => import("./views/shop.js"), "shop"));
router.route("/g/:guildId/profile", lazy(() => import("./views/profile.js"), "profile"));
router.route("/g/:guildId/leaderboards", lazy(() => import("./views/leaderboards.js"), "leaderboards"));

/* ── Boot ───────────────────────────────────────────────────────────────── */
async function boot() {
  store.applyTheme();

  let session;
  try {
    session = await api.get("/api/auth/me", { fresh: true });
  } catch {
    fill(root, offlineScreen());
    return;
  }

  store.set({
    user: session.user,
    csrf: session.csrf,
    bot: session.bot,
    configured: session.configured,
    playEnabled: session.play_enabled,
    missing: session.missing || [],
  });

  // A session that dies mid-visit shouldn't strand the user on a page that has
  // quietly stopped working — bounce to login and say why, once.
  api.onUnauthorized(() => {
    if (location.pathname === "/login") return;
    store.set({ user: null });
    ui.warn("Your session expired. Sign in again to carry on.");
    router.go("/login");
  });

  if (!session.user && location.pathname !== "/login") {
    history.replaceState({}, "", "/login");
  }
  if (session.user && location.pathname === "/login") {
    history.replaceState({}, "", "/");
  }

  // Loaded once at boot rather than by whichever page happens to need it: the
  // header and the nav both read it, so a deep link straight to
  // /g/123/fishing would otherwise paint a header with no server name and hide
  // the Settings tab from someone who can use it. Cheap — the server caches
  // the guild list for a minute.
  if (session.user) {
    try {
      const { guilds } = await api.get("/api/auth/guilds");
      store.set({ guilds });
    } catch {
      // Discord not answering isn't fatal: the picker says so when it's opened,
      // and every page that names a server still works from the path.
      store.set({ guilds: [] });
    }
  }

  fill(root, shell());
  router.start(document.getElementById("view"), { onNavigate: paintChrome });
}

function offlineScreen() {
  return h(
    "div",
    { class: "login" },
    h(
      "div",
      { class: "login__card card stack" },
      h("div", { class: "login__mark" }, "📡"),
      h("h1", {}, "Can't reach NanoBot"),
      h("p", { class: "muted" }, "The bot isn't answering. It may be restarting — give it a moment."),
      h("button", { class: "btn btn--primary", onClick: () => location.reload() }, "Try again")
    )
  );
}

/* ── Shell ──────────────────────────────────────────────────────────────── */
function shell() {
  return h(
    "div",
    { class: "app" },
    h("nav", { class: "rail", id: "rail", "aria-label": "Sections" }),
    h(
      "header",
      { class: "topbar" },
      h("div", { class: "topbar__title", id: "topbar-title" }),
      h("div", { class: "row row--tight", id: "topbar-actions" })
    ),
    h("main", { class: "main", id: "view" }),
    h("nav", { class: "tabbar", id: "tabbar", "aria-label": "Main" })
  );
}

/**
 * Repaint the header and both navigations for the route just entered.
 *
 * Called by the router on every navigation rather than by each view, so a view
 * can never forget to set its own title — and so the two navigations can never
 * disagree about which destination is current.
 */
function paintChrome(current) {
  const guildId = current.params?.guildId || null;
  const guild = guildId ? store.guildById(guildId) : null;
  if (guildId) store.rememberGuild(guildId);
  store.set({ guild });

  const title = document.getElementById("topbar-title");
  const actions = document.getElementById("topbar-actions");
  const active = activeKey(current.path, guildId);

  fill(
    title,
    guild
      ? h(
          "a",
          { class: "row", href: "/", style: { color: "inherit" }, title: "Switch server" },
          guild.icon_url
            ? h("img", { class: "avatar avatar--sm", src: guild.icon_url, alt: "" })
            : h("span", { class: "icon-tile", style: { width: "28px", height: "28px", fontSize: "0.9rem" } }, guild.name[0] || "?"),
          h(
            "span",
            { class: "grow" },
            h("h1", {}, guild.name),
            h("span", { class: "topbar__crumb" }, navLabel(active) || "Dashboard")
          )
        )
      : h("h1", {}, "NanoBot")
  );

  fill(actions, themeButton(), accountButton());
  fill(document.getElementById("tabbar"), ...tabBar(guildId, active));
  fill(document.getElementById("rail"), ...rail(guildId, active));
}

const navLabel = (key) => ALL_NAV.find((item) => item.key === key)?.label || null;

function activeKey(path, guildId) {
  if (!guildId) return "home";
  const rest = path.replace(`/g/${guildId}`, "").split("?")[0].replace(/\/$/, "");
  if (!rest) return "overview";
  const item = ALL_NAV.find((entry) => entry.path && rest.startsWith(entry.path));
  return item ? item.key : "overview";
}

function visible(item) {
  // A member who can't configure the server has no reason to be shown a
  // Settings tab that would only refuse them.
  if (item.manager && !store.state.guild?.managed) return false;
  return true;
}

function tabBar(guildId, active) {
  if (!guildId) return [];
  const items = ALL_NAV.filter((item) => item.tab && visible(item));
  return items.map((item) =>
    h(
      "a",
      {
        class: "tabbar__item",
        href: `/g/${guildId}${item.path}`,
        "aria-current": item.key === active ? "page" : null,
      },
      h("span", { class: "tabbar__glyph", "aria-hidden": "true" }, item.glyph),
      h("span", {}, item.label)
    )
  );
}

function rail(guildId, active) {
  const out = [
    h(
      "a",
      { class: "rail__brand", href: "/" },
      store.state.bot?.avatar
        ? h("img", { class: "avatar avatar--md", src: store.state.bot.avatar, alt: "" })
        : h("span", { class: "icon-tile" }, "🤖"),
      h(
        "span",
        {},
        h("strong", {}, store.state.bot?.name || "NanoBot"),
        h("span", { class: "small dim", style: { display: "block" } }, "Dashboard")
      )
    ),
  ];
  if (!guildId) {
    out.push(h("a", { class: "rail__item", href: "/", "aria-current": "page" },
      h("span", { class: "rail__glyph" }, "🗂️"), "Your servers"));
  } else {
    for (const section of NAV) {
      const items = section.items.filter(visible);
      if (!items.length) continue;
      out.push(h("div", { class: "rail__group" }, section.group));
      for (const item of items) {
        out.push(
          h(
            "a",
            {
              class: "rail__item",
              href: `/g/${guildId}${item.path}`,
              "aria-current": item.key === active ? "page" : null,
            },
            h("span", { class: "rail__glyph", "aria-hidden": "true" }, item.glyph),
            item.label
          )
        );
      }
    }
    out.push(
      h("div", { class: "rail__foot" },
        h("a", { class: "rail__item", href: "/" },
          h("span", { class: "rail__glyph" }, "🔄"), "Switch server"))
    );
  }
  return out;
}

/* ── Header controls ────────────────────────────────────────────────────── */
function themeButton() {
  const glyphs = { system: "🌗", dark: "🌙", light: "☀️" };
  const order = ["system", "dark", "light"];
  const button = h(
    "button",
    {
      class: "btn btn--ghost btn--sm",
      type: "button",
      title: `Theme: ${store.theme()}`,
      "aria-label": `Theme: ${store.theme()}. Change theme`,
      onClick: () => {
        const next = order[(order.indexOf(store.theme()) + 1) % order.length];
        store.setTheme(next);
        button.textContent = glyphs[next];
        button.title = `Theme: ${next}`;
        button.setAttribute("aria-label", `Theme: ${next}. Change theme`);
      },
    },
    glyphs[store.theme()]
  );
  return button;
}

function accountButton() {
  const user = store.state.user;
  if (!user) return h("a", { class: "btn btn--sm btn--primary", href: "/login" }, "Sign in");
  return h(
    "button",
    {
      class: "btn btn--ghost btn--sm",
      type: "button",
      "aria-label": `Account: ${user.name}`,
      onClick: () => accountSheet(user),
    },
    h("img", { class: "avatar avatar--sm", src: user.avatar, alt: "" })
  );
}

function accountSheet(user) {
  ui.sheet(
    "Your account",
    h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "row" },
        h("img", { class: "avatar avatar--lg", src: user.avatar, alt: "" }),
        h("div", {}, h("strong", {}, user.name), h("div", { class: "small dim" }, `@${user.handle}`))
      ),
      h(
        "div",
        { class: "stack stack--tight" },
        h("div", { class: "small muted" }, "Theme"),
        ui.segmented(
          [
            { value: "system", label: "System" },
            { value: "dark", label: "Dark" },
            { value: "light", label: "Light" },
          ],
          store.theme(),
          (value) => {
            store.setTheme(value);
            location.reload();
          },
          { label: "Theme" }
        )
      ),
      user.bot_owner &&
        ui.banner(
          "You're the bot owner. Bot-wide settings — reward amounts and activity cooldowns — are owner-only and live on the prefix commands !econ and !cooldown.",
          { kind: "info", glyph: "🔑" }
        )
    ),
    {
      actions: [
        h(
          "button",
          {
            class: "btn btn--danger btn--block",
            type: "button",
            onClick: async () => {
              await api.post("/api/auth/logout");
              location.href = "/login";
            },
          },
          "Sign out"
        ),
      ],
    }
  );
}

boot();
