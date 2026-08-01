/**
 * views/overview.js
 * A server's command centre.
 *
 * Ordered by what needs attention rather than by what is interesting: anything
 * broken (a missing permission, a queue of unfulfilled rewards) sits above the
 * numbers, because a dashboard's first job is to tell you when something is
 * wrong and its second job is to tell you how things are going.
 *
 * The permission panel is the piece that earns its place. Missing permissions
 * are the most common cause of "the bot is broken", and they are invisible in
 * Discord until a command fails at an inconvenient moment.
 */

import { h } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";

export async function overview({ guildId }) {
  const data = await api.get(`/api/guilds/${guildId}/overview`);
  const { guild, permissions, economy, activity, features, bot } = data;

  return h(
    "div",
    { class: "stack" },
    attentionBlock(guildId, permissions, economy, data.can_manage),

    h(
      "div",
      { class: "grid grid--2" },
      ui.statTile("Members", fmt.compact(guild.members), {
        sub: `${fmt.num(guild.humans)} people · ${fmt.num(guild.bots)} bots`,
      }),
      ui.statTile("Wallets", fmt.compact(economy.members_with_coins), {
        sub: `holding ${economy.currency.name}s`,
      }),
      ui.statTile("Anglers", fmt.compact(activity.anglers), { sub: "have fished" }),
      ui.statTile("Jackpot", fmt.compact(economy.jackpot), {
        sub: `${economy.currency.emoji} in the casino pool`,
      })
    ),

    featuresCard(guildId, features, data.can_manage),

    h(
      "div",
      { class: "split" },
      h(
        "div",
        { class: "stack" },
        boardCard("Richest here", economy.top, economy.currency, "coins", guildId),
        boardCard("Top anglers", activity.top_anglers, economy.currency, "earned", guildId)
      ),
      h(
        "div",
        { class: "stack" },
        permissionsCard(permissions),
        healthCard(guild, bot)
      )
    )
  );
}

/* ── The things that need doing ─────────────────────────────────────────── */
function attentionBlock(guildId, permissions, economy, canManage) {
  const notes = [];
  if (permissions.problems) {
    notes.push(
      ui.banner(
        h(
          "span",
          {},
          h("strong", {}, `${fmt.plural(permissions.problems, "feature")} can't work properly`),
          h("div", { class: "small" }, "NanoBot is missing permissions they need.")
        ),
        {
          kind: "bad",
          glyph: "🔒",
          action: h("a", { class: "btn btn--sm", href: "#permissions" }, "See which"),
        }
      )
    );
  }
  if (economy.pending_rewards && canManage) {
    notes.push(
      ui.banner(
        h(
          "span",
          {},
          h("strong", {}, `${fmt.plural(economy.pending_rewards, "reward")} waiting`),
          h("div", { class: "small" }, "Members bought these and are waiting for someone to hand them over.")
        ),
        {
          kind: "warn",
          glyph: "🎁",
          action: h("a", { class: "btn btn--sm", href: `/g/${guildId}/settings/economy` }, "Fulfil"),
        }
      )
    );
  }
  if (!notes.length) {
    notes.push(ui.banner("Everything looks healthy here.", { kind: "info", glyph: "✅" }));
  }
  return h("div", { class: "stack stack--tight" }, ...notes);
}

/* ── Features ───────────────────────────────────────────────────────────── */
const FEATURE_LINKS = {
  automod: "automod",
  auditlog: "logging",
  welcome: "welcome",
  leave: "leave",
  leveling: "leveling",
  fishing: "games",
  casino: "games",
  activities: "games",
};

function featuresCard(guildId, features, canManage) {
  return ui.card(
    {},
    ui.cardHead("Features", {
      glyph: "🧩",
      sub: "What's switched on in this server",
      action: canManage
        ? h("a", { class: "btn btn--sm", href: `/g/${guildId}/settings` }, "Settings")
        : null,
    }),
    h(
      "div",
      { class: "grid grid--auto" },
      ...features.map((feature) => {
        const target = FEATURE_LINKS[feature.key];
        const inner = [
          h("span", { class: "grow small" }, feature.label),
          feature.enabled ? ui.pill("On", "ok") : ui.pill("Off"),
        ];
        return canManage && target
          ? h(
              "a",
              {
                class: "tile row",
                href: `/g/${guildId}/settings/${target}`,
                style: { textDecoration: "none", color: "inherit" },
              },
              ...inner
            )
          : h("div", { class: "tile row" }, ...inner);
      })
    )
  );
}

