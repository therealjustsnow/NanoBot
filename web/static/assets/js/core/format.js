/**
 * core/format.js
 * Numbers, durations and coins, formatted the way the bot formats them.
 *
 * `duration` mirrors `utils/helpers.fmt_duration`, and `coins` mirrors
 * `fmt_coins`, because a cooldown that reads "2m 30s" in Discord and "150
 * seconds" on the site is two products. Where the bot has an opinion, this
 * file copies it rather than having its own.
 */

const nf = new Intl.NumberFormat();
const nfCompact = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

export const num = (value) => nf.format(Math.round(Number(value) || 0));

/** Compact form for cramped places — 12.4K rather than 12,400. */
export const compact = (value) => {
  const n = Number(value) || 0;
  return Math.abs(n) < 10000 ? nf.format(Math.round(n)) : nfCompact.format(n);
};

export const signed = (value) => (Number(value) > 0 ? `+${num(value)}` : num(value));

export const pct = (value, digits = 0) =>
  `${((Number(value) || 0) * 100).toFixed(digits)}%`;

/** Coins with the guild's own currency emoji and plural name. */
export function coins(amount, currency) {
  const emoji = currency?.emoji || "🪙";
  const name = currency?.name || "NanoCoin";
  const n = Math.round(Number(amount) || 0);
  return `${emoji} ${nf.format(n)} ${name}${Math.abs(n) === 1 ? "" : "s"}`;
}

/** Just the number and the emoji — for a tight row where the name won't fit. */
export function coinsShort(amount, currency) {
  return `${currency?.emoji || "🪙"} ${compact(amount)}`;
}

/**
 * "2h 30m", "45s", "3d 4h" — at most two units, largest first.
 * Matches the bot's own duration wording.
 */
export function duration(seconds) {
  let s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 1) return "now";
  const units = [
    ["d", 86400],
    ["h", 3600],
    ["m", 60],
    ["s", 1],
  ];
  const parts = [];
  for (const [label, size] of units) {
    if (s >= size) {
      const count = Math.floor(s / size);
      s -= count * size;
      parts.push(`${count}${label}`);
      if (parts.length === 2) break;
    }
  }
  return parts.join(" ");
}

/** A countdown for a live timer: mm:ss under an hour, else the duration form. */
export function clock(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s >= 3600) return duration(s);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** "3 days ago" / "in 4 hours", from a unix timestamp in seconds. */
export function relative(timestamp) {
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const delta = (Number(timestamp) || 0) * 1000 - Date.now();
  const units = [
    ["year", 31536e6],
    ["month", 2592e6],
    ["week", 6048e5],
    ["day", 864e5],
    ["hour", 36e5],
    ["minute", 6e4],
    ["second", 1e3],
  ];
  for (const [unit, ms] of units) {
    if (Math.abs(delta) >= ms || unit === "second") {
      return rtf.format(Math.round(delta / ms), unit);
    }
  }
  return "just now";
}

export const date = (timestamp) =>
  new Date((Number(timestamp) || 0) * 1000).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

/** Weight, matching `cogs/fishing/helpers.fmt_weight`. */
export const weight = (kg) => {
  const n = Number(kg) || 0;
  return n < 1 ? `${Math.round(n * 1000)}g` : `${n.toFixed(2)}kg`;
};

export const plural = (count, one, many = `${one}s`) =>
  `${num(count)} ${Math.abs(Number(count)) === 1 ? one : many}`;

/**
 * "about 3 hours of fishing" — the /shop yardstick, in words.
 * The server computes the seconds (from the same balance model Discord uses);
 * this only chooses how to say it.
 */
export function timeToEarn(seconds) {
  const s = Number(seconds) || 0;
  if (s <= 0) return "instantly";
  if (s < 3600) return `about ${Math.max(1, Math.round(s / 60))} min of fishing`;
  if (s < 86400) return `about ${Math.round(s / 3600)}h of fishing`;
  return `about ${Math.round(s / 86400)} days of fishing`;
}
