/**
 * views/home.js
 * The server picker, with the account it belongs to across the top.
 *
 * Sorted so the servers you can actually configure come first — that ordering
 * is done server-side in `web/permissions.manageable_guilds`, and the reason is
 * worth repeating: scrolling past forty servers you have no power in, to reach
 * the one you do, is the single most annoying thing about bot dashboards.
 *
 * Servers the bot isn't in are still listed, as an invite. Hiding them makes a
 * user think the dashboard has lost their server.
 */

import { h } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

export async function home() {
  const [guilds, me] = await Promise.all([
    api.get("/api/auth/guilds"),
    api.get("/api/me").catch(() => null),
  ]);
  store.set({ guilds: guilds.guilds });

  const managed = guilds.guilds.filter((g) => g.managed && g.present);
  const invitable = guilds.guilds.filter((g) => g.managed && !g.present);
  const member = guilds.guilds.filter((g) => !g.managed && g.present);

  return h(
    "div",
    { class: "stack" },
    me && accountStrip(me),

    !guilds.guilds.length
      ? ui.empty(
          "No servers yet",
          "You're not in any servers NanoBot can see. Add it to one to get started.",
          {
            glyph: "🗂️",
            action:
              guilds.invite &&
              h("a", { class: "btn btn--primary", href: guilds.invite, target: "_blank", rel: "noopener" }, "Add NanoBot to a server"),
          }
        )
      : null,

    section("Servers you manage", managed, {
      empty: "None yet — the servers you have Manage Server in will show up here.",
    }),
    invitable.length
      ? section("Ready to add NanoBot", invitable, { invite: guilds.invite })
      : null,
    member.length ? section("Servers you're in", member, { collapsed: true }) : null,

    h(
      "div",
      { class: "row row--between" },
      h("span", { class: "tiny dim" }, "Server list is cached for a minute."),
      h(
        "button",
        {
          class: "btn btn--ghost btn--sm",
          type: "button",
          onClick: async () => {
            await api.get("/api/auth/guilds?refresh=1", { fresh: true });
            api.clearCache();
            router.reload();
          },
        },
        "Refresh"
      )
    )
  );
}

function accountStrip(me) {
  const user = store.state.user;
  return ui.card(
    { class: "card--accent" },
    h(
      "div",
      { class: "row" },
      user && h("img", { class: "avatar avatar--lg", src: user.avatar, alt: "" }),
      h(
        "div",
        { class: "grow stack stack--tight" },
        h("h2", {}, user?.name || "Your account"),
        ui.progressBlock(`Level ${me.level}`, me.into, me.need, {
          extra: `${fmt.num(me.into)} / ${fmt.num(me.need)} XP`,
        }),
        h(
          "div",
          { class: "row row--wrap small muted" },
          h("span", {}, `🪙 ${fmt.num(me.balance)}`),
          me.rank ? h("span", {}, `#${fmt.num(me.rank)} richest`) : null,
          h("span", {}, `🏆 ${fmt.plural(me.achievements, "achievement")}`),
          me.prestige ? h("span", {}, `⭐ Prestige ${me.prestige}`) : null,
          h("span", {}, `🎒 ${fmt.plural(me.items, "item")}`)
        )
      )
    ),
    h(
      "p",
      { class: "tiny dim", style: { marginTop: "var(--s-3)", marginBottom: 0 } },
      "This is your account across every server — coins, levels and achievements " +
        "follow you. Pick a server below to play or configure it."
    )
  );
}

function section(title, guilds, { empty = null, invite = null, collapsed = false } = {}) {
  if (!guilds.length && !empty) return null;
  return h(
    "section",
    { class: "stack stack--tight" },
    h("div", { class: "section__head" }, h("h2", {}, title),
      guilds.length ? h("span", { class: "small dim" }, fmt.num(guilds.length)) : null),
    guilds.length
      ? h(
          "div",
          { class: "grid grid--wide" },
          ...guilds.map((guild) => guildCard(guild, { invite }))
        )
      : h("p", { class: "small dim" }, empty)
  );
}

function guildCard(guild, { invite = null } = {}) {
  const inner = [
    guild.icon_url
      ? h("img", { class: "avatar avatar--md", src: guild.icon_url, alt: "", loading: "lazy" })
      : h("span", { class: "icon-tile" }, guild.name[0] || "?"),
    h(
      "div",
      { class: "grow", style: { minWidth: 0 } },
      h("div", { style: { fontWeight: 620, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, guild.name),
      h(
        "div",
        { class: "tiny dim" },
        guild.present
          ? guild.members
            ? fmt.plural(guild.members, "member")
            : "Connected"
          : "NanoBot isn't here yet"
      )
    ),
    guild.owner ? ui.pill("Owner", "brand") : guild.managed ? ui.pill("Admin", "brand") : null,
  ];

  if (!guild.present) {
    const href = invite ? `${invite}&guild_id=${guild.id}` : "#";
    return h(
      "a",
      { class: "card row", href, target: "_blank", rel: "noopener", style: { textDecoration: "none", color: "inherit" } },
      ...inner,
      h("span", { class: "btn btn--sm btn--primary" }, "Add")
    );
  }
  return h(
    "a",
    { class: "card row", href: `/g/${guild.id}`, style: { textDecoration: "none", color: "inherit" } },
    ...inner
  );
}
