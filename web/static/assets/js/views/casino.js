/**
 * views/casino.js
 * The five games, playable.
 *
 * One bet control shared by all of them, because the bet is the only thing they
 * have in common and it is the thing you change most: a stepper with the
 * server's own min/max, quick multipliers, and an all-in that respects the
 * table limit. Changing game keeps your stake.
 *
 * The animations are deliberately cheap — CSS transforms on three reel strips,
 * a coin that flips, a wheel that spins to a computed angle. No canvas, no
 * per-frame JavaScript, nothing that a mid-range phone has to think about. The
 * *result is already decided by the server* before anything moves; the
 * animation is showing you an outcome, not computing one, which is why it can
 * be skipped by `prefers-reduced-motion` without changing a thing.
 *
 * Blackjack is the one with state, and it shares the `casino_blackjack` table
 * with Discord — so a hand dealt in chat shows up here as a hand to finish.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

const SPIN_MS = 900;

export async function casino({ guildId }) {
  const base = `/api/guilds/${guildId}/casino`;
  let state = await api.get(base);

  if (!state.enabled) {
    return ui.empty(
      "The casino is closed here",
      "An admin can open it from Settings → Games, or with /casino toggle in Discord.",
      { glyph: "🎰" }
    );
  }

  const host = h("div", { class: "stack" });
  let game = router.query("game", "flip");
  let bet = clampBet(Math.max(state.limits.min, Math.min(100, state.limits.max)));
  let side = "heads";
  let space = "red";
  let result = null;
  let hand = state.open_hand;

  function clampBet(value) {
    return Math.max(state.limits.min, Math.min(state.limits.max, Math.round(value) || state.limits.min));
  }

  async function refresh() {
    state = await api.get(base, { fresh: true });
    hand = state.open_hand;
    paint();
  }

  function paint() {
    const spec = state.games.find((g) => g.key === game) || state.games[0];
    fill(
      host,
      header(),
      !store.state.playEnabled
        ? ui.banner(
            "Playing from the web is switched off on this instance. Everything still works in Discord.",
            { kind: "warn", glyph: "🔒" }
          )
        : null,

      hand ? openHandCard() : null,

      h(
        "div",
        { class: "scroller" },
        ...state.games.map((entry) =>
          h(
            "button",
            {
              class: "chip",
              type: "button",
              "aria-pressed": String(entry.key === game),
              onClick: () => {
                game = entry.key;
                result = null;
                router.setQuery("game", entry.key === "flip" ? null : entry.key);
                paint();
              },
            },
            h("span", { "aria-hidden": "true" }, entry.emoji),
            entry.name
          )
        )
      ),

      ui.card(
        {},
        ui.cardHead(`${spec.emoji}  ${spec.name}`, { sub: spec.blurb }),
        stage(spec),
        spec.needs === "side" ? sidePicker() : null,
        spec.needs === "space" ? spacePicker() : null,
        betControl(),
        playButton(spec)
      ),

      result ? resultCard() : null,

      h("div", { class: "grid grid--2" }, jackpotCard(), statsCard()),
      challengesCard()
    );
  }

  function header() {
    return h(
      "div",
      { class: "row row--between" },
      h(
        "div",
        {},
        h("h1", {}, "Casino"),
        h(
          "div",
          { class: "small dim" },
          `Bets ${fmt.num(state.limits.min)}–${fmt.num(state.limits.max)}`
        )
      ),
      h(
        "div",
        { class: "row" },
        state.stats.streak >= state.streak.min
          ? ui.pill(`🔥 ${state.stats.streak} wins`, "warn")
          : null,
        ui.pill(`${state.currency.emoji} ${fmt.compact(state.balance)}`, "brand")
      )
    );
  }

  /* ── The bet ──────────────────────────────────────────────────────────── */
  function betControl() {
    const input = h("input", {
      class: "input tabular",
      type: "number",
      min: String(state.limits.min),
      max: String(state.limits.max),
      value: String(bet),
      "aria-label": "Bet",
      onChange: () => {
        bet = clampBet(Number(input.value));
        input.value = String(bet);
      },
    });

    const quick = (label, next) =>
      h(
        "button",
        {
          class: "chip",
          type: "button",
          onClick: () => {
            bet = clampBet(next());
            input.value = String(bet);
          },
        },
        label
      );

    return h(
      "div",
      { class: "stack stack--tight", style: { marginTop: "var(--s-3)" } },
      ui.field("Your bet", input, {
        hint:
          state.balance < bet
            ? `You only have ${fmt.num(state.balance)}.`
            : `You have ${fmt.num(state.balance)}.`,
      }),
      h(
        "div",
        { class: "row row--wrap" },
        quick("−", () => bet / 2),
        quick("+", () => bet * 2),
        quick("Min", () => state.limits.min),
        quick("Max", () => Math.min(state.limits.max, state.balance || state.limits.min)),
        // All-in respects the table limit, so it can't produce a bet the server
        // would then refuse.
        quick("All in", () => Math.min(state.balance, state.limits.max))
      )
    );
  }

  function sidePicker() {
    return h(
      "div",
      { class: "row", style: { justifyContent: "center", marginTop: "var(--s-3)" } },
      ui.segmented(
        [
          { value: "heads", label: "Heads" },
          { value: "tails", label: "Tails" },
        ],
        side,
        (value) => {
          side = value;
          paint();
        },
        { label: "Call it" }
      )
    );
  }

  function spacePicker() {
    const number = h("input", {
      class: "input tabular",
      type: "number",
      min: "0",
      max: "36",
      placeholder: "0–36",
      value: /^\d+$/.test(space) ? space : "",
      onInput: () => {
        if (number.value !== "") space = String(Math.max(0, Math.min(36, Number(number.value))));
        paintChips();
      },
    });
    const chips = h("div", { class: "row row--wrap" });

    function paintChips() {
      fill(
        chips,
        ...state.roulette_spaces.map((key) =>
          h(
            "button",
            {
              class: "chip",
              type: "button",
              "aria-pressed": String(space === key),
              onClick: () => {
                space = key;
                number.value = "";
                paintChips();
              },
            },
            key
          )
        )
      );
    }
    paintChips();

    return h(
      "div",
      { class: "stack stack--tight", style: { marginTop: "var(--s-3)" } },
      h("div", { class: "small muted" }, "Outside bets pay 2x. A single number pays 35x."),
      chips,
      ui.field("Or an exact number", number)
    );
  }

  /* ── The stage: whatever the game looks like mid-play ──────────────────── */
  const stageHost = h("div", { class: "casino-stage" });

  function stage(spec) {
    if (game === "blackjack") {
      fill(
        stageHost,
        h(
          "p",
          { class: "muted center" },
          hand
            ? "You've got a hand open — finish it below."
            : "Two cards each. Dealer stands on 17, a natural pays 3:2."
        )
      );
      return stageHost;
    }
    if (!result || result.game !== game) {
      fill(stageHost, idleStage(spec));
    }
    return stageHost;
  }

  function idleStage(spec) {
    if (game === "slots") return reels(["🍒", "🔔", "7️⃣"], false);
    if (game === "flip") return h("div", { class: "coin" }, side === "heads" ? "🪙" : "🪙");
    if (game === "roulette") return wheel(null);
    if (game === "dice") return h("div", { class: "dice-row" }, die(1), die(1));
    return h("div", { class: "center", style: { fontSize: "3rem" } }, spec.emoji);
  }

  const reels = (symbols, spinning) =>
    h(
      "div",
      { class: `reels${spinning ? " reels--spinning" : ""}` },
      ...symbols.map((symbol, i) =>
        h("div", { class: "reel", style: { animationDelay: `${i * 120}ms` } }, symbol)
      )
    );

  const die = (value) =>
    h("div", { class: "die" }, ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"][Math.max(0, value - 1)]);

  function wheel(number) {
    return h(
      "div",
      { class: "wheel" },
      h(
        "div",
        { class: "wheel__face" },
        number === null ? "🎡" : String(number)
      )
    );
  }

  function playButton(spec) {
    const affordable = state.balance >= bet;
    return h(
      "div",
      { style: { marginTop: "var(--s-4)" } },
      hand && game === "blackjack"
        ? h("p", { class: "small dim center" }, "Finish the open hand first.")
        : ui.actionButton(
            affordable ? `Play for ${fmt.num(bet)}` : `You need ${fmt.num(bet - state.balance)} more`,
            () => play(),
            { variant: "hero", block: true, glyph: spec.emoji, disabled: !affordable }
          )
    );
  }

  /* ── Playing ──────────────────────────────────────────────────────────── */
  async function play() {
    const body = { bet };
    if (game === "flip") body.side = side;
    if (game === "roulette") body.space = space;

    const outcome = await api.post(`${base}/play/${game}`, body);

    if (game === "blackjack" && outcome.state === "playing") {
      hand = outcome;
      state.balance = outcome.balance;
      result = null;
      paint();
      return;
    }

    result = outcome;
    state.balance = outcome.balance;
    state.stats = { ...state.stats, ...(outcome.stats || {}) };
    await animate(outcome);
    paint();
    announce(outcome);
    // The jackpot and the challenge rows both moved; re-read quietly so the
    // cards below are right without blocking the result the player is reading.
    // The repaint is the point — storing the fresh state without painting it
    // left the jackpot and the challenge progress showing pre-bet numbers.
    // `result` is kept in scope, so the outcome the player is reading survives.
    api.get(base, { fresh: true }).then((fresh) => {
      state = { ...fresh };
      hand = fresh.open_hand;
      paint();
    });
  }

  /** Show the outcome. Skipped wholesale under reduced motion. */
  async function animate(outcome) {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;
    if (outcome.game === "slots") {
      fill(stageHost, reels(["❔", "❔", "❔"], true));
    } else if (outcome.game === "roulette") {
      const spinner = wheel("…");
      spinner.classList.add("wheel--spinning");
      fill(stageHost, spinner);
    } else if (outcome.game === "flip") {
      const coin = h("div", { class: "coin coin--flipping" }, "🪙");
      fill(stageHost, coin);
    } else if (outcome.game === "dice") {
      fill(stageHost, h("div", { class: "dice-row dice-row--rolling" }, die(1), die(1)));
    }
    await new Promise((resolve) => setTimeout(resolve, SPIN_MS));
  }

  function announce(outcome) {
    if (outcome.jackpot) {
      ui.ok(`The progressive jackpot paid out ${fmt.coins(outcome.jackpot, state.currency)}!`, {
        title: "💰 JACKPOT",
        ms: 9000,
      });
    }
    for (const chal of outcome.challenges || []) {
      if (chal.completed_now) {
        ui.ok(`${chal.label} — ${fmt.coins(chal.reward, state.currency)}`, {
          title: "🏆 Challenge complete",
        });
      }
    }
  }

  /* ── Results ──────────────────────────────────────────────────────────── */
  function resultCard() {
    const won = result.net > 0;
    const push = result.net === 0;
    fill(stageHost, resultStage());

    return ui.card(
      { class: won ? "card--accent" : "" },
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h2", {}, won ? "You won" : push ? "Push" : "You lost"),
          h("div", { class: "small dim" }, resultLine())
        ),
        h(
          "strong",
          {
            class: "tabular",
            style: { fontSize: "1.5rem", color: won ? "var(--success)" : push ? "inherit" : "var(--danger)" },
          },
          `${result.net >= 0 ? "+" : ""}${fmt.num(result.net)}`
        )
      ),
      result.streak_bonus
        ? h(
            "p",
            { class: "small", style: { marginTop: "var(--s-2)" } },
            `🔥 ${result.streak}-win streak — your payout was boosted ${fmt.pct(result.streak_bonus, 0)}.`
          )
        : null
    );
  }

  function resultStage() {
    if (result.game === "slots") return reels(result.reels, false);
    if (result.game === "flip") return h("div", { class: "coin" }, result.result === "heads" ? "🪙" : "🪙");
    if (result.game === "roulette") {
      const node = wheel(result.number);
      node.dataset.color = result.color;
      return node;
    }
    if (result.game === "dice") {
      return h(
        "div",
        { class: "stack stack--tight" },
        h("div", { class: "dice-row" }, ...result.player_rolls.map(die)),
        h("div", { class: "small dim center" }, "against"),
        h("div", { class: "dice-row" }, ...result.dealer_rolls.map(die))
      );
    }
    return cardsStage(result);
  }

  function resultLine() {
    if (result.game === "flip") return `It landed on ${result.result} — you called ${result.choice}.`;
    if (result.game === "dice") return `You rolled ${result.player_total}, the dealer ${result.dealer_total}.`;
    if (result.game === "slots") return result.reels.join(" ");
    if (result.game === "roulette") return `${result.number} ${result.color} — you bet ${result.space}.`;
    return `${result.outcome} · you ${result.player.total}, dealer ${result.dealer.total}`;
  }

  /* ── Blackjack ────────────────────────────────────────────────────────── */
  function openHandCard() {
    return ui.card(
      { class: "card--accent" },
      ui.cardHead("Blackjack", {
        glyph: "🃏",
        sub: hand.from_discord
          ? "Dealt in Discord — finish it here or there, it's the same hand."
          : `Betting ${fmt.num(hand.bet)}`,
      }),
      cardsStage(hand),
      h(
        "div",
        { class: "btn-group", style: { marginTop: "var(--s-3)" } },
        ui.actionButton("Hit", () => move("hit"), { variant: "primary", glyph: "➕" }),
        ui.actionButton("Stand", () => move("stand"), { glyph: "✋" })
      )
    );
  }

  async function move(action) {
    const outcome = await api.post(`${base}/blackjack/${hand.hand_id}`, { action });
    if (outcome.state === "playing") {
      hand = { ...hand, ...outcome };
      paint();
      return;
    }
    hand = null;
    result = outcome;
    state.balance = outcome.balance;
    paint();
    announce(outcome);
    refresh();
  }

  function cardsStage(data) {
    const row = (label, side) =>
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "row row--between small" },
          h("span", { class: "muted" }, label),
          h("span", { class: "tabular" }, side.hidden ? `${side.total} + ?` : String(side.total))
        ),
        h(
          "div",
          { class: "hand" },
          ...side.cards.map((card, index) =>
            card.hidden
              ? h("div", { class: "playing-card playing-card--back" }, "🂠")
              : h(
                  "div",
                  {
                    class: `playing-card${card.red ? " playing-card--red" : ""}`,
                    style: { animationDelay: `${index * 90}ms` },
                  },
                  card.label
                )
          )
        )
      );
    return h(
      "div",
      { class: "stack" },
      row("Dealer", data.dealer),
      row("You", data.player)
    );
  }

  /* ── Side cards ───────────────────────────────────────────────────────── */
  const jackpotCard = () =>
    ui.card(
      {},
      ui.cardHead("Progressive jackpot", { glyph: "💰" }),
      ui.stat("Pool", `${state.currency.emoji} ${fmt.compact(state.jackpot)}`, { large: true }),
      h(
        "p",
        { class: "tiny dim", style: { marginBottom: 0 } },
        "Fed by a fifth of every net loss in this server. Triple 7️⃣ on the slots takes the lot."
      )
    );

  const statsCard = () =>
    ui.card(
      {},
      ui.cardHead("Your record", { glyph: "📊" }),
      h(
        "div",
        { class: "stack stack--tight small" },
        line("Games", fmt.num(state.stats.games)),
        line("Wagered", fmt.compact(state.stats.wagered)),
        line("Net", fmt.signed(state.stats.net)),
        line("Biggest win", fmt.compact(state.stats.biggest_win)),
        line("Best streak", fmt.num(state.stats.best_streak)),
        state.stats.rank ? line("Rank", `#${fmt.num(state.stats.rank)}`) : null
      )
    );

  const line = (label, value) =>
    h(
      "div",
      { class: "row row--between" },
      h("span", { class: "muted" }, label),
      h("span", { class: "tabular" }, value)
    );

  function challengesCard() {
    return ui.card(
      {},
      ui.cardHead("Today's challenges", { glyph: "🏆", sub: "Two a day, claimed automatically" }),
      h(
        "div",
        { class: "stack" },
        ...state.challenges.map((chal) =>
          h(
            "div",
            { class: "stack stack--tight" },
            h(
              "div",
              { class: "row row--between small" },
              h("span", {}, chal.label),
              chal.claimed
                ? ui.pill("Claimed", "ok")
                : h("span", { class: "dim tabular" }, `${fmt.compact(chal.progress)} / ${fmt.compact(chal.target)}`)
            ),
            ui.bar(chal.progress, chal.target, { success: chal.claimed })
          )
        )
      )
    );
  }

  paint();
  return host;
}
