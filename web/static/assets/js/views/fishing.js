/**
 * views/fishing.js
 * Fishing, played in the browser.
 *
 * The Discord version's whole design problem is that a cast is a twenty-second
 * cooldown, so an hour of fishing is 180 invocations of the same thing — which
 * is why the bot moved it onto buttons and a screen that edits itself in place.
 * This page is the same idea with the room a web page has: the cast button, the
 * cooldown, the last haul and everything the cast depended on are all visible at
 * once, and none of it scrolls away.
 *
 * Three things this can show that an embed can't afford to:
 *
 *   - **Why you caught that.** The luck stack is itemised — rod, level, bait,
 *     charms, events — because "is my bait actually doing anything?" is a real
 *     question the chat version answers only by implication.
 *   - **The catch arriving.** Fish animate in, one after another, with the
 *     rarity as the card's own colour rather than a word in front of a name.
 *   - **The whole shelf at once.** Travel, tackle, the bag and the trap are tabs
 *     rather than separate commands, so buying bait and arming it is two taps in
 *     the same place.
 *
 * The cooldown ticks locally but is never trusted: every cast asks the server,
 * and a refusal comes back with the real `retry_after`, which resets the timer.
 * The client-side countdown is a courtesy, not a gate.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

const TABS = [
  { value: "cast", label: "Cast" },
  { value: "bag", label: "Bag" },
  { value: "map", label: "Map" },
  { value: "shop", label: "Tackle" },
  { value: "dex", label: "Dex" },
];

export async function fishing({ guildId }) {
  const base = `/api/guilds/${guildId}/fishing`;
  let state = await api.get(base);

  if (!state.enabled) {
    return ui.empty(
      "Fishing is switched off here",
      "An admin can turn it back on from Settings → Games, or with /fish toggle in Discord.",
      { glyph: "🎣" }
    );
  }

  const host = h("div", { class: "stack" });
  let tab = router.query("tab", "cast");
  let lastCast = null;

  /** Re-read the whole state and repaint. One request, because the page is
      useless with half of it. */
  async function refresh() {
    state = await api.get(base, { fresh: true });
    paint();
  }

  function paint() {
    fill(
      host,
      header(state),
      // A read-only instance still renders everything — the play buttons just
      // come back refused. Saying so up front beats letting someone find out by
      // pressing.
      !store.state.playEnabled
        ? ui.banner(
            "Playing from the web is switched off on this instance. Everything still works in Discord.",
            { kind: "warn", glyph: "🔒" }
          )
        : null,
      ui.segmented(TABS, tab, (value) => {
        tab = value;
        router.setQuery("tab", value === "cast" ? null : value);
        paint();
      }, { label: "Fishing" }),
      tab === "cast" ? castPanel() : null,
      tab === "bag" ? bagPanel() : null,
      tab === "map" ? mapPanel() : null,
      tab === "shop" ? shopPanel() : null,
      tab === "dex" ? dexPanel() : null
    );
  }

  /* ── Cast ─────────────────────────────────────────────────────────────── */
  function castPanel() {
    const readout = h("div", { class: "stack" });
    const pond = h("div", { class: "pond stack" });

    const paintPond = (cooldownLeft) => {
      fill(
        pond,
        h("div", { style: { fontSize: "2.4rem" } }, state.spot.emoji),
        h("h2", {}, state.spot.name),
        h("p", { class: "small", style: { color: "#9fb4c9" } }, state.spot.blurb),
        cooldownLeft > 0
          ? h(
              "div",
              { class: "cooldown muted" },
              "Line's in the water · ",
              ui.countdown(cooldownLeft, () => paintPond(0))
            )
          : ui.actionButton("Cast your line", cast, {
              variant: "hero",
              glyph: "🎣",
              block: true,
            })
      );
    };
    paintPond(state.ready_in);

    async function cast() {
      pond.classList.add("pond--casting");
      try {
        const result = await api.post(`${base}/cast`);
        lastCast = result;
        state.balance = result.balance;
        state.bag.count = result.bag_count;
        state.level.level = result.level.level;
        fill(readout, castResult(result));
        if (result.level.levelled_up) {
          ui.ok(`Fishing level ${result.level.level}!`, { title: "Level up" });
        }
        for (const note of result.notes || []) {
          ui.ok(note.text + (note.coins ? ` +${fmt.num(note.coins)}` : ""), { title: "🔥" });
        }
        paintPond(result.cooldown);
        // The cast changed the bag, the wallet, the quest, the streak and
        // possibly an event; re-read in the background and repaint the lot.
        // Refilling only the status bar left the quest bar, the luck stack and
        // the events card showing pre-cast numbers — and wrote into `bar` even
        // after a tab change had detached it. `lastCast` is kept, so the catch
        // stays on screen through the repaint.
        api.get(base, { fresh: true }).then((fresh) => {
          state = fresh;
          paint();
        });
      } catch (error) {
        if (error.retryAfter) paintPond(error.retryAfter);
        throw error;
      } finally {
        pond.classList.remove("pond--casting");
      }
    }

    const bar = h("div", { class: "grid grid--2" }, ...statusBar(state));

    if (lastCast) fill(readout, castResult(lastCast));

    return h(
      "div",
      { class: "stack" },
      pond,
      readout,
      bar,
      questCard(state),
      state.events.length ? eventsCard(state.events) : null,
      luckCard(state)
    );
  }

  function castResult(result) {
    if (result.outcome === "snag") {
      return ui.card(
        {},
        ui.cardHead("Snapped!", { glyph: "💥", sub: result.spot.name }),
        h(
          "p",
          { class: "muted" },
          "The line goes taut, sings, and snaps. Whatever that was, it's keeping it." +
            (result.bait_spent ? " Your bait went with it — one charge, no more." : "")
        ),
        h("div", { class: "row small dim" }, `+${fmt.num(result.xp)} XP`)
      );
    }
    const total = result.catches.reduce((sum, entry) => sum + (entry.value || entry.coins || 0), 0);
    return ui.card(
      {},
      ui.cardHead(result.net ? "Net haul" : "Caught!", {
        glyph: result.net ? "🕸️" : "🐟",
        sub: `${result.spot.name} · +${fmt.num(result.xp)} XP`,
      }),
      h(
        "div",
        { class: "scroller" },
        ...result.catches.map((entry, index) => ui.loot(entry, { fresh: true, index }))
      ),
      h(
        "div",
        { class: "row row--between small", style: { marginTop: "var(--s-3)" } },
        h("span", { class: "muted" }, result.coins ? "Treasure paid straight out" : "Worth in the bag"),
        h("strong", { class: "tabular" }, `${state.currency.emoji} ${fmt.num(result.coins || total)}`)
      )
    );
  }

  function statusBar(s) {
    return [
      ui.statTile("Balance", `${s.currency.emoji} ${fmt.compact(s.balance)}`, {
        sub: `${fmt.plural(s.bag.count, "fish")} in the bag`,
      }),
      ui.statTile(`Level ${s.level.level}`, `${fmt.num(s.level.into)}/${fmt.num(s.level.need)}`, {
        sub: `+${fmt.pct(s.level.luck_bonus, 0)} luck from levels`,
      }),
    ];
  }

  /* ── Luck ─────────────────────────────────────────────────────────────── */
  function luckCard(s) {
    const armed = s.armed || [];
    return ui.card(
      {},
      ui.cardHead("Your luck", {
        glyph: "🍀",
        sub: "Shifts the odds away from junk and towards the good stuff",
        action: h("strong", { class: "tabular" }, fmt.pct(rodLuck(s) , 0)),
      }),
      ui.bar(rodLuck(s), s.rod ? 1 : 1, { success: true }),
      h(
        "div",
        { class: "stack stack--tight small", style: { marginTop: "var(--s-3)" } },
        luckRow("Rod", s.rod.name, fmt.pct(s.rod.luck, 0)),
        luckRow("Fishing level", `Level ${s.level.level}`, `+${fmt.pct(s.level.luck_bonus, 0)}`),
        armed.length
          ? armed.map((effect) =>
              luckRow(
                effectLabel(effect.key),
                effect.uses_left ? `${effect.uses_left} left` : effect.expires_in ? fmt.duration(effect.expires_in) : "active",
                effect.magnitude < 1 ? `+${fmt.pct(effect.magnitude, 0)}` : `×${effect.magnitude}`
              )
            )
          : h("p", { class: "tiny dim" }, "Nothing armed. Bait from the Tackle tab adds luck for a few casts."),
        s.events.length
          ? s.events.map((event) => luckRow(event.label, fmt.duration(event.ends_in) + " left", "event"))
          : null
      )
    );
  }

  const rodLuck = (s) => (s.rod?.luck || 0) + (s.level?.luck_bonus || 0);

  const luckRow = (label, detail, value) =>
    h(
      "div",
      { class: "row row--between" },
      h("span", {}, label, h("span", { class: "dim" }, ` · ${detail}`)),
      h("span", { class: "tabular muted" }, value)
    );

  const effectLabel = (key) =>
    ({ fish_bait: "Bait", fish_net: "Cast net", fish_xp: "XP potion", luck: "Lucky charm" })[key] || key;

  /* ── Quest & events ───────────────────────────────────────────────────── */
  function questCard(s) {
    const q = s.quest;
    return ui.card(
      {},
      ui.cardHead("Today's quest", {
        glyph: "📜",
        action: q.claimed ? ui.pill("Claimed", "ok") : null,
      }),
      h("p", { class: "muted small" }, q.label),
      ui.bar(Math.min(q.progress, q.target), q.target, { success: q.claimed }),
      h(
        "div",
        { class: "row row--between tiny dim", style: { marginTop: "6px" } },
        h("span", {}, `${fmt.num(q.progress)} / ${fmt.num(q.target)}`),
        h("span", {}, `Reward: ${s.currency.emoji} ${fmt.num(q.reward.coins)} · ${fmt.num(q.reward.xp)} XP`)
      )
    );
  }

  const eventsCard = (events) =>
    ui.card(
      { class: "card--accent" },
      ui.cardHead("Happening now", { glyph: "✨" }),
      h(
        "div",
        { class: "stack stack--tight" },
        ...events.map((event) =>
          h(
            "div",
            { class: "row row--between small" },
            h("strong", {}, event.label),
            h("span", { class: "dim" }, `${fmt.duration(event.ends_in)} left`)
          )
        )
      )
    );

  /* ── Bag ──────────────────────────────────────────────────────────────── */
  function bagPanel() {
    const bag = state.bag;
    if (!bag.rows.length) {
      return ui.empty("Your bag is empty", "Cast a line and whatever you catch lands here.", {
        glyph: "🎒",
      });
    }
    return h(
      "div",
      { class: "stack" },
      ui.card(
        {},
        ui.cardHead("Bag", {
          glyph: "🎒",
          sub: `${fmt.plural(bag.count, "fish")} · worth ${state.currency.emoji} ${fmt.num(bag.value)}`,
          action: ui.actionButton(
            "Sell everything",
            async () => {
              const result = await api.post(`${base}/sell`, { target: "all" });
              ui.ok(`Sold ${fmt.plural(result.sold, "fish")} for ${fmt.coins(result.coins, state.currency)}.`);
              await refresh();
            },
            { variant: "primary", size: "sm", glyph: "💰" }
          ),
        }),
        h(
          "div",
          { class: "stack stack--tight" },
          ...bag.rows.map((row) =>
            h(
              "div",
              { class: "row" },
              h("span", { class: "icon-tile" }, row.emoji),
              h(
                "div",
                { class: "grow" },
                h("div", {}, row.name, h("span", { class: "dim small" }, ` ×${fmt.num(row.qty)}`)),
                h("div", { class: "tiny", style: { color: row.rarity_color } }, row.rarity_label)
              ),
              ui.actionButton(
                `${state.currency.emoji} ${fmt.compact(row.total_value)}`,
                async () => {
                  const result = await api.post(`${base}/sell`, { target: row.key });
                  ui.ok(`Sold ${fmt.plural(result.sold, row.name)} for ${fmt.coins(result.coins, state.currency)}.`);
                  await refresh();
                },
                { size: "sm" }
              )
            )
          )
        )
      )
    );
  }

  /* ── Map ──────────────────────────────────────────────────────────────── */
  function mapPanel() {
    return h(
      "div",
      { class: "stack" },
      ui.banner(
        "Every spot has species found nowhere else — that's the reason to travel. " +
          "Chartering costs coins once; moving between spots you've chartered is free.",
        { kind: "info", glyph: "🗺️" }
      ),
      ...state.spots.map((spot) => spotCard(spot))
    );
  }

  function spotCard(spot) {
    const gate = spot.state;
    const current = spot.current;
    return ui.card(
      { class: current ? "card--accent" : "" },
      h(
        "div",
        { class: "row" },
        h("span", { class: "icon-tile" }, spot.emoji),
        h(
          "div",
          { class: "grow" },
          h("div", { class: "row" }, h("strong", {}, spot.name), current ? ui.pill("You're here", "brand") : null),
          h("div", { class: "small dim" }, spot.blurb)
        ),
        current
          ? null
          : gate.state === "open"
            ? ui.actionButton("Travel", () => travel(spot), { size: "sm" })
            : gate.state === "buyable"
              ? ui.actionButton(`Charter · ${fmt.compact(spot.price)}`, () => travel(spot), {
                  size: "sm",
                  variant: "primary",
                })
              : h("span", { class: `pill pill--${gate.state === "locked" ? "bad" : "warn"}` }, gate.reason)
      ),
      h(
        "div",
        { class: "row row--wrap small dim", style: { marginTop: "var(--s-2)" } },
        spot.level ? h("span", {}, `Needs level ${spot.level}`) : h("span", {}, "Open to everyone"),
        spot.hazard ? h("span", {}, `${fmt.pct(spot.hazard, 0)} snag risk`) : h("span", {}, "No snags"),
        spot.exclusives.length ? h("span", {}, `${spot.exclusives.length} exclusive species`) : null
      ),
      spot.exclusives.length
        ? h(
            "div",
            { class: "scroller", style: { marginTop: "var(--s-2)" } },
            ...spot.exclusives.map((fish) => ui.loot(fish))
          )
        : null
    );
  }

  async function travel(spot) {
    const result = await api.post(`${base}/travel`, { spot: spot.key });
    ui.ok(
      result.charged
        ? `Chartered ${result.spot.name} for ${fmt.coins(result.charged, state.currency)}.`
        : `You're fishing at ${result.spot.name}.`
    );
    await refresh();
  }

  /* ── Tackle shop ──────────────────────────────────────────────────────── */
  function shopPanel() {
    return h(
      "div",
      { class: "stack" },
      trapCard(),
      ui.card(
        {},
        ui.cardHead("Tackle shop", {
          glyph: "🛒",
          sub: `You have ${state.currency.emoji} ${fmt.num(state.balance)}`,
        }),
        h(
          "div",
          { class: "stack stack--tight" },
          ...state.shop.map((item) =>
            h(
              "div",
              { class: "tile row" },
              h("span", { class: "icon-tile" }, item.emoji),
              h(
                "div",
                { class: "grow" },
                h(
                  "div",
                  { class: "row" },
                  h("strong", {}, item.name),
                  item.owned ? ui.pill(`${fmt.num(item.owned)} owned`) : null
                ),
                h("div", { class: "tiny dim" }, item.description)
              ),
              h(
                "div",
                { class: "btn-group" },
                item.owned && item.armable
                  ? ui.actionButton("Arm", async () => {
                      await api.post(`${base}/arm`, { item: item.key, qty: 1 });
                      ui.ok(`${item.name} armed.`);
                      await refresh();
                    }, { size: "sm", glyph: "⚡" })
                  : null,
                ui.actionButton(
                  `${fmt.compact(item.price)}`,
                  async () => {
                    await api.post(`${base}/buy`, { item: item.key, qty: 1 });
                    ui.ok(`Bought a ${item.name}.`, {
                      action: item.armable
                        ? {
                            label: "Arm it",
                            onClick: async () => {
                              await api.post(`${base}/arm`, { item: item.key, qty: 1 });
                              ui.ok(`${item.name} armed.`);
                              await refresh();
                            },
                          }
                        : null,
                    });
                    await refresh();
                  },
                  { size: "sm", variant: item.affordable ? "primary" : "", disabled: !item.affordable }
                )
              )
            )
          )
        ),
        h(
          "p",
          { class: "tiny dim", style: { marginTop: "var(--s-3)", marginBottom: 0 } },
          "Bought bait does nothing until it's armed — that's what the ⚡ button is for."
        )
      ),
      rodCard()
    );
  }

  function rodCard() {
    const rod = state.rod;
    return ui.card(
      {},
      ui.cardHead("Your rod", { glyph: rod.emoji, sub: rod.name }),
      rod.next
        ? h(
            "div",
            { class: "stack stack--tight" },
            h(
              "div",
              { class: "row row--between small" },
              h("span", { class: "muted" }, `Next: ${rod.next.emoji} ${rod.next.name}`),
              h("span", { class: "tabular" }, `luck ${fmt.pct(rod.luck, 0)} → ${fmt.pct(rod.next.luck, 0)}`)
            ),
            ui.actionButton(
              `Upgrade · ${state.currency.emoji} ${fmt.num(rod.next.price)}`,
              async () => {
                const result = await api.post(`${base}/upgrade`);
                ui.ok(`You bought the ${result.rod.name}.`, { title: "Upgraded" });
                await refresh();
              },
              { variant: "primary", block: true, disabled: state.balance < rod.next.price }
            ),
            state.balance < rod.next.price
              ? h("p", { class: "tiny dim" }, `${fmt.num(rod.next.price - state.balance)} more to go.`)
              : null
          )
        : h("p", { class: "muted small" }, "You own the best rod there is. 🎉")
    );
  }

  function trapCard() {
    /* One trap per spot, so the card is a list: each row is a place with its
       own soak bar. Pulling collects every ready one at once — travel is free
       and the basket is rolled at the water it sat in either way. */
    const trap = state.trap;
    const rows = (trap.traps || []).map((t) =>
      h(
        "div",
        { class: "stack stack--tight" },
        h("div", { class: "small" }, `${t.spot.emoji || ""} ${t.spot.name}`),
        ui.bar(t.soaked, trap.soak, { success: t.ready }),
        t.ready
          ? h("div", { class: "small dim" }, "Ready to pull")
          : h("div", { class: "cooldown muted" }, "Ready in ", ui.countdown(t.ready_in, refresh))
      )
    );
    const actions = [];
    if (trap.ready) {
      actions.push(
        ui.actionButton(trap.ready > 1 ? `Pull ${trap.ready} traps` : "Pull the trap", pullTrap, {
          variant: "primary",
          block: true,
          glyph: "🪣",
        })
      );
    } else if (trap.here && trap.owned) {
      actions.push(ui.actionButton("Set a trap here", pullTrap, { variant: "primary", block: true }));
    } else if (trap.here) {
      actions.push(h("p", { class: "small dim" }, "You don't own one. The shop below sells them."));
    }
    return ui.card(
      {},
      ui.cardHead("Fish traps", {
        glyph: "🪤",
        sub: trap.set
          ? `${fmt.plural(trap.traps.length, "trap")} in the water`
          : "Fishes while you're not here — one per spot",
      }),
      h("div", { class: "stack stack--tight" }, ...rows, ...actions)
    );
  }

  async function pullTrap() {
    const result = await api.post(`${base}/trap`);
    if (result.action === "set") {
      ui.ok(`Trap set at ${result.spot.name}. Come back in ${fmt.duration(result.soak)}.`);
    } else {
      const where = result.baskets.length > 1 ? ` from ${fmt.plural(result.baskets.length, "spot")}` : "";
      ui.ok(`The baskets hold ${fmt.plural(result.catches.length, "fish")}${where}.`, { title: "Traps pulled" });
    }
    await refresh();
  }

  /* ── Dex ──────────────────────────────────────────────────────────────── */
  function dexPanel() {
    const dex = state.dex;
    const caught = dex.species.filter((s) => s.count > 0);
    const missing = dex.species.filter((s) => !s.count);
    return h(
      "div",
      { class: "stack" },
      ui.card(
        {},
        ui.cardHead("Encyclopedia", {
          glyph: "📖",
          sub: `${fmt.num(dex.caught)} of ${fmt.num(dex.total)} species`,
        }),
        ui.bar(dex.caught, dex.total, { success: dex.caught === dex.total }),
        h(
          "p",
          { class: "tiny dim", style: { marginTop: "var(--s-2)", marginBottom: 0 } },
          "The dex is lifetime — selling a fish never removes it."
        )
      ),
      caught.length
        ? h(
            "section",
            {},
            h("div", { class: "section__head" }, h("h2", {}, "Caught")),
            h("div", { class: "grid grid--auto" }, ...caught.map((fish) => ui.loot({ ...fish, qty: fish.count })))
          )
        : null,
      missing.length
        ? h(
            "section",
            {},
            h("div", { class: "section__head" }, h("h2", {}, "Still out there")),
            h(
              "div",
              { class: "grid grid--auto" },
              ...missing.map((fish) =>
                h(
                  "div",
                  { class: `loot loot-rarity-${fish.rarity}`, style: { opacity: 0.45 } },
                  h("span", { class: "loot__glyph" }, "❔"),
                  h("span", { class: "loot__name" }, fish.rarity_label),
                  fish.spot ? h("span", { class: "loot__meta" }, "spot exclusive") : null
                )
              )
            )
          )
        : null
    );
  }

  function header(s) {
    return h(
      "div",
      { class: "row row--between" },
      h(
        "div",
        {},
        h("h1", {}, "Fishing"),
        h("div", { class: "small dim" }, `${s.spot.emoji} ${s.spot.name} · ${s.rod.name}`)
      ),
      s.streak.days
        ? ui.pill(`🔥 ${fmt.plural(s.streak.days, "day")}`, "warn")
        : null
    );
  }

  paint();
  return host;
}
