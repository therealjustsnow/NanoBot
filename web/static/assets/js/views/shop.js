/**
 * views/shop.js
 * This server's rewards, priced in coins and in time.
 *
 * The time-to-earn line is the part worth having. A price in coins is only
 * meaningful next to how long earning that takes, and the bot already computes
 * it from its own balance model — so the shop can say "about 3h of fishing"
 * instead of leaving a number to be guessed at. Both figures come from the
 * server; nothing here re-derives them.
 *
 * Buying goes straight through `db.purchase_item`, so the funds check, the
 * stock decrement, the per-user limit and the cooldown are the same ones
 * Discord enforces — including the compensating writes that make a sold-out
 * race safe.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as ui from "../core/ui.js";

export async function shop({ guildId }) {
  const base = `/api/guilds/${guildId}/shop`;
  let state = await api.get(base);
  const host = h("div", { class: "stack" });

  async function refresh() {
    state = await api.get(base, { fresh: true });
    paint();
  }

  function paint() {
    const affordable = state.items.filter((item) => item.affordable && item.enabled !== false);
    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h1", {}, "Shop"),
          h("div", { class: "small dim" }, "Rewards this server offers")
        ),
        ui.pill(`${state.currency.emoji} ${fmt.num(state.balance)}`, "brand")
      ),

      !state.items.length
        ? ui.empty(
            "Nothing on the shelves yet",
            "An admin can add rewards in Settings → Economy, or seed a starter set with /shop seed in Discord.",
            { glyph: "🛒" }
          )
        : h(
            "div",
            { class: "stack" },
            affordable.length
              ? ui.banner(
                  `You can afford ${fmt.plural(affordable.length, "reward")} right now.`,
                  { kind: "info", glyph: "✨" }
                )
              : null,
            h("div", { class: "grid grid--wide" }, ...state.items.map(itemCard))
          ),

      ui.banner(
        "Shop prices belong to this server — a purchase only ever spends coins here. " +
          "Your wallet itself follows you everywhere.",
        { kind: "info", glyph: "ℹ️" }
      )
    );
  }

  function itemCard(item) {
    const soldOut = item.stock === 0;
    const limited = item.per_user_limit > 0;
    const maxedOut = limited && item.purchased >= item.per_user_limit;

    return ui.card(
      { class: item.affordable && !soldOut && !maxedOut ? "card--accent" : "" },
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "row row--between" },
          h(
            "div",
            { class: "grow" },
            h("strong", {}, item.name),
            h(
              "div",
              { class: "tiny dim" },
              item.kind === "role" ? "Grants a role instantly" : "A mod hands this over"
            )
          ),
          item.kind === "role" ? h("span", { class: "icon-tile" }, "🎭") : h("span", { class: "icon-tile" }, "🎁")
        ),
        item.description ? h("p", { class: "small muted" }, item.description) : null,

        h(
          "div",
          { class: "row row--wrap tiny" },
          item.stock >= 0 ? ui.pill(soldOut ? "Sold out" : `${fmt.num(item.stock)} left`, soldOut ? "bad" : null) : null,
          limited ? ui.pill(`${item.purchased}/${item.per_user_limit} bought`, maxedOut ? "warn" : null) : null,
          item.cooldown ? ui.pill(`One every ${fmt.duration(item.cooldown)}`) : null
        ),

        h(
          "div",
          { class: "row row--between", style: { marginTop: "var(--s-2)" } },
          h(
            "div",
            {},
            h("strong", { class: "tabular" }, `${state.currency.emoji} ${fmt.num(item.price)}`),
            h("div", { class: "tiny dim" }, fmt.timeToEarn(item.seconds_to_afford))
          ),
          ui.actionButton(
            soldOut ? "Sold out" : maxedOut ? "Limit reached" : "Buy",
            () => buy(item),
            {
              variant: item.affordable && !soldOut && !maxedOut ? "primary" : "",
              disabled: soldOut || maxedOut || !item.affordable,
              size: "sm",
            }
          )
        ),
        !item.affordable && !soldOut && !maxedOut
          ? h("div", { class: "tiny dim" }, `${fmt.num(item.price - state.balance)} more to go.`)
          : null
      )
    );
  }

  async function buy(item) {
    const yes = await ui.confirm(
      `Buy ${item.name}?`,
      `This spends ${fmt.coins(item.price, state.currency)} from your wallet.` +
        (item.kind === "custom" ? " A moderator will hand it over when they see it." : ""),
      { confirmLabel: "Buy it" }
    );
    if (!yes) return;
    await api.post(`${base}/${item.id}/buy`);
    ui.ok(
      item.kind === "role"
        ? `${item.name} is yours — the role has been added.`
        : `${item.name} bought. A moderator will sort it out shortly.`,
      { title: "Purchased" }
    );
    await refresh();
  }

  paint();
  return host;
}
