/**
 * core/chart.js
 * Small SVG charts, hand-drawn.
 *
 * No charting library, for the same reasons the rest of the frontend has no
 * framework: the CSP forbids an external script host, there is no build step to
 * bundle one, and what is actually needed here is a line, some bars and a
 * horizontal bar list. That is about a hundred lines of path arithmetic.
 *
 * Three rules make them readable on a phone, which is the hard case:
 *
 *   **Sized by viewBox, never by pixels.** The SVG scales to its column, so the
 *   same chart works at 320px and at 900px without a resize observer.
 *   **Sparse labels.** At most four x labels, chosen by index, because a dense
 *   axis is illegible below about 500px and useless above it.
 *   **A real empty state.** A flat line at zero looks like data. "Nothing yet"
 *   is the truth and takes the same space.
 *
 * Colour never carries meaning on its own: every series has a label, and values
 * are printed next to the bars rather than left to be read off an axis.
 */

import { h } from "./dom.js";
import * as fmt from "./format.js";

const W = 320;
const HEIGHT = 110;
const PAD = { top: 8, right: 4, bottom: 16, left: 4 };

const svg = (tag, attrs = {}, ...children) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) el.setAttribute(key, String(value));
  }
  for (const child of children.flat()) if (child) el.appendChild(child);
  return el;
};

const empty = (message) => h("div", { class: "chart-empty" }, message);

/** Pick at most `count` evenly spaced indexes — the sparse-axis rule. */
function ticks(length, count = 4) {
  if (length <= count) return [...Array(length).keys()];
  const step = (length - 1) / (count - 1);
  return Array.from({ length: count }, (_, i) => Math.round(i * step));
}

const label = (t, bucketSeconds) =>
  new Date(t * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(bucketSeconds > 86400 ? {} : {}),
  });

/**
 * A time series as a filled line.
 *
 * `points` is `[{t, value}]` from the API — already bucketed server-side, which
 * is what keeps this function from having to know anything about dates beyond
 * how to write one under an axis.
 */
export function lineChart(points, { bucket = 86400, format = fmt.compact } = {}) {
  const clean = (points || []).filter((p) => p && Number.isFinite(p.value));
  if (!clean.length || clean.every((p) => p.value === 0)) {
    return empty("Nothing in this period yet");
  }

  const max = Math.max(...clean.map((p) => p.value), 1);
  const innerW = W - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const x = (i) => PAD.left + (clean.length === 1 ? innerW / 2 : (i / (clean.length - 1)) * innerW);
  const y = (value) => PAD.top + innerH - (value / max) * innerH;

  const line = clean.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(clean.length - 1).toFixed(1)},${PAD.top + innerH} L${x(0).toFixed(1)},${PAD.top + innerH} Z`;

  const node = svg(
    "svg",
    {
      class: "chart",
      viewBox: `0 0 ${W} ${HEIGHT}`,
      preserveAspectRatio: "none",
      role: "img",
      "aria-label": `Chart peaking at ${format(max)}`,
    },
    svg("path", { class: "chart__area", d: area }),
    svg("path", { class: "chart__line", d: line }),
    ...ticks(clean.length).map((i) =>
      svg(
        "text",
        {
          class: "chart__label",
          x: x(i),
          y: HEIGHT - 4,
          "text-anchor": i === 0 ? "start" : i === clean.length - 1 ? "end" : "middle",
        },
        document.createTextNode(label(clean[i].t, bucket))
      )
    )
  );
  // preserveAspectRatio="none" would squash the text; scale the SVG box
  // instead by letting CSS set the width and the viewBox set the ratio.
  node.removeAttribute("preserveAspectRatio");
  return node;
}

/** A categorical distribution, as vertical bars. */
export function barChart(rows, { format = fmt.compact } = {}) {
  const clean = (rows || []).filter(Boolean);
  if (!clean.length || clean.every((r) => !r.value)) return empty("No data yet");

  const max = Math.max(...clean.map((r) => r.value), 1);
  const innerW = W - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const slot = innerW / clean.length;
  const width = Math.max(4, slot * 0.62);

  return svg(
    "svg",
    {
      class: "chart",
      viewBox: `0 0 ${W} ${HEIGHT}`,
      role: "img",
      "aria-label": clean.map((r) => `${r.label}: ${format(r.value)}`).join(", "),
    },
    ...clean.flatMap((row, i) => {
      const height = (row.value / max) * innerH;
      const x = PAD.left + i * slot + (slot - width) / 2;
      return [
        svg("rect", {
          class: "chart__bar",
          x,
          y: PAD.top + innerH - height,
          width,
          height: Math.max(row.value ? 2 : 0, height),
          rx: 3,
        }),
        svg(
          "text",
          {
            class: "chart__label",
            x: x + width / 2,
            y: HEIGHT - 4,
            "text-anchor": "middle",
          },
          document.createTextNode(row.label)
        ),
      ];
    })
  );
}

/**
 * A horizontal bar list — the readable form when labels are words rather than
 * dates, and the one that survives a narrow screen without rotating text.
 */
export function barList(rows, { format = fmt.compact, of = null } = {}) {
  const clean = (rows || []).filter(Boolean);
  if (!clean.length) return empty("No data yet");
  const max = of || Math.max(...clean.map((r) => r.value), 1);
  return h(
    "div",
    { class: "stack stack--tight" },
    ...clean.map((row) =>
      h(
        "div",
        { class: "hbar" },
        h(
          "span",
          { class: "hbar__label" },
          row.emoji ? `${row.emoji} ${row.label}` : row.label
        ),
        h(
          "div",
          {
            class: "hbar__track",
            role: "progressbar",
            "aria-valuenow": row.value,
            "aria-valuemax": max,
            "aria-label": row.label,
          },
          h("div", {
            class: "hbar__fill",
            style: { width: `${Math.min(100, (row.value / max) * 100)}%` },
          })
        ),
        h("span", { class: "hbar__value" }, format(row.value))
      )
    )
  );
}
