/**
 * views/inventory.js
 * Everything a member is carrying — filter, sort, inspect, use, sell, gift.
 *
 * The Discord inventory is a grouped list because an embed is a grouped list.
 * Here the same data becomes a grid of cards, which lets an item show what it
 * *is* — its category colour, its sell value, whether it's usable and what
 * arming it would do — without the reader having to hold a legend in their head.
 *
 * The bulk sell keeps its confirmation step for exactly the reason the bot has
 * one: "sell everything" is easy to tap and impossible to undo, so it previews
 * the whole plan first and settles only on a second press.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as ui from "../core/ui.js";

const SORTS = [
  { value: "category", label: "Category" },
  { value: "value", label: "Value" },
  { value: "qty", label: "Count" },
  { value: "name", label: "A–Z" },
];

export async function inventory({ guildId }) {
  const base = `/api/guilds/${guildId}/inventory`;
  let state = await api.get(base);
  const host = h("div", { class: "stack" });

  let filter = "all";
  let sort = "category";
  let search = "";

  async function refresh() {
    state = await api.get(base, { fresh: true });
    paint();
  }

  function visible() {
    let rows = state.items;
    if (filter !== "all") rows = rows.filter((item) => item.category === filter);
    if (search) {
      const needle = search.toLowerCase();
      rows = rows.filter(
        (item) =>
          item.name.toLowerCase().includes(needle) ||
          item.description.toLowerCase().includes(needle)
      );
    }
    const sorters = {
      category: (a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name),
      value: (a, b) => b.value * b.qty - a.value * a.qty,
      qty: (a, b) => b.qty - a.qty,
      name: (a, b) => a.name.localeCompare(b.name),
    };
    return [...rows].sort(sorters[sort]);
  }

  function paint() {
    const rows = visible();
    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h1", {}, "Inventory"),
          h(
            "div",
            { class: "small dim" },
            `${fmt.plural(state.totals.items, "item")} · ${fmt.plural(state.totals.stacks, "stack")} · worth ${state.currency.emoji} ${fmt.num(state.totals.value)}`
          )
        )
      ),

      state.effects.length ? effectsCard(state.effects) : null,

      h(
        "div",
        { class: "stack stack--tight" },
        h("input", {
          class: "input",
          type: "search",
          placeholder: "Search your items",
          value: search,
          "aria-label": "Search items",
          oninput: (event) => {
            search = event.target.value;
            fill(gridHost, grid(visible()));
          },
        }),
        h(
          "div",
          { class: "scroller" },
          chip("All", filter === "all", () => setFilter("all")),
          ...state.categories.map((category) =>
            chip(capitalise(category), filter === category, () => setFilter(category))
          )
        ),
        h(
          "div",
          { class: "row row--between" },
          ui.segmented(SORTS, sort, (value) => { sort = value; fill(gridHost, grid(visible())); }, { label: "Sort" }),
          state.totals.value > 0
            ? ui.actionButton("Sell all", () => confirmBulk("all"), { size: "sm", glyph: "💰" })
            : null
        )
      ),

      gridHost
    );
    fill(gridHost, grid(rows));
  }

  const gridHost = h("div", {});

  function setFilter(value) {
    filter = value;
    paint();
  }

  const chip = (label, active, onClick) =>
    h("button", { class: "chip", type: "button", "aria-pressed": String(active), onClick }, label);

  function grid(rows) {
    if (!rows.length) {
      return ui.empty(
        state.items.length ? "Nothing matches" : "Your bags are empty",
        state.items.length
          ? "Try a different category or search."
          : "Mine, hunt and explore turn up items — and the tackle shop sells bait.",
        { glyph: "🎒" }
      );
    }
    return h("div", { class: "grid grid--auto" }, ...rows.map(itemCard));
  }

  function itemCard(item) {
    return h(
      "button",
      {
        class: "tile stack stack--tight",
        type: "button",
        style: { textAlign: "left", cursor: "pointer" },
        onClick: () => openItem(item),
      },
      h(
        "div",
        { class: "row" },
        h("span", { class: "icon-tile" }, item.emoji),
        h(
          "div",
          { class: "grow", style: { minWidth: 0 } },
          h("div", { style: { fontWeight: 600 } }, item.name),
          h("div", { class: "tiny dim" }, `${capitalise(item.category)} · ×${fmt.num(item.qty)}`)
        )
      ),
      h(
        "div",
        { class: "row row--between tiny" },
        item.usable
          ? ui.pill("Usable", "brand")
          : item.container
            ? ui.pill("Opens", "brand")
            : h("span", {}),
        item.value
          ? h("span", { class: "tabular muted" }, `${state.currency.emoji} ${fmt.compact(item.value * item.qty)}`)
          : h("span", { class: "dim" }, "no sale value")
      )
    );
  }

  /* ── One item ─────────────────────────────────────────────────────────── */
  function openItem(item) {
    const qty = h("input", {
      class: "input",
      type: "number",
      min: "1",
      max: String(item.qty),
      value: "1",
      "aria-label": "How many",
    });

    const actions = [];
    if (item.container) {
      const keyItem = state.items.find((row) => row.key === item.container.opens_with);
      const keys = keyItem ? keyItem.qty : 0;
      actions.push(
        ui.actionButton(
          keys ? item.container.verb : "No key",
          async () => {
            const result = await api.post(`${base}/open`, {
              item: item.key,
              qty: Number(qty.value) || 1,
            });
            view.close();
            ui.ok(
              `${fmt.plural(result.qty, "chest")} opened — ${fmt.coins(result.coins, state.currency)} inside.`,
              { title: `${item.emoji} Opened` }
            );
            await refresh();
          },
          { variant: keys ? "primary" : "", glyph: "🗝️", disabled: !keys }
        )
      );
    }
    if (item.usable) {
      actions.push(
        ui.actionButton(
          "Use",
          async () => {
            const result = await api.post(`${base}/use`, { item: item.key, qty: Number(qty.value) || 1 });
            view.close();
            ui.ok(useMessage(result), { title: `${item.emoji} ${item.name}` });
            await refresh();
          },
          { variant: "primary", glyph: "⚡" }
        )
      );
    }
    if (item.value > 0) {
      actions.push(
        ui.actionButton(
          "Sell",
          async () => {
            const result = await api.post(`${base}/sell`, { item: item.key, qty: Number(qty.value) || 1 });
            view.close();
            ui.ok(`Sold ${fmt.plural(result.qty, item.name)} for ${fmt.coins(result.coins, state.currency)}.`);
            await refresh();
          },
          { glyph: "💰" }
        )
      );
    }
    actions.push(ui.actionButton("Gift", () => { view.close(); openGift(item); }, { glyph: "🎁" }));

    const view = ui.sheet(
      `${item.emoji}  ${item.name}`,
      h(
        "div",
        { class: "stack" },
        h("p", { class: "muted" }, item.description || "No description."),
        h(
          "div",
          { class: "grid grid--2" },
          ui.statTile("You have", fmt.num(item.qty)),
          ui.statTile("Sells for", item.value ? `${state.currency.emoji} ${fmt.num(item.value)}` : "—", {
            sub: item.value ? "each" : "can't be sold",
          })
        ),
        item.sources && item.sources.length
          ? h(
              "div",
              { class: "row tiny dim", style: { flexWrap: "wrap" } },
              h("span", {}, "Comes from"),
              ...item.sources.map((source) => ui.pill(source))
            )
          : null,
        item.effect ? effectExplainer(item.effect) : null,
        item.container ? containerExplainer(item) : null,
        item.qty > 1 ? ui.field("How many", qty) : qty
      ),
      { actions }
    );
    if (item.qty <= 1) qty.style.display = "none";
  }

  /**
   * What opening this takes, said before the tap rather than after it.
   *
   * In Discord you find out a chest needs a key by trying to open one without
   * it. A screen can hold both numbers at once, so it does.
   */
  function containerExplainer(item) {
    const needed = state.items.find((row) => row.key === item.container.opens_with);
    const have = needed ? needed.qty : 0;
    const name = needed ? needed.name : "a key";
    return ui.banner(
      have
        ? `Each one takes a ${name}, and you have ${fmt.num(have)}.`
        : `Each one takes a ${name}, and you don't have any yet. ` +
          "Mining and exploring turn them up.",
      { kind: have ? "info" : "warn", glyph: "🗝️" }
    );
  }

  function effectExplainer(effect) {
    const what = {
      fish_bait: "adds fishing luck",
      fish_xp: "multiplies fishing XP",
      fish_net: "pulls several fish per cast",
      luck: "adds luck to everything that rolls",
      rob_shield: "blocks anyone from robbing you",
      coin_boost: "multiplies coin rewards",
    }[effect.key] || `applies ${effect.key}`;
    const how = effect.duration
      ? `for ${fmt.duration(effect.duration)}`
      : effect.uses
        ? `for ${fmt.plural(effect.uses, "use")}`
        : "";
    return ui.banner(`Using this ${what} ${how}.`, { kind: "info", glyph: "⚡" });
  }

  const useMessage = (result) =>
    result.uses
      ? `Armed for ${fmt.plural(result.uses, "use")}.`
      : result.duration
        ? `Active for ${fmt.duration(result.duration)}.`
        : "Used.";

  /* ── Gift ─────────────────────────────────────────────────────────────── */
  async function openGift(item) {
    const { members } = await api.get(`/api/guilds/${guildId}/members`);
    const qty = h("input", { class: "input", type: "number", min: "1", max: String(item.qty), value: "1" });
    const list = h("div", { class: "stack stack--tight" });

    const view = ui.sheet(
      `Give ${item.name}`,
      h(
        "div",
        { class: "stack" },
        item.qty > 1 ? ui.field("How many", qty) : null,
        h("div", { class: "small dim" }, "Items are part of the account, so this follows them everywhere."),
        list
      )
    );

    fill(
      list,
      ...members.map((member) =>
        h(
          "div",
          { class: "row" },
          h("img", { class: "avatar avatar--sm", src: member.avatar, alt: "", loading: "lazy" }),
          h("span", { class: "grow" }, member.name),
          ui.actionButton(
            "Give",
            async () => {
              await api.post(`${base}/gift`, {
                member: member.id,
                item: item.key,
                qty: Number(qty.value) || 1,
              });
              view.close();
              ui.ok(`Sent ${item.name} to ${member.name}.`);
              await refresh();
            },
            { size: "sm" }
          )
        )
      )
    );
  }

  /* ── Bulk sell ────────────────────────────────────────────────────────── */
  async function confirmBulk(target) {
    const sellable = state.items.filter((item) => item.value > 0 && (target === "all" || item.category === target));
    const total = sellable.reduce((sum, item) => sum + item.value * item.qty, 0);
    if (!sellable.length) {
      ui.warn("Nothing there is worth selling.");
      return;
    }
    const yes = await ui.confirm(
      "Sell everything sellable?",
      `${fmt.plural(sellable.length, "stack")} for about ${fmt.coins(total, state.currency)}. ` +
        "Items with no sale value are left alone. This can't be undone.",
      { danger: true, confirmLabel: "Sell them" }
    );
    if (!yes) return;
    const result = await api.post(`${base}/sell`, { item: target, bulk: true });
    ui.ok(`Sold ${fmt.plural(result.sold.length, "stack")} for ${fmt.coins(result.coins, state.currency)}.`);
    await refresh();
  }

  /* ── Active effects ───────────────────────────────────────────────────── */
  function effectsCard(effects) {
    return ui.card(
      { class: "card--accent" },
      ui.cardHead("Active right now", { glyph: "⚡" }),
      h(
        "div",
        { class: "stack stack--tight" },
        ...effects.map((effect) =>
          h(
            "div",
            { class: "row row--between small" },
            h("strong", {}, effect.key.replace(/_/g, " ")),
            h(
              "span",
              { class: "dim tabular" },
              effect.uses_left
                ? `${fmt.plural(effect.uses_left, "use")} left`
                : effect.expires_in
                  ? `${fmt.duration(effect.expires_in)} left`
                  : "active"
            )
          )
        )
      )
    );
  }

  const capitalise = (word) => word.charAt(0).toUpperCase() + word.slice(1);

  paint();
  return host;
}
