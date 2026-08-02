/**
 * views/wardrobe.js
 * Cosmetics, with the card you're dressing right there while you dress it.
 *
 * This is the page that most obviously justifies a screen over a command.
 * `/profile equip` already takes several cosmetics at once, but you have to
 * know what you want before you type it — and what a banner *looks like with
 * your name and your stats on top* is the entire thing being chosen.
 *
 * So the model here is staging, not applying:
 *
 *   1. Tap cosmetics. Nothing is written; a "staged" bar appears saying how
 *      many slots changed.
 *   2. The card above re-renders as a preview — the real renderer, through
 *      `/profile preview`, so it is the actual image and not an approximation.
 *   3. Apply writes every changed slot in one request; Discard throws it away.
 *
 * Badges are a multi-select up to the slot's own limit, which is what makes the
 * staged model earn its keep: picking six badges is one apply, not six.
 *
 * Locked cosmetics stay listed with their unlock line — the typing picker in
 * Discord does the same, and a wardrobe that hides what you haven't got answers
 * no questions.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

export async function wardrobe({ guildId }) {
  const base = `/api/guilds/${guildId}/wardrobe`;
  let state = await api.get(base);
  const host = h("div", { class: "stack" });

  const clone = (obj) =>
    Object.fromEntries(Object.entries(obj || {}).map(([k, v]) => [k, [...v]]));

  let slot = router.query("slot", state.slots[0]?.key || "banner");
  let showLocked = true;
  // The staged loadout: a copy of what's equipped, edited freely, written only
  // when Apply is pressed.
  let staged = clone(state.equipped);

  const dirty = () => changedSlots().length > 0;

  function changedSlots() {
    const keys = new Set([...Object.keys(staged), ...Object.keys(state.equipped)]);
    return [...keys].filter(
      (key) =>
        (staged[key] || []).join(",") !== (state.equipped[key] || []).join(",")
    );
  }

  async function refresh() {
    state = await api.get(base, { fresh: true });
    staged = clone(state.equipped);
    paint();
  }

  function paint() {
    const slotSpec = state.slots.find((s) => s.key === slot) || state.slots[0];
    fill(
      host,
      header(),
      state.newly_claimed.length
        ? ui.banner(
            `Unlocked: ${state.newly_claimed.map((c) => c.name).join(", ")}`,
            { kind: "info", glyph: "🎁" }
          )
        : null,

      previewCard(),

      h(
        "div",
        { class: "scroller" },
        ...state.slots.map((spec) =>
          h(
            "button",
            {
              class: "chip",
              type: "button",
              "aria-pressed": String(spec.key === slot),
              onClick: () => {
                slot = spec.key;
                router.setQuery("slot", spec.key);
                paint();
              },
            },
            spec.label,
            h("span", { class: "dim" }, ` ${spec.owned}/${spec.total}`)
          )
        )
      ),

      ui.card(
        {},
        ui.cardHead(slotSpec.label, {
          glyph: "🎨",
          sub:
            slotSpec.max > 1
              ? `${slotSpec.description} Up to ${slotSpec.max} at once.`
              : slotSpec.description,
          action: h(
            "label",
            { class: "row tiny", style: { gap: "6px" } },
            h("input", {
              type: "checkbox",
              checked: showLocked,
              onChange: (event) => {
                showLocked = event.target.checked;
                paint();
              },
            }),
            "Show locked"
          ),
        }),
        grid(slotSpec)
      ),

      dirty() ? stagedBar() : null
    );
  }

  function header() {
    return h(
      "div",
      { class: "row row--between" },
      h(
        "div",
        {},
        h("h1", {}, "Wardrobe"),
        h(
          "div",
          { class: "small dim" },
          `${fmt.num(state.cosmetics.filter((c) => c.owned).length)} of ` +
            `${fmt.num(state.cosmetics.length)} unlocked`
        )
      ),
      ui.pill(`${state.currency.emoji} ${fmt.compact(state.balance)}`, "brand")
    );
  }

  /* ── The live preview ─────────────────────────────────────────────────── */
  function previewCard() {
    // The staged loadout, in the `slot:key+key|slot:key` form the card endpoint
    // reads. It doubles as the cache key: the browser re-fetches when the picks
    // change and not when the page merely repaints.
    const loadout = encodeURIComponent(
      Object.entries(staged)
        .sort()
        .map(([key, values]) => `${key}:${values.join("+")}`)
        .join("|")
    );
    const image = h("img", {
      src: `/api/guilds/${guildId}/me/card/profile?loadout=${loadout}`,
      alt: "Your profile card",
      style: { width: "100%", borderRadius: "var(--r-lg)", display: "block" },
    });
    image.addEventListener("error", () =>
      fill(
        image.parentElement,
        ui.empty("The card couldn't be drawn", "The picks below still work.", { glyph: "🖼️" })
      )
    );

    return ui.card(
      {},
      ui.cardHead("Your card", {
        glyph: "🪪",
        sub: dirty() ? "Previewing your changes" : "As it looks right now",
      }),
      h("div", {}, image),
      h(
        "p",
        { class: "tiny dim", style: { marginTop: "var(--s-2)", marginBottom: 0 } },
        dirty()
          ? "Nothing is saved yet — this is what applying would give you."
          : "The same image /profile posts in Discord."
      )
    );
  }

  /* ── The grid ─────────────────────────────────────────────────────────── */
  /**
   * What you can act on, first.
   *
   * A slot holds up to 47 cosmetics, and in catalogue order that is an
   * alphabetical wall with the four you own, the two you can afford and the
   * forty you can't all shuffled together — so the page answers "what's in the
   * catalogue" when the question is "what can I do right now". This is the same
   * affordability ordering the bot's own `/shop` picker uses.
   *
   * Nothing is hidden: an unaffordable or play-locked row is still listed, with
   * its price or its unlock line, because that list *is* the discovery UI. It
   * just sorts below the rows a tap would actually do something on.
   */
  const RANK = { owned: 0, affordable: 1, expensive: 2, locked: 3 };

  const bucket = (c) => {
    if (c.owned) return "owned";
    if (!c.for_sale) return "locked";
    return state.balance >= c.price ? "affordable" : "expensive";
  };

  function ordered(rows) {
    return [...rows].sort((a, b) => {
      const ra = RANK[bucket(a)];
      const rb = RANK[bucket(b)];
      if (ra !== rb) return ra - rb;
      // Within the two priced buckets, cheapest first — that is the order
      // someone saving up reads them in.
      if ((ra === RANK.affordable || ra === RANK.expensive) && a.price !== b.price) {
        return a.price - b.price;
      }
      return a.name.localeCompare(b.name);
    });
  }

  function grid(slotSpec) {
    const rows = ordered(
      state.cosmetics.filter((c) => c.slot === slotSpec.key && (showLocked || c.owned))
    );
    if (!rows.length) {
      return ui.empty("Nothing here yet", "Play a little, or check the shop.", { glyph: "🎨" });
    }
    return h("div", { class: "grid grid--auto" }, ...rows.map((c) => tile(c, slotSpec)));
  }

  function tile(cosmetic, slotSpec) {
    const picked = (staged[cosmetic.slot] || []).includes(cosmetic.key);
    const palette = cosmetic.palette.length ? cosmetic.palette : ["#5865f2", "#8b5cf6"];

    return h(
      "button",
      {
        class: `cosmetic${cosmetic.owned ? "" : " cosmetic--locked"}`,
        type: "button",
        "aria-pressed": String(picked),
        disabled: !cosmetic.owned,
        onClick: () => toggle(cosmetic, slotSpec),
      },
      h(
        "span",
        {
          class: "swatch",
          style: {
            background: `linear-gradient(135deg, ${palette[0]}, ${palette[palette.length - 1]})`,
          },
          "aria-hidden": "true",
        },
        cosmetic.glyph || ""
      ),
      h(
        "span",
        { class: "row row--between" },
        h("strong", { class: "small" }, cosmetic.name),
        picked
          ? ui.pill("On", "brand")
          : cosmetic.owned
            ? null
            : cosmetic.for_sale
              ? ui.pill(`${state.currency.emoji} ${fmt.compact(cosmetic.price)}`)
              : ui.pill("Locked")
      ),
      h(
        "span",
        { class: "tiny dim" },
        cosmetic.owned ? cosmetic.description || "Yours" : cosmetic.unlock
      ),
      !cosmetic.owned && cosmetic.for_sale
        ? h(
            "span",
            { class: "row", style: { marginTop: "4px" } },
            ui.actionButton(
              state.balance >= cosmetic.price ? "Buy it" : "Too expensive",
              () => buy(cosmetic),
              {
                size: "sm",
                variant: state.balance >= cosmetic.price ? "primary" : "",
                disabled: state.balance < cosmetic.price,
              }
            )
          )
        : null,
      cosmetic.earned_not_claimed ? ui.pill("Earned — open your card", "ok") : null
    );
  }

  /**
   * Stage a pick.
   *
   * Single-value slots swap; the badge showcase toggles up to its limit and
   * says so rather than silently ignoring the seventh tap — the same
   * `equip_result` rule the Discord command follows, applied locally so the
   * preview can move before anything is written.
   */
  function toggle(cosmetic, slotSpec) {
    const current = staged[cosmetic.slot] || [];
    if (slotSpec.max <= 1) {
      staged[cosmetic.slot] = current[0] === cosmetic.key ? [] : [cosmetic.key];
    } else if (current.includes(cosmetic.key)) {
      staged[cosmetic.slot] = current.filter((key) => key !== cosmetic.key);
    } else if (current.length >= slotSpec.max) {
      ui.warn(`${slotSpec.label} holds ${slotSpec.max}. Take one off first.`);
      return;
    } else {
      staged[cosmetic.slot] = [...current, cosmetic.key];
    }
    paint();
  }

  function stagedBar() {
    const count = changedSlots().length;
    return h(
      "div",
      { class: "staged" },
      h(
        "div",
        { class: "grow" },
        h("strong", {}, `${fmt.plural(count, "slot")} changed`),
        h("div", { class: "tiny dim" }, "Nothing saved yet")
      ),
      h(
        "button",
        {
          class: "btn btn--sm",
          type: "button",
          onClick: () => {
            staged = clone(state.equipped);
            paint();
          },
        },
        "Discard"
      ),
      ui.actionButton("Apply", apply, { variant: "primary", size: "sm" })
    );
  }

  async function apply() {
    // Only the slots that changed are sent, so applying twice is a no-op rather
    // than six deletes and six inserts.
    const payload = {};
    for (const key of changedSlots()) payload[key] = staged[key] || [];
    const result = await api.post(`${base}/equip`, { loadout: payload });
    for (const refusal of result.refused || []) ui.warn(refusal.why);
    if (result.changed.length) {
      ui.ok(`${fmt.plural(result.changed.length, "slot")} updated.`, { title: "🎨 Applied" });
    }
    await refresh();
  }

  async function buy(cosmetic) {
    const yes = await ui.confirm(
      `Unlock ${cosmetic.name}?`,
      `This spends ${fmt.coins(cosmetic.price, state.currency)}. Cosmetics are ` +
        "part of your account, so it follows you between servers.",
      { confirmLabel: "Buy it" }
    );
    if (!yes) return;
    await api.post(`${base}/unlock`, { key: cosmetic.key });
    ui.ok(`${cosmetic.name} is yours.`, { title: "🎨 Unlocked" });
    await refresh();
  }

  paint();
  return host;
}
