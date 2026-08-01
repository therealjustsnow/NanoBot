/**
 * views/crafting.js
 * The workbench.
 *
 * A recipe is a sentence — *these, in these amounts, become that* — and the
 * embed version has to write it out. Here it can be drawn: ingredients on the
 * left, an arrow, the result on the right, with any ingredient you're short of
 * outlined and carrying the number you still need. Nothing has to be read
 * twice.
 *
 * The default sort is craftable-first (the server does it), and the quantity
 * stepper is capped at what your materials actually allow, so it can't offer a
 * number the craft would then refuse. Everything else — search, category filter,
 * the "only what I can make" toggle — is local, because the whole registry
 * arrives in the one request and re-asking the server to filter eight recipes
 * would be slower than not.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";

export async function crafting({ guildId }) {
  const base = `/api/guilds/${guildId}/crafting`;
  let state = await api.get(base);
  const host = h("div", { class: "stack" });

  let search = "";
  let category = "all";
  let onlyCraftable = false;
  let made = null;

  async function refresh() {
    state = await api.get(base, { fresh: true });
    paint();
  }

  const visible = () =>
    state.recipes.filter((recipe) => {
      if (onlyCraftable && !recipe.craftable) return false;
      if (category !== "all" && recipe.output.category !== category) return false;
      if (!search) return true;
      const needle = search.toLowerCase();
      return (
        recipe.output.name.toLowerCase().includes(needle) ||
        recipe.description.toLowerCase().includes(needle) ||
        recipe.inputs.some((input) => input.name.toLowerCase().includes(needle))
      );
    });

  const listHost = h("div", { class: "stack" });

  function paint() {
    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h1", {}, "Crafting"),
          h(
            "div",
            { class: "small dim" },
            `${fmt.plural(state.craftable_now, "recipe")} you can make right now`
          )
        ),
        ui.pill(`${state.currency.emoji} ${fmt.compact(state.balance)}`, "brand")
      ),

      !store.state.playEnabled
        ? ui.banner(
            "Playing from the web is switched off on this instance. Everything still works in Discord.",
            { kind: "warn", glyph: "🔒" }
          )
        : null,

      made ? madeCard() : null,

      h(
        "div",
        { class: "stack stack--tight" },
        h("input", {
          class: "input",
          type: "search",
          placeholder: "Search recipes and materials",
          value: search,
          "aria-label": "Search recipes",
          onInput: (event) => {
            search = event.target.value;
            fill(listHost, list());
          },
        }),
        h(
          "div",
          { class: "scroller" },
          chip("All", category === "all", () => setCategory("all")),
          ...state.categories.map((key) =>
            chip(capitalise(key), category === key, () => setCategory(key))
          )
        ),
        h(
          "label",
          { class: "row small", style: { gap: "var(--s-2)" } },
          h("input", {
            type: "checkbox",
            checked: onlyCraftable,
            onChange: (event) => {
              onlyCraftable = event.target.checked;
              fill(listHost, list());
            },
          }),
          "Only what I can make now"
        )
      ),

      listHost,

      ui.banner(
        "Crafting can't mint money: every recipe's output is worth at most 1.5× " +
          "the materials it eats. What you get out of it is gear, not profit.",
        { kind: "info", glyph: "ℹ️" }
      )
    );
    fill(listHost, list());
  }

  const chip = (label, active, onClick) =>
    h("button", { class: "chip", type: "button", "aria-pressed": String(active), onClick }, label);

  function setCategory(value) {
    category = value;
    paint();
  }

  function list() {
    const rows = visible();
    if (!rows.length) {
      return ui.empty(
        onlyCraftable ? "Nothing craftable yet" : "Nothing matches",
        onlyCraftable
          ? "Mine, hunt and fish for materials — then come back."
          : "Try a different search or category.",
        { glyph: "🔨" }
      );
    }
    return h("div", { class: "stack" }, ...rows.map(recipeCard));
  }

  /* ── One recipe ───────────────────────────────────────────────────────── */
  function recipeCard(recipe) {
    const qty = h("input", {
      class: "input tabular",
      type: "number",
      min: "1",
      max: String(Math.max(1, recipe.max_craftable)),
      value: "1",
      "aria-label": `How many ${recipe.output.name} to make`,
      style: { width: "84px" },
    });

    return ui.card(
      { class: recipe.craftable ? "card--accent" : "" },
      h(
        "div",
        { class: "recipe" },
        h(
          "div",
          { class: "row row--between" },
          h(
            "div",
            { class: "row" },
            h("span", { class: "icon-tile" }, recipe.output.emoji),
            h(
              "div",
              {},
              h("strong", {}, recipe.output.name),
              h(
                "div",
                { class: "tiny dim" },
                `${capitalise(recipe.output.category)}` +
                  (recipe.output.qty > 1 ? ` · makes ${recipe.output.qty}` : "")
              )
            )
          ),
          recipe.craftable
            ? ui.pill(`${recipe.max_craftable} possible`, "ok")
            : ui.pill("Short on materials", "warn")
        ),

        recipe.description ? h("p", { class: "small muted" }, recipe.description) : null,

        h(
          "div",
          { class: "recipe__flow" },
          ...recipe.inputs.map((input) =>
            h(
              "span",
              {
                class: `ingredient${input.short ? " ingredient--short" : ""}`,
                title: input.short
                  ? `You need ${input.short} more ${input.name}`
                  : `You have ${input.have}`,
              },
              h("span", { "aria-hidden": "true" }, input.emoji),
              input.name,
              h(
                "span",
                { class: "ingredient__count" },
                input.short ? `${input.have}/${input.need}` : `×${input.need}`
              )
            )
          ),
          h("span", { class: "recipe__arrow", "aria-hidden": "true" }, "→"),
          h(
            "span",
            { class: "ingredient" },
            h("span", { "aria-hidden": "true" }, recipe.output.emoji),
            recipe.output.name,
            recipe.output.qty > 1
              ? h("span", { class: "ingredient__count" }, `×${recipe.output.qty}`)
              : null
          )
        ),

        recipe.output.value
          ? h(
              "div",
              { class: "tiny dim" },
              `Sells for ${fmt.num(recipe.output.value)} · materials worth ${fmt.num(recipe.input_value)}`
            )
          : null,

        h(
          "div",
          { class: "row" },
          recipe.max_craftable > 1 ? qty : null,
          h("span", { class: "grow" }),
          ui.actionButton(
            recipe.craftable ? "Craft" : "Missing materials",
            () => craft(recipe, Number(qty.value) || 1),
            {
              variant: recipe.craftable ? "primary" : "",
              disabled: !recipe.craftable,
              glyph: "🔨",
            }
          )
        )
      )
    );
  }

  async function craft(recipe, qty) {
    const result = await api.post(`${base}/make`, { recipe: recipe.key, qty });
    made = result;
    state.recipes = result.recipes;
    ui.ok(
      `Made ${fmt.plural(result.made, result.output.name)}.`,
      { title: `${result.output.emoji} Crafted` }
    );
    paint();
    // The inventory changed; re-read so the other recipes' shortfalls are right.
    refresh();
  }

  function madeCard() {
    return ui.card(
      { class: "card--accent" },
      ui.cardHead("Just crafted", { glyph: "🔨" }),
      h(
        "div",
        { class: "scroller" },
        ui.loot({ ...made.output, qty: made.made }, { fresh: true })
      ),
      h(
        "div",
        { class: "tiny dim", style: { marginTop: "var(--s-2)" } },
        "Used: " + made.consumed.map((c) => `${c.emoji} ${c.name} ×${c.qty}`).join(", ")
      )
    );
  }

  const capitalise = (word) => word.charAt(0).toUpperCase() + word.slice(1);

  paint();
  return host;
}
