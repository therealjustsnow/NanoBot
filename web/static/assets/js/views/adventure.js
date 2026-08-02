/**
 * views/adventure.js
 * Work, mining, hunting and exploring — plus the encounters that hang off them.
 *
 * The bot's own dashboard learned this lesson the hard way: a card that says
 * four things are ready, and then makes you type four more commands, has done
 * half its job. So every activity is a button, and there is one Collect all for
 * the case the charge caps are designed around — someone who checks in twice a
 * day and finds a dozen runs banked.
 *
 * Charges are drawn as pips rather than a number because "how many have I got
 * waiting?" is a glance, not a reading. The bucket refills on a timer, so the
 * page counts down to the next one and repaints itself when it lands.
 *
 * An encounter takes over the result card rather than appearing beside it. It
 * is a choice with a real cost and a real payout, and burying it under a haul
 * list is how people miss it — the same reason the Discord version hangs it off
 * the result embed instead of sending a second message.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";

export async function adventure({ guildId }) {
  const base = `/api/guilds/${guildId}/adventure`;
  let state = await api.get(base);
  const host = h("div", { class: "stack" });
  let last = null;

  async function refresh() {
    state = await api.get(base, { fresh: true });
    paint();
  }

  function paint() {
    const runnable = state.activities.filter((a) => a.runnable && a.enabled);
    const ready = runnable.reduce((sum, a) => sum + a.ready, 0);

    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h1", {}, "Adventure"),
          h(
            "div",
            { class: "small dim" },
            ready
              ? `${fmt.plural(ready, "run")} ready to collect`
              : "Nothing banked — check back later"
          )
        ),
        state.streak.days
          ? ui.pill(`🔥 ${fmt.plural(state.streak.days, "day")} · +${fmt.pct(state.streak.multiplier - 1, 0)}`, "warn")
          : null
      ),

      !store.state.playEnabled
        ? ui.banner(
            "Playing from the web is switched off on this instance. Everything still works in Discord.",
            { kind: "warn", glyph: "🔒" }
          )
        : null,

      ready > 1
        ? ui.actionButton(
            `Collect all ${ready} runs`,
            async () => {
              const result = await api.post(`${base}/collect`);
              last = result;
              paintResult(result);
              await refresh();
            },
            { variant: "hero", glyph: "📥", block: true }
          )
        : null,

      last ? resultCard(last) : null,

      h("div", { class: "grid grid--2" }, ...state.activities.map(activityCard)),

      progressCards(state),

      streakCard(state)
    );
  }

  function paintResult(result) {
    last = result;
    // Repaint everything: a run changes the balance, the charges, and possibly
    // the career or the pickaxe. Refreshing piecemeal is how a dashboard ends up
    // showing two different truths at once.
    paint();
    if (result.encounter) openEncounter(result.encounter);
  }

  /* ── One activity ─────────────────────────────────────────────────────── */
  function activityCard(activity) {
    const disabled = !activity.enabled;
    const body = [
      h(
        "div",
        { class: "row" },
        h("span", { class: "icon-tile" }, activity.emoji),
        h(
          "div",
          { class: "grow" },
          h("strong", {}, capitalise(activity.activity)),
          h("div", { class: "tiny dim" }, activity.blurb)
        )
      ),
      h(
        "div",
        { class: "row row--between", style: { marginTop: "var(--s-3)" } },
        ui.pips(activity.ready, activity.max_charges),
        h(
          "span",
          { class: "tiny dim" },
          disabled
            ? "Off in this server"
            : activity.ready
              ? `${activity.ready} of ${activity.max_charges}`
              : ""
        )
      ),
    ];

    let control;
    if (disabled) {
      control = h("div", { class: "banner", style: { marginTop: "var(--s-3)" } }, "Switched off here");
    } else if (!activity.runnable) {
      // /rob takes a target, so it gets a picker instead of a one-tap button —
      // the same reason it has no button in Discord.
      control = ui.actionButton("Choose a target", () => openRob(), {
        block: true,
        glyph: "🎯",
        disabled: !activity.ready,
      });
      if (!activity.ready) {
        control = h("div", { class: "cooldown muted small", style: { marginTop: "var(--s-3)" } },
          "Lying low · ", ui.countdown(activity.next_in, refresh));
      }
    } else if (activity.ready) {
      control = ui.actionButton(
        `Run · ${activity.ready} ready`,
        async () => {
          const result = await api.post(`${base}/run/${activity.activity}`);
          paintResult(result);
        },
        { variant: "primary", block: true }
      );
    } else {
      control = h(
        "div",
        { class: "cooldown muted small", style: { marginTop: "var(--s-3)" } },
        "Next in ",
        ui.countdown(activity.next_in, refresh)
      );
    }

    return ui.card({ class: activity.ready && activity.enabled ? "card--accent" : "" }, ...body,
      h("div", { style: { marginTop: "var(--s-3)" } }, control),
      h("div", { class: "tiny dim", style: { marginTop: "var(--s-2)" } },
        `${fmt.plural(activity.count, "run")} lifetime · one every ${fmt.duration(activity.cooldown)}`));
  }

  /* ── Result ───────────────────────────────────────────────────────────── */
  function resultCard(result) {
    if (result.outcome === "collected") {
      return ui.card(
        { class: "card--accent" },
        ui.cardHead("Collected", {
          glyph: "📥",
          sub: result.ran.map((r) => `${r.emoji} ×${r.runs}`).join("  "),
        }),
        h(
          "div",
          { class: "row row--between" },
          h("span", { class: "muted" }, `${fmt.plural(result.runs, "run")}`),
          h("strong", { class: "tabular" }, `${state.currency.emoji} ${fmt.signed(result.coins)}`)
        ),
        result.items.length
          ? h("div", { class: "scroller", style: { marginTop: "var(--s-3)" } },
              ...result.items.map((item, index) => ui.loot(item, { fresh: true, index })))
          : null
      );
    }

    const titles = {
      work: "Shift done",
      dig: "Dig",
      cave_in: "Cave-in",
      hunt: "Hunt",
      explore: "Explore",
    };
    const glyphs = { work: "💼", dig: "⛏️", cave_in: "💥", hunt: "🏹", explore: "🧭" };

    const lines = [];
    if (result.scene) lines.push(h("p", { class: "muted" }, result.scene));
    if (result.flavor) lines.push(h("p", { class: "muted" }, result.flavor));
    if (result.outcome === "cave_in") {
      lines.push(h("p", { class: "muted" }, "The tunnel collapses behind you — you scramble out empty-handed."));
    }
    if (result.injury) {
      lines.push(h("p", { class: "small" }, `🤕 You took a tumble and paid ${fmt.coins(result.injury, state.currency)} for a bandage.`));
    }
    if (result.career?.promoted) {
      lines.push(ui.banner(`Promoted! You're now a ${result.career.title}.`, { kind: "info", glyph: "🎉" }));
    }

    return ui.card(
      { class: "card--accent" },
      ui.cardHead(titles[result.outcome] || "Result", {
        glyph: glyphs[result.outcome] || "✨",
        sub: result.rich_seam ? "The seam opens up" : null,
      }),
      ...lines,
      result.items?.length
        ? h("div", { class: "scroller" }, ...result.items.map((item, index) => ui.loot(item, { fresh: true, index })))
        : null,
      result.coins
        ? h(
            "div",
            { class: "row row--between", style: { marginTop: "var(--s-3)" } },
            h("span", { class: "muted" }, result.coins > 0 ? "Earned" : "Paid out"),
            h("strong", { class: "tabular" }, `${state.currency.emoji} ${fmt.signed(result.coins)}`)
          )
        : null,
      result.streak?.started
        ? h("p", { class: "small", style: { marginTop: "var(--s-2)" } },
            `🔥 ${result.streak.days}-day streak — coin rewards are +${fmt.pct(result.streak.multiplier - 1, 0)} for the rest of today.`)
        : null
    );
  }

  /* ── Encounter ────────────────────────────────────────────────────────── */
  function openEncounter(encounter) {
    const view = ui.sheet(
      `${encounter.emoji}  ${encounter.title}`,
      h(
        "div",
        { class: "stack" },
        h("p", { class: "muted" }, encounter.prompt),
        h(
          "p",
          { class: "tiny dim" },
          "Answer it or walk away — walking away pays nothing, and that's deliberate."
        )
      ),
      {
        actions: encounter.options.map((option) =>
          ui.actionButton(
            option.label,
            async () => {
              const result = await api.post(`${base}/choose`, {
                token: encounter.token,
                option: option.key,
              });
              view.close();
              ui.sheet(
                "How it went",
                h(
                  "div",
                  { class: "stack" },
                  h("p", {}, result.text),
                  result.coins
                    ? h("div", { class: "row row--between" },
                        h("span", { class: "muted" }, result.coins > 0 ? "Earned" : "Cost"),
                        h("strong", { class: "tabular" }, `${state.currency.emoji} ${fmt.signed(result.coins)}`))
                    : null,
                  result.items?.length
                    ? h("div", { class: "scroller" }, ...result.items.map((item, i) => ui.loot(item, { fresh: true, index: i })))
                    : null
                ),
                { actions: [] }
              );
              await refresh();
              // Some answers open another question. The follow-up arrives under
              // the same key a run's encounter does, so it opens the same way.
              if (result.encounter) openEncounter(result.encounter);
            },
            { glyph: option.emoji, variant: "primary" }
          )
        ),
      }
    );
  }

  /* ── Rob ──────────────────────────────────────────────────────────────── */
  async function openRob() {
    const { members } = await api.get(`/api/guilds/${guildId}/members`);
    const list = h("div", { class: "stack stack--tight" });
    const search = h("input", {
      class: "input",
      type: "search",
      placeholder: "Search members",
      oninput: async () => {
        const found = await api.get(
          `/api/guilds/${guildId}/members?q=${encodeURIComponent(search.value)}`
        );
        fill(list, ...found.members.map(memberRow));
      },
    });

    const view = ui.sheet(
      "Who are you robbing?",
      h(
        "div",
        { class: "stack" },
        ui.banner(
          "Succeed and you take a cut of their wallet. Fail and you pay a fine — " +
            "robbing mints nothing, it only moves coins.",
          { kind: "warn", glyph: "🥷" }
        ),
        search,
        list
      )
    );

    function memberRow(member) {
      return h(
        "div",
        { class: "row" },
        h("img", { class: "avatar avatar--sm", src: member.avatar, alt: "", loading: "lazy" }),
        h("span", { class: "grow" }, member.name),
        ui.actionButton(
          "Rob",
          async () => {
            const result = await api.post(`${base}/rob`, { member: member.id });
            view.close();
            if (result.outcome === "success") {
              ui.ok(`You got away with ${fmt.coins(result.coins, state.currency)}.`, { title: "Clean getaway" });
            } else {
              ui.warn(`Caught. You paid ${fmt.coins(Math.abs(result.coins), state.currency)}.`, { title: "Busted" });
            }
            await refresh();
          },
          { size: "sm", variant: "danger" }
        )
      );
    }

    fill(list, ...members.map(memberRow));
  }

  /* ── Progression ──────────────────────────────────────────────────────── */
  function progressCards(s) {
    return h(
      "div",
      { class: "grid grid--2" },
      ui.card(
        {},
        ui.cardHead("Career", { glyph: "💼", sub: s.career.title }),
        s.career.next
          ? h(
              "div",
              { class: "stack stack--tight" },
              ui.bar(s.career.shifts, s.career.next.shifts, { success: false }),
              h(
                "div",
                { class: "tiny dim" },
                `${fmt.plural(s.career.next.to_go, "shift")} to ${s.career.next.title}`
              )
            )
          : h("p", { class: "small muted" }, "Top of the ladder. 🎉"),
        s.career.bonus
          ? h("div", { class: "tiny dim" }, `+${fmt.num(s.career.bonus)} coins on every shift`)
          : null
      ),
      ui.card(
        {},
        ui.cardHead("Pickaxe", {
          glyph: s.pickaxe.emoji,
          sub: `${s.pickaxe.name} · tier ${s.pickaxe.tier}/${s.pickaxe.tiers}`,
        }),
        h(
          "div",
          { class: "stack stack--tight small" },
          h("div", { class: "row row--between" },
            h("span", { class: "muted" }, "Luck"), h("span", { class: "tabular" }, fmt.pct(s.pickaxe.luck, 0))),
          s.pickaxe.vein
            ? h("div", { class: "row row--between" },
                h("span", { class: "muted" }, "Extra ore per seam"), h("span", { class: "tabular" }, `+${s.pickaxe.vein}`))
            : null,
          h("div", { class: "row row--between" },
            h("span", { class: "muted" }, "Digs"), h("span", { class: "tabular" }, fmt.num(s.pickaxe.digs)))
        ),
        s.pickaxe.next
          ? h(
              "div",
              { style: { marginTop: "var(--s-3)" } },
              ui.actionButton(
                `${s.pickaxe.next.emoji} ${s.pickaxe.next.name} · ${fmt.num(s.pickaxe.next.price)}`,
                async () => {
                  const result = await api.post(`${base}/pickaxe`);
                  ui.ok(`You bought the ${result.pickaxe.name}.`, { title: "Upgraded" });
                  await refresh();
                },
                {
                  variant: "primary",
                  block: true,
                  disabled: !s.pickaxe.next.affordable,
                }
              ),
              !s.pickaxe.next.affordable
                ? h("p", { class: "tiny dim" }, `${fmt.num(s.pickaxe.next.price - s.balance)} more to go.`)
                : null
            )
          : h("p", { class: "small muted" }, "Best pickaxe there is. 🎉")
      )
    );
  }

  function streakCard(s) {
    return ui.card(
      {},
      ui.cardHead("Daily streak", { glyph: "🔥" }),
      s.streak.claimed_today
        ? h(
            "p",
            { class: "muted small" },
            `Day ${s.streak.days}. Coin payouts are +${fmt.pct(s.streak.multiplier - 1, 0)} for the rest of today. ` +
              "Come back tomorrow to keep it climbing."
          )
        : h(
            "p",
            { class: "muted small" },
            "Run anything today to start (or continue) your streak. It's worth up to +25% on coin payouts."
          ),
      h(
        "p",
        { class: "tiny dim", style: { marginBottom: 0 } },
        "The streak multiplies coins only — an activity that pays in items can't pay a fraction of a pelt."
      )
    );
  }

  const capitalise = (word) => word.charAt(0).toUpperCase() + word.slice(1);

  paint();
  return host;
}
