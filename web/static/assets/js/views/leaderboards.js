/**
 * views/leaderboards.js
 * Boards, with the This server / Global switch that every economy board offers.
 *
 * The switch isn't decoration — it is the whole shape of the economy made
 * visible. A server-scoped board is the *global* table filtered to this
 * server's members, never a separate tally, which is why flipping it can show
 * you a very different rank. The footnote says so, because that difference
 * surprises people otherwise.
 *
 * Your own row is highlighted and, when you're off the visible page, pinned to
 * the bottom — the number most people came to see is their own.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

const BOARDS = [
  { value: "coins", label: "Wealth", glyph: "🪙" },
  { value: "fishing", label: "Fishing", glyph: "🎣" },
  { value: "contribution", label: "Co-op", glyph: "🤝" },
];

const SCOPES = [
  { value: "server", label: "This server" },
  { value: "global", label: "Global" },
];

export async function leaderboards({ guildId }) {
  const host = h("div", { class: "stack" });
  let board = router.query("board", "coins");
  let scope = router.query("scope", "server");
  let page = Number(router.query("page", 1)) || 1;

  async function load() {
    const data = await api.get(
      `/api/guilds/${guildId}/leaderboard/${board}?scope=${scope}&page=${page}`
    );
    paint(data);
  }

  function paint(data) {
    const mine = data.rows.find((row) => row.is_me);
    fill(
      host,
      h("h1", {}, "Leaderboards"),
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "scroller" },
          ...BOARDS.map((entry) =>
            h(
              "button",
              {
                class: "chip",
                type: "button",
                "aria-pressed": String(entry.value === board),
                onClick: () => {
                  board = entry.value;
                  page = 1;
                  router.setQuery("board", entry.value);
                  router.setQuery("page", null);
                  load();
                },
              },
              h("span", { "aria-hidden": "true" }, entry.glyph),
              entry.label
            )
          )
        ),
        ui.segmented(SCOPES, scope, (value) => {
          scope = value;
          page = 1;
          router.setQuery("scope", value === "server" ? null : value);
          router.setQuery("page", null);
          load();
        }, { label: "Scope" })
      ),

      ui.card(
        {},
        ui.cardHead(`${data.emoji}  ${data.label}`, {
          sub: scope === "server" ? store.state.guild?.name || "This server" : "Every server",
        }),
        data.rows.length
          ? h("div", { class: "stack stack--tight" }, ...data.rows.map(rankRow))
          : ui.empty("Nobody here yet", "Play a little and this board fills up.", { glyph: "🏆" }),
        pager(data)
      ),

      !mine && page === 1
        ? h(
            "p",
            { class: "small dim center" },
            "You're not on this page yet — keep going."
          )
        : null,

      ui.banner(
        scope === "server"
          ? "This server's board is the global table filtered to members here — the " +
            "same coins, just a smaller field. Switch to Global to see everyone."
          : "Everyone who plays NanoBot, anywhere. Coins, catches and contribution " +
            "belong to the account, not to a server.",
        { kind: "info", glyph: "ℹ️" }
      )
    );
  }

  function rankRow(row) {
    return h(
      "div",
      { class: `rank${row.is_me ? " rank--me" : ""}` },
      h("span", { class: `rank__pos rank__pos--${row.rank}` }, `#${row.rank}`),
      row.avatar
        ? h("img", { class: "avatar avatar--sm", src: row.avatar, alt: "", loading: "lazy" })
        : h("span", { class: "avatar avatar--sm" }),
      h("span", { class: "grow" }, row.name, row.is_me ? h("span", { class: "dim small" }, " · you") : null),
      h("span", { class: "rank__value" }, fmt.compact(row.value))
    );
  }

  function pager(data) {
    const atStart = page <= 1;
    const atEnd = data.rows.length < 25;
    if (atStart && atEnd) return null;
    return h(
      "div",
      { class: "row row--between card__foot" },
      h(
        "button",
        {
          class: "btn btn--sm",
          type: "button",
          disabled: atStart,
          onClick: () => {
            page -= 1;
            router.setQuery("page", page > 1 ? page : null);
            load();
          },
        },
        "◀ Previous"
      ),
      h("span", { class: "small dim tabular" }, `Page ${page}`),
      h(
        "button",
        {
          class: "btn btn--sm",
          type: "button",
          disabled: atEnd,
          onClick: () => {
            page += 1;
            router.setQuery("page", page);
            load();
          },
        },
        "Next ▶"
      )
    );
  }

  await load();
  return host;
}
