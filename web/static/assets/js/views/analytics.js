/**
 * views/analytics.js
 * Charts that answer a question, and a note saying what they can't.
 *
 * The server decides what's worth plotting (see `web/engine/analytics.py`); this
 * page's job is to make each chart *legible on a phone* and to keep the honest
 * caveats attached rather than in a footnote nobody reads. Every chart carries
 * a one-line "why this matters" under it, because a number without a decision
 * attached is decoration.
 *
 * The wealth section is a distribution, not a trend, and says so. NanoBot keeps
 * lifetime totals rather than daily snapshots, so a wealth-over-time line would
 * have to be invented — and a chart with made-up history is worse than no chart.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as chart from "../core/chart.js";
import * as fmt from "../core/format.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

export async function analytics({ guildId }) {
  const base = `/api/guilds/${guildId}/analytics`;
  let range = router.query("range", "30d");
  let state = await api.get(`${base}?range=${range}`);
  const host = h("div", { class: "stack" });

  async function load(next) {
    range = next;
    router.setQuery("range", next === "30d" ? null : next);
    state = await api.get(`${base}?range=${range}`, { fresh: true });
    paint();
  }

  function paint() {
    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h1", {}, "Analytics"),
          h("div", { class: "small dim" }, `Updated ${fmt.relative(state.generated_at)}`)
        ),
        ui.segmented(
          state.ranges.map((r) => ({ value: r.key, label: r.label })),
          state.range.key,
          load,
          { label: "Time range" }
        )
      ),

      economyCard(),
      participationCard(),
      h("div", { class: "grid grid--2" }, ...chartCards()),
      moderationCard(),

      ...state.notes.map((note) => ui.banner(note, { kind: "info", glyph: "ℹ️" }))
    );
  }

  /* ── Economy ──────────────────────────────────────────────────────────── */
  function economyCard() {
    const e = state.economy;
    return ui.card(
      {},
      ui.cardHead("Where the money is", {
        glyph: "🪙",
        sub: `${fmt.compact(e.total_coins)} ${state.currency.name}s across ${fmt.plural(e.holders, "member")}`,
      }),
      h(
        "div",
        { class: "grid grid--2" },
        ui.statTile("Median", fmt.compact(e.median), { sub: "the typical member" }),
        ui.statTile("Mean", fmt.compact(e.mean), { sub: "pulled up by the top" }),
        ui.statTile("Top 10% hold", fmt.pct(e.top_share, 0), {
          sub: e.top_share > 0.6 ? "very concentrated" : "reasonably spread",
        }),
        ui.statTile("Have any", `${fmt.num(e.holders)} / ${fmt.num(e.members)}`, {
          sub: "of your members",
        })
      ),
      h(
        "div",
        { style: { marginTop: "var(--s-4)" } },
        h("div", { class: "small muted", style: { marginBottom: "var(--s-2)" } }, "How wealth is spread"),
        chart.barChart(e.distribution)
      ),
      h(
        "div",
        { style: { marginTop: "var(--s-4)" } },
        h(
          "div",
          { class: "small muted", style: { marginBottom: "var(--s-2)" } },
          "What an active member can earn"
        ),
        chart.barList(e.faucets),
        h(
          "p",
          { class: "tiny dim", style: { marginTop: "var(--s-2)", marginBottom: 0 } },
          "Recomputed from the game's own drop tables, so it moves when the " +
            "balance does. Price your shop against these rather than against a " +
            "number that felt right."
        )
      )
    );
  }

  /* ── Participation ────────────────────────────────────────────────────── */
  function participationCard() {
    const p = state.participation;
    return ui.card(
      {},
      ui.cardHead("Who's playing", {
        glyph: "👥",
        sub: `Out of ${fmt.plural(p.of, "member")}`,
      }),
      chart.barList(p.series, { of: p.of, format: fmt.num }),
      h(
        "p",
        { class: "tiny dim", style: { marginTop: "var(--s-3)", marginBottom: 0 } },
        "Lifetime, not this period — somebody who fished once last year still " +
          "counts. A feature nobody has ever touched is the one worth looking at."
      )
    );
  }

  /* ── Time series ──────────────────────────────────────────────────────── */
  function chartCards() {
    return Object.entries(state.charts).map(([key, spec]) =>
      ui.card(
        { key },
        ui.cardHead(spec.label, {
          glyph: { shop_spend: "🛒", catches: "🎣", warnings: "⚠️" }[key] || "📊",
          sub: `${fmt.compact(spec.total)} in ${state.range.label.toLowerCase()}`,
        }),
        chart.lineChart(spec.points, { bucket: state.range.bucket }),
        h("p", { class: "tiny dim", style: { marginTop: "var(--s-2)", marginBottom: 0 } }, spec.why)
      )
    );
  }

  /* ── Moderation ───────────────────────────────────────────────────────── */
  function moderationCard() {
    const m = state.moderation;
    return ui.card(
      {},
      ui.cardHead("Standing load", { glyph: "🛡️" }),
      h(
        "div",
        { class: "grid grid--2" },
        ui.statTile("Warnings", fmt.num(m.warnings), {
          sub: `${fmt.plural(m.warned_members, "member")} warned`,
        }),
        ui.statTile("Open tickets", fmt.num(m.open_tickets)),
        ui.statTile("Rewards to hand over", fmt.num(m.pending_rewards), {
          sub: m.pending_rewards ? "somebody is waiting" : "all clear",
        })
      ),
      m.pending_rewards
        ? h(
            "div",
            { style: { marginTop: "var(--s-3)" } },
            h(
              "a",
              { class: "btn btn--sm", href: `/g/${guildId}/settings/economy` },
              "Go and fulfil them"
            )
          )
        : null
    );
  }

  paint();
  return host;
}
