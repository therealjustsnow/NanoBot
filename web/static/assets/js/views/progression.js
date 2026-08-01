/**
 * views/progression.js
 * The long game: everything a member has built, and what's next.
 *
 * The brief for this page was "celebrate accomplishments rather than present
 * numbers", and the concrete version of that is ordering. It opens with what
 * you're *closest to earning* — not the list, not the totals — because that is
 * the thing that makes somebody go and do one more dig. Then the two levels,
 * then this week, then the case, then prestige.
 *
 * Every locked achievement carries a progress bar, which is the thing the
 * Discord version genuinely can't afford: forty bars is a wall in an embed and
 * a glance on a page. It is also what makes "closest" a real, sortable idea
 * rather than a guess.
 *
 * Opening your own page banks anything you've qualified for — the same lazy
 * award `/progress` does, through the same once-only insert — so the numbers
 * here are never behind the numbers in chat.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

export async function progression({ guildId }) {
  const base = `/api/guilds/${guildId}/progression`;
  let state = await api.get(base);
  const host = h("div", { class: "stack" });
  let filter = router.query("category", "all");
  let showEarned = true;

  async function refresh() {
    state = await api.get(base, { fresh: true });
    paint();
  }

  function paint() {
    fill(
      host,
      header(),
      state.newly_earned.length ? newlyCard() : null,
      state.closest.length ? closestCard() : null,
      levelsCard(),
      weeklyCard(),
      titlesCard(),
      caseCard(),
      prestigeCard()
    );
  }

  function header() {
    return h(
      "div",
      { class: "row row--between" },
      h(
        "div",
        {},
        h("h1", {}, "Progress"),
        h(
          "div",
          { class: "small dim" },
          `${fmt.num(state.earned_count)} of ${fmt.num(state.total_count)} achievements · ` +
            `${fmt.num(state.points)} points`
        )
      ),
      state.prestige.rank
        ? ui.pill(`⭐ ${state.prestige.title}`, "warn")
        : ui.pill(`Tier ${state.tier}`, "brand")
    );
  }

  const newlyCard = () =>
    ui.card(
      { class: "card--accent" },
      ui.cardHead("Just earned", { glyph: "🎉" }),
      h(
        "div",
        { class: "row row--wrap" },
        ...state.newly_earned.map((entry) =>
          ui.pill(`${entry.emoji} ${entry.name} · +${entry.points}`, "ok")
        )
      )
    );

  /* ── Nearly there ─────────────────────────────────────────────────────── */
  function closestCard() {
    return ui.card(
      {},
      ui.cardHead("Nearly there", {
        glyph: "🎯",
        sub: "The three you're closest to",
      }),
      h(
        "div",
        { class: "stack" },
        ...state.closest.map((entry) =>
          h(
            "div",
            { class: "stack stack--tight" },
            h(
              "div",
              { class: "row row--between small" },
              h("span", {}, `${entry.emoji} ${entry.name}`),
              h(
                "span",
                { class: "dim tabular" },
                `${fmt.compact(entry.value)} / ${fmt.compact(entry.threshold)}`
              )
            ),
            ui.bar(entry.value, entry.threshold),
            h("div", { class: "tiny dim" }, entry.description)
          )
        )
      )
    );
  }

  /* ── Levels ───────────────────────────────────────────────────────────── */
  function levelsCard() {
    const g = state.levels.global;
    return ui.card(
      {},
      ui.cardHead("Levels", { glyph: "📈" }),
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "stack stack--tight" },
          h(
            "div",
            { class: "row row--between" },
            ui.stat("Account level", fmt.num(g.level), {
              sub: "Everywhere you use NanoBot",
              large: true,
            }),
            h("div", { class: "ring", style: { "--pct": Math.round((g.into / g.need) * 100) } },
              h("span", { class: "ring__label" }, `${Math.round((g.into / g.need) * 100)}%`))
          ),
          ui.bar(g.into, g.need),
          h("div", { class: "tiny dim" }, `${fmt.num(g.need - g.into)} XP to level ${g.level + 1}`)
        ),
        h(
          "p",
          { class: "tiny dim", style: { marginBottom: 0 } },
          "The account level is deliberately not configurable — a server with " +
            "5× XP grants no more of it than any other, which is what makes it " +
            "mean the same thing everywhere."
        )
      )
    );
  }

  /* ── Weekly ───────────────────────────────────────────────────────────── */
  function weeklyCard() {
    const weekly = state.weekly;
    return ui.card(
      {},
      ui.cardHead("This week", {
        glyph: "📅",
        sub: weekly.period,
        action:
          weekly.multiplier > 1
            ? ui.pill(`Prestige ×${weekly.multiplier.toFixed(2)}`, "warn")
            : null,
      }),
      h(
        "div",
        { class: "stack" },
        ...weekly.objectives.map((obj) =>
          h(
            "div",
            { class: "stack stack--tight" },
            h(
              "div",
              { class: "row row--between small" },
              h("span", {}, `${obj.emoji} ${obj.name}`),
              obj.claimed
                ? ui.pill("Claimed", "ok")
                : h("span", { class: "dim tabular" }, `${fmt.compact(obj.progress)} / ${fmt.compact(obj.target)}`)
            ),
            ui.bar(obj.progress, obj.target, { success: obj.claimed }),
            h(
              "div",
              { class: "tiny dim" },
              `${obj.description} · pays ${fmt.num(obj.reward)}`
            )
          )
        )
      ),
      h(
        "p",
        { class: "tiny dim", style: { marginTop: "var(--s-3)", marginBottom: 0 } },
        "Progress is measured from where you were when the week started, and the " +
          "reward is claimed automatically the moment you finish."
      )
    );
  }

  /* ── Titles ───────────────────────────────────────────────────────────── */
  function titlesCard() {
    if (!state.titles.length) {
      return ui.card(
        {},
        ui.cardHead("Titles", { glyph: "🏷️" }),
        h("p", { class: "small dim" }, "Earn achievements that carry a title and they'll show up here.")
      );
    }
    return ui.card(
      {},
      ui.cardHead("Titles", { glyph: "🏷️", sub: "Shown on your profile card" }),
      h(
        "div",
        { class: "row row--wrap" },
        ...state.titles.map((title) =>
          h(
            "button",
            {
              class: "chip",
              type: "button",
              "aria-pressed": String(title.selected),
              onClick: () => pickTitle(title.selected ? "" : title.name),
            },
            title.name
          )
        ),
        state.selected_title
          ? h(
              "button",
              { class: "chip", type: "button", onClick: () => pickTitle("") },
              "Clear"
            )
          : null
      )
    );
  }

  async function pickTitle(name) {
    await api.post(`${base}/title`, { title: name });
    ui.ok(name ? `Wearing “${name}”.` : "Title cleared.");
    await refresh();
  }

  /* ── The case ─────────────────────────────────────────────────────────── */
  function caseCard() {
    const rows = state.achievements.filter((entry) => {
      if (filter !== "all" && entry.category !== filter) return false;
      if (!showEarned && entry.earned) return false;
      return true;
    });

    return ui.card(
      {},
      ui.cardHead("Achievements", {
        glyph: "🏆",
        sub: `${fmt.num(state.earned_count)} / ${fmt.num(state.total_count)}`,
        action: h(
          "a",
          {
            class: "btn btn--sm btn--ghost",
            href: `/g/${guildId}/profile?card=trophies`,
          },
          "Trophy case"
        ),
      }),
      ui.bar(state.earned_count, state.total_count, { success: state.earned_count === state.total_count }),

      h(
        "div",
        { class: "scroller", style: { marginTop: "var(--s-3)" } },
        chip("All", filter === "all", () => setFilter("all")),
        ...state.categories.map((cat) =>
          chip(`${cat.key} ${cat.earned}/${cat.total}`, filter === cat.key, () => setFilter(cat.key))
        )
      ),
      h(
        "label",
        { class: "row small", style: { gap: "var(--s-2)", marginBottom: "var(--s-3)" } },
        h("input", {
          type: "checkbox",
          checked: showEarned,
          onChange: (event) => {
            showEarned = event.target.checked;
            paint();
          },
        }),
        "Show ones I've earned"
      ),

      h(
        "div",
        { class: "grid grid--wide" },
        ...rows.map((entry) =>
          h(
            "div",
            {
              class: `trophy${entry.earned ? "" : " trophy--locked"}`,
              style: { "--accent": entry.accent },
            },
            h("span", { class: "trophy__glyph", "aria-hidden": "true" }, entry.emoji),
            h(
              "div",
              { class: "grow" },
              h("div", { class: "trophy__name" }, entry.name),
              h("div", { class: "tiny dim" }, entry.description),
              entry.earned
                ? h(
                    "div",
                    { class: "tiny muted" },
                    entry.earned_at ? `Earned ${fmt.relative(entry.earned_at)}` : "Earned"
                  )
                : h(
                    "div",
                    { class: "stack stack--tight", style: { marginTop: "6px" } },
                    ui.bar(entry.value, entry.threshold, { thin: true }),
                    h(
                      "div",
                      { class: "tiny dim tabular" },
                      `${fmt.compact(entry.value)} / ${fmt.compact(entry.threshold)}`
                    )
                  )
            ),
            h("span", { class: "tiny dim tabular" }, `${entry.points}p`)
          )
        )
      ),
      rows.length ? null : ui.empty("Nothing here", "Try another category.", { glyph: "🔍" })
    );
  }

  const chip = (label, active, onClick) =>
    h("button", { class: "chip", type: "button", "aria-pressed": String(active), onClick }, label);

  function setFilter(value) {
    filter = value;
    router.setQuery("category", value === "all" ? null : value);
    paint();
  }

  /* ── Prestige ─────────────────────────────────────────────────────────── */
  function prestigeCard() {
    const p = state.prestige;
    return ui.card(
      { class: p.next.ready ? "card--accent" : "" },
      ui.cardHead("Prestige", {
        glyph: "⭐",
        sub: p.rank ? `Rank ${p.rank} · ${p.title}` : "Not yet prestiged",
      }),
      h(
        "p",
        { class: "small muted" },
        "Prestige is the endgame coin sink. It costs points and coins, keeps " +
          "everything you've earned, and adds a permanent bonus to weekly " +
          "objective payouts."
      ),
      h(
        "div",
        { class: "grid grid--2" },
        ui.statTile("Points", `${fmt.num(p.next.have_points)} / ${fmt.num(p.next.points)}`),
        ui.statTile("Coins", `${fmt.compact(p.next.have_coins)} / ${fmt.compact(p.next.coins)}`)
      ),
      h(
        "div",
        { style: { marginTop: "var(--s-3)" } },
        p.next.ready
          ? ui.actionButton(`Prestige to rank ${p.rank + 1}`, confirmPrestige, {
              variant: "primary",
              block: true,
              glyph: "⭐",
            })
          : h("p", { class: "small dim", style: { marginBottom: 0 } }, p.next.reason)
      ),
      p.multiplier > 1
        ? h(
            "div",
            { class: "tiny dim", style: { marginTop: "var(--s-2)" } },
            `Your weekly objectives currently pay ×${p.multiplier.toFixed(2)}.`
          )
        : null
    );
  }

  async function confirmPrestige() {
    const p = state.prestige;
    const yes = await ui.confirm(
      `Prestige to rank ${p.rank + 1}?`,
      `This spends ${fmt.num(p.next.coins)} coins. You keep every achievement, ` +
        "item and level — prestige adds a rank, a title, and a permanent bonus " +
        "on weekly objective rewards. It can't be undone.",
      { confirmLabel: "Prestige" }
    );
    if (!yes) return;
    const result = await api.post(`${base}/prestige`);
    ui.ok(`You're now ${result.title}.`, { title: "⭐ Prestige", ms: 7000 });
    await refresh();
  }

  paint();
  return host;
}
