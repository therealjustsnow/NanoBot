/**
 * views/profile.js
 * The account: the card, both levels, the wallet, the stats, the wardrobe.
 *
 * The card at the top is the *real* one — the same PNG `/profile` posts in
 * Discord, rendered by the same code. That matters more than it sounds: the
 * card is the thing people screenshot, and a website version that looked
 * almost-but-not-quite the same would immediately be the wrong one.
 *
 * Below it, the page does what an image can't: shows the numbers as numbers, and
 * lists the whole cosmetic catalogue with what each one takes — including the
 * ones not yet earned, because a wardrobe that only shows what you own answers
 * no questions.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

const CARDS = [
  { value: "profile", label: "Profile" },
  { value: "wallet", label: "Wallet" },
  { value: "trophies", label: "Trophies" },
];

export async function profile({ guildId }) {
  const data = await api.get(`/api/guilds/${guildId}/me/profile`);
  const host = h("div", { class: "stack" });
  let card = router.query("card", "profile");

  function paint() {
    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          { class: "row" },
          data.member.avatar
            ? h("img", { class: "avatar avatar--lg", src: data.member.avatar, alt: "" })
            : null,
          h(
            "div",
            {},
            h("h1", {}, data.member.name),
            data.progression.title
              ? h("div", { class: "small dim" }, data.progression.title)
              : null
          )
        ),
        data.progression.prestige ? ui.pill(`⭐ Prestige ${data.progression.prestige}`, "warn") : null
      ),

      cardBlock(),

      h(
        "div",
        { class: "grid grid--2" },
        levelCard(
          "Account level",
          data.global_level.level,
          data.global_level.into,
          data.global_level.need,
          "Earned everywhere you use NanoBot. Not configurable, by design."
        ),
        levelCard(
          "Server level",
          data.server_level.level,
          data.server_level.into,
          data.server_level.need,
          data.server_level.rank
            ? `#${fmt.num(data.server_level.rank)} in ${data.server_level.scope}`
            : `From messages in ${data.server_level.scope}`
        )
      ),

      walletCard(data.wallet),
      statsCard(data.stats),
      socialCard(data.social),
      wardrobeLink()
    );
  }

  function cardBlock() {
    const image = h("img", {
      src: `/api/guilds/${guildId}/me/card/${card}?member=${data.member.id}`,
      alt: `${data.member.name}'s ${card} card`,
      style: { width: "100%", borderRadius: "var(--r-lg)", display: "block" },
      loading: "eager",
    });
    // A render can fail (a missing font, a cog not loaded). The page should lose
    // one image, not break.
    image.addEventListener("error", () =>
      fill(
        image.parentElement,
        ui.empty("That card couldn't be drawn", "The numbers below are all still here.", { glyph: "🖼️" })
      )
    );

    return ui.card(
      {},
      h(
        "div",
        { class: "row row--between", style: { marginBottom: "var(--s-3)" } },
        ui.segmented(CARDS, card, (value) => {
          card = value;
          router.setQuery("card", value === "profile" ? null : value);
          paint();
        }, { label: "Card" }),
        h(
          "a",
          {
            class: "btn btn--sm btn--ghost",
            href: `/api/guilds/${guildId}/me/card/${card}?member=${data.member.id}`,
            target: "_blank",
            rel: "noopener",
          },
          "Open"
        )
      ),
      h("div", {}, image),
      h(
        "p",
        { class: "tiny dim", style: { marginTop: "var(--s-2)", marginBottom: 0 } },
        "This is the same image /profile posts in Discord."
      )
    );
  }

  const levelCard = (title, level, into, need, note) =>
    ui.card(
      {},
      ui.cardHead(title, { glyph: "📈" }),
      ui.stat("Level", fmt.num(level), { large: true }),
      ui.bar(into, need),
      h("div", { class: "tiny dim", style: { marginTop: "6px" } }, `${fmt.num(into)} / ${fmt.num(need)} XP · ${note}`)
    );

  function walletCard(wallet) {
    return ui.card(
      {},
      ui.cardHead("Wallet", { glyph: "🪙" }),
      h(
        "div",
        { class: "grid grid--2" },
        ui.statTile("Balance", `${wallet.currency.emoji} ${fmt.compact(wallet.balance)}`, {
          sub: wallet.rank ? `#${fmt.num(wallet.rank)} of ${fmt.num(wallet.of)}` : "unranked",
        }),
        ui.statTile("Contribution", fmt.compact(wallet.contribution.points), {
          sub: wallet.contribution.title || "from co-op activities",
        }),
        ui.statTile("Daily streak", fmt.plural(wallet.streak.days, "day"), {
          sub: wallet.streak.capped ? "bonus maxed" : `bonus caps at ${wallet.streak.cap}`,
        }),
        ui.statTile("Daily", wallet.daily.ready ? "Ready" : fmt.duration(wallet.daily.ready_in), {
          sub: wallet.daily.ready ? "claim it" : "until the next one",
        })
      ),
      wallet.daily.ready
        ? h(
            "div",
            { style: { marginTop: "var(--s-3)" } },
            ui.actionButton(
              "Claim your daily",
              async () => {
                const result = await api.post(`/api/guilds/${guildId}/wallet/daily`);
                ui.ok(
                  `${result.band.label} You claimed ${fmt.coins(result.coins, wallet.currency)}` +
                    (result.streak_bonus ? ` (${fmt.num(result.base)} rolled + ${fmt.num(result.streak_bonus)} streak).` : "."),
                  { title: `${result.band.emoji} Daily reward` }
                );
                router.reload();
              },
              { variant: "primary", block: true, glyph: "🎁" }
            )
          )
        : null
    );
  }

  /* The stat keys come from the progression cog's registry, so this maps only
     the ones worth surfacing — the rest are still on the card. */
  const STAT_ROWS = [
    ["fish_caught", "🎣", "Fish caught"],
    ["fishing_earned", "💰", "Fishing earnings"],
    ["fishing_level", "📈", "Fishing level"],
    ["casino_games", "🎰", "Casino games"],
    ["casino_won", "🃏", "Casino winnings"],
    ["work_shifts", "💼", "Shifts worked"],
    ["mine_count", "⛏️", "Digs"],
    ["hunt_count", "🏹", "Hunts"],
    ["explore_count", "🧭", "Expeditions"],
    ["items_owned", "🎒", "Items carried"],
  ];

  function statsCard(stats) {
    const rows = STAT_ROWS.filter(([key]) => stats[key]);
    if (!rows.length) {
      return ui.card(
        {},
        ui.cardHead("Lifetime", { glyph: "📊" }),
        ui.empty("Nothing yet", "Fish, mine or work and this fills up.", { glyph: "🌱" })
      );
    }
    return ui.card(
      {},
      ui.cardHead("Lifetime", { glyph: "📊", sub: "Across every server" }),
      h(
        "div",
        { class: "grid grid--auto" },
        ...rows.map(([key, glyph, label]) =>
          h(
            "div",
            { class: "tile row" },
            h("span", { "aria-hidden": "true" }, glyph),
            h("span", { class: "grow small" }, label),
            h("strong", { class: "tabular" }, fmt.compact(stats[key]))
          )
        )
      ),
      h(
        "div",
        { class: "row row--between", style: { marginTop: "var(--s-3)" } },
        h("span", { class: "muted small" }, "Achievements"),
        h("strong", {}, fmt.num(data.progression.achievements))
      )
    );
  }

  const socialCard = (social) =>
    ui.card(
      {},
      ui.cardHead("Social", { glyph: "🤝" }),
      h(
        "div",
        { class: "grid grid--2" },
        ui.statTile("Reputation", fmt.num(social.rep || 0)),
        ui.statTile("Cookies", fmt.num(social.cookies_received || 0), {
          sub: `${fmt.num(social.cookies_sent || 0)} given`,
        })
      )
    );

  function wardrobeLink() {
    return ui.card(
      {},
      ui.cardHead("Wardrobe", {
        glyph: "🎨",
        sub: `${fmt.num(data.cosmetics.unlocked.length)} unlocked`,
        action: ui.actionButton("Open", openWardrobe, { size: "sm" }),
      }),
      h(
        "p",
        { class: "small muted", style: { marginBottom: 0 } },
        "Banners, borders, nameplates and badges. Some are earned by playing, " +
          "some are bought with coins, and a few are only ever given out."
      )
    );
  }

  async function openWardrobe() {
    const { cosmetics } = await api.get("/api/me/cosmetics");
    const slots = [...new Set(cosmetics.map((c) => c.slot))];
    let slot = slots[0];
    const list = h("div", { class: "stack stack--tight" });

    const render = () => {
      const rows = cosmetics.filter((c) => c.slot === slot);
      fill(
        list,
        ...rows.map((cosmetic) =>
          h(
            "div",
            { class: "tile row" },
            h(
              "div",
              { class: "grow" },
              h(
                "div",
                { class: "row" },
                h("strong", {}, cosmetic.name),
                cosmetic.worn ? ui.pill("Worn", "brand") : null,
                cosmetic.earned_not_claimed ? ui.pill("Earned", "ok") : null
              ),
              h("div", { class: "tiny dim" }, cosmetic.owned ? cosmetic.description || "Yours" : cosmetic.unlock)
            ),
            cosmetic.owned
              ? ui.pill("Owned", "ok")
              : cosmetic.for_sale
                ? ui.pill(`🪙 ${fmt.num(cosmetic.price)}`)
                : ui.pill("Locked")
          )
        )
      );
    };

    ui.sheet(
      "Wardrobe",
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "scroller" },
          ...slots.map((key) =>
            h(
              "button",
              {
                class: "chip",
                type: "button",
                "aria-pressed": String(key === slot),
                onClick: (event) => {
                  slot = key;
                  [...event.target.parentElement.children].forEach((child) =>
                    child.setAttribute("aria-pressed", String(child === event.target))
                  );
                  render();
                },
              },
              cosmetics.find((c) => c.slot === key)?.slot_label || key
            )
          )
        ),
        ui.banner(
          "Equip and unequip from Discord with /profile equip — the wardrobe here " +
            "is for seeing what's out there and how to get it.",
          { kind: "info", glyph: "💡" }
        ),
        list
      )
    );
    render();
  }

  paint();
  return host;
}
