/**
 * views/more.js
 * The phone's fifth tab: everything the bottom bar couldn't hold.
 *
 * A bottom bar takes five destinations before the targets get too narrow to
 * hit, and the dashboard has eleven. The usual answers are both bad: hiding the
 * rest behind a hamburger nobody opens, or shrinking the bar until every tap is
 * a gamble. So the fifth slot is a real page — grouped, labelled, one tap from
 * anywhere, and identical in content to the side rail a wide screen shows.
 *
 * It reads its list from the same `NAV` the bar and the rail read, so a
 * destination cannot exist in one navigation and not another. That was the rule
 * from the start and this page is what keeps it true now there are eleven of
 * them.
 */

import { h } from "../core/dom.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import { NAV } from "../app.js";

export async function more({ guildId }) {
  const managed = Boolean(store.state.guild?.managed);
  const sections = NAV.map((section) => ({
    ...section,
    items: section.items.filter((item) => !item.manager || managed),
  })).filter((section) => section.items.length);

  return h(
    "div",
    { class: "stack" },
    h(
      "div",
      {},
      h("h1", {}, "Everything"),
      h(
        "div",
        { class: "small dim" },
        store.state.guild?.name || "This server"
      )
    ),

    ...sections.map((section) =>
      h(
        "section",
        { class: "stack stack--tight" },
        h("div", { class: "section__head" }, h("h2", {}, section.group)),
        h(
          "div",
          { class: "grid grid--wide" },
          ...section.items.map((item) =>
            h(
              "a",
              {
                class: "card row",
                href: `/g/${guildId}${item.path}`,
                style: { textDecoration: "none", color: "inherit" },
              },
              h("span", { class: "icon-tile" }, item.glyph),
              h("span", { class: "grow" }, h("strong", {}, item.label)),
              h("span", { class: "dim" }, "›")
            )
          )
        )
      )
    ),

    !managed
      ? ui.banner(
          "Admin pages are hidden because you don't have Manage Server here. " +
            "Everything you can play is above.",
          { kind: "info", glyph: "ℹ️" }
        )
      : null,

    h(
      "div",
      { class: "row", style: { justifyContent: "center" } },
      h("a", { class: "btn btn--ghost", href: "/" }, "Switch server")
    )
  );
}