/* ── Permissions ────────────────────────────────────────────────────────── */
function permissionsCard(permissions) {
  const reports = permissions.reports;
  const problems = reports.filter((report) => !report.ok);
  const degraded = reports.filter((report) => report.ok && report.degraded.length);
  const healthy = reports.length - problems.length - degraded.length;

  return ui.card(
    { id: "permissions" },
    ui.cardHead("Permissions", {
      glyph: "🔐",
      sub: problems.length
        ? `${fmt.plural(problems.length, "feature")} blocked`
        : `All ${fmt.num(healthy)} healthy`,
    }),
    h(
      "div",
      { class: "stack stack--tight" },
      ...problems.map((report) => permissionRow(report, "bad")),
      ...degraded.map((report) => permissionRow(report, "warn")),
      !problems.length && !degraded.length
        ? h("p", { class: "small muted" }, "NanoBot has everything it needs here.")
        : h(
            "p",
            { class: "tiny dim" },
            "Fix these in Server Settings → Roles, or re-invite NanoBot with the " +
              "permissions it asks for."
          )
    )
  );
}

function permissionRow(report, kind) {
  const missing = kind === "bad" ? report.missing : report.degraded;
  return h(
    "details",
    { class: "tile" },
    h(
      "summary",
      { class: "row", style: { cursor: "pointer", listStyle: "none" } },
      h("span", { class: "grow" }, report.label),
      ui.pill(missing.length === 1 ? missing[0] : `${missing.length} missing`, kind)
    ),
    h(
      "div",
      { class: "stack stack--tight", style: { marginTop: "var(--s-2)" } },
      h("p", { class: "small muted" }, report.why),
      h(
        "div",
        { class: "row row--wrap" },
        ...report.required.map((permission) =>
          ui.pill(permission.name, permission.granted ? "ok" : "bad")
        ),
        ...report.optional.map((permission) =>
          ui.pill(permission.name, permission.granted ? "ok" : "warn")
        )
      )
    )
  );
}

/* ── Health ─────────────────────────────────────────────────────────────── */
function healthCard(guild, bot) {
  return ui.card(
    {},
    ui.cardHead("Connection", { glyph: "📡" }),
    h(
      "div",
      { class: "stack stack--tight small" },
      row("Gateway", bot.latency_ms !== null ? `${bot.latency_ms} ms` : "connecting…"),
      row("Joined", bot.joined_at ? fmt.relative(bot.joined_at) : "—"),
      row("Highest role", bot.top_role ? bot.top_role.name : "—"),
      row("Server created", fmt.date(guild.created_at)),
      guild.boosts ? row("Boosts", `${fmt.num(guild.boosts)} · tier ${guild.tier}`) : null
    )
  );
}

const row = (label, value) =>
  h("div", { class: "row row--between" }, h("span", { class: "muted" }, label), h("span", { class: "tabular" }, value));

/* ── Small leaderboards ─────────────────────────────────────────────────── */
function boardCard(title, rows, currency, key, guildId) {
  return ui.card(
    {},
    ui.cardHead(title, {
      glyph: "🏅",
      action: h("a", { class: "btn btn--sm btn--ghost", href: `/g/${guildId}/leaderboards` }, "All"),
    }),
    rows.length
      ? h(
          "div",
          { class: "stack stack--tight" },
          ...rows.map((entry, index) =>
            h(
              "div",
              { class: "rank" },
              h("span", { class: `rank__pos rank__pos--${index + 1}` }, `#${index + 1}`),
              h("span", { class: "grow small" }, entry.name || `User ${entry.user_id}`),
              h("span", { class: "rank__value" }, `${currency.emoji} ${fmt.compact(entry[key] || 0)}`)
            )
          )
        )
      : h("p", { class: "small dim" }, "Nobody's on this board yet.")
  );
}
