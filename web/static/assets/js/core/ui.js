/**
 * core/ui.js
 * The shared component vocabulary: cards, stats, buttons, toasts, sheets.
 *
 * Every screen builds from these rather than from raw `h("div")`, which is what
 * keeps eleven settings pages and six game pages looking like one product. If a
 * pattern appears twice, it belongs here.
 *
 * Two components carry most of the dashboard's UX promises:
 *
 *   `actionButton` — one press, one in-flight request, one visible result. It
 *   disables itself for the duration (so a double-tap can't double-cast),
 *   shows a spinner, and turns a refusal into a toast carrying the server's own
 *   sentence. Every mutating control on the site goes through it.
 *
 *   `autoField` — a setting that saves itself. It debounces, shows "Saving…"
 *   then "Saved", and rolls the control back to the server's value if the save
 *   is refused, so the screen never shows a state the database doesn't hold.
 */

import { h, fill } from "./dom.js";
import { ApiError } from "./api.js";
import * as fmt from "./format.js";

/* ── Toasts ─────────────────────────────────────────────────────────────── */
const HOST = () => document.getElementById("toasts");

export function toast(message, { kind = "ok", title = null, action = null, ms = 4200 } = {}) {
  const host = HOST();
  if (!host) return;
  const node = h(
    "div",
    { class: `toast toast--${kind}`, role: kind === "bad" ? "alert" : "status" },
    h(
      "div",
      { class: "toast__body" },
      title && h("div", { class: "toast__title" }, title),
      h("div", {}, message)
    ),
    action &&
      h(
        "button",
        {
          class: "toast__action",
          onClick: () => {
            action.onClick();
            node.remove();
          },
        },
        action.label
      )
  );
  host.appendChild(node);
  const timer = setTimeout(() => node.remove(), ms);
  node.addEventListener("click", (event) => {
    if (event.target.closest(".toast__action")) return;
    clearTimeout(timer);
    node.remove();
  });
  return node;
}

export const ok = (message, opts) => toast(message, { ...opts, kind: "ok" });
export const warn = (message, opts) => toast(message, { ...opts, kind: "warn" });
export const fail = (message, opts) => toast(message, { ...opts, kind: "bad" });

/** Report a thrown error using the server's own wording wherever there is one. */
export function reportError(error) {
  if (error?.name === "AbortError") return;
  const message =
    error instanceof ApiError ? error.message : "Something went wrong. Try again.";
  const kind = error instanceof ApiError && error.status === 409 ? "warn" : "bad";
  toast(message, { kind, ms: kind === "bad" ? 6000 : 4200 });
}

/* ── Primitives ─────────────────────────────────────────────────────────── */
export const card = (props, ...children) =>
  h("section", { ...props, class: ["card", props?.class].filter(Boolean).join(" ") }, ...children);

export function cardHead(title, { glyph = null, action = null, sub = null } = {}) {
  return h(
    "header",
    { class: "card__head" },
    h(
      "div",
      { class: "card__title" },
      glyph && h("span", { class: "card__glyph", "aria-hidden": "true" }, glyph),
      h("div", {}, h("h2", {}, title), sub && h("div", { class: "small dim" }, sub))
    ),
    action
  );
}

export function stat(label, value, { sub = null, large = false } = {}) {
  return h(
    "div",
    { class: `stat${large ? " stat--lg" : ""}` },
    h("div", { class: "stat__label" }, label),
    h("div", { class: "stat__value" }, value),
    sub && h("div", { class: "stat__sub" }, sub)
  );
}

export const statTile = (label, value, opts) =>
  h("div", { class: "tile" }, stat(label, value, opts));

export const pill = (text, kind = null) =>
  h("span", { class: `pill${kind ? ` pill--${kind}` : ""}` }, text);

export function bar(value, max, { success = false, thin = false } = {}) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return h(
    "div",
    {
      class: `bar${success ? " bar--success" : ""}${thin ? " bar--thin" : ""}`,
      role: "progressbar",
      "aria-valuenow": Math.round(value),
      "aria-valuemin": "0",
      "aria-valuemax": Math.round(max),
    },
    h("div", { class: "bar__fill", style: { width: `${percent}%` } })
  );
}

/** Level bar with its own label — used by both level systems and fishing. */
export function progressBlock(label, into, need, { extra = null, success = false } = {}) {
  return h(
    "div",
    { class: "stack stack--tight" },
    h(
      "div",
      { class: "row row--between small" },
      h("span", { class: "muted" }, label),
      h("span", { class: "dim tabular" }, extra ?? `${fmt.num(into)} / ${fmt.num(need)} XP`)
    ),
    bar(into, need, { success })
  );
}

export function empty(title, message, { glyph = "🫧", action = null } = {}) {
  return h(
    "div",
    { class: "empty" },
    h("span", { class: "empty__glyph", "aria-hidden": "true" }, glyph),
    h("div", { class: "empty__title" }, title),
    message && h("p", { class: "small" }, message),
    action
  );
}

export function banner(message, { kind = "info", glyph = "ℹ️", action = null } = {}) {
  return h(
    "div",
    { class: `banner banner--${kind}` },
    h("span", { class: "banner__glyph", "aria-hidden": "true" }, glyph),
    h("div", { class: "grow" }, message),
    action
  );
}

export const skeleton = (lines = 3) =>
  h(
    "div",
    { class: "card stack", "aria-busy": "true", "aria-label": "Loading" },
    h("div", { class: "skeleton skeleton--line", style: { width: "45%" } }),
    ...Array.from({ length: lines }, () => h("div", { class: "skeleton skeleton--line" }))
  );

export const loading = (cards = 2) =>
  h("div", { class: "stack" }, ...Array.from({ length: cards }, () => skeleton()));

/* ── Buttons ────────────────────────────────────────────────────────────── */
/**
 * A button that owns its own request.
 *
 * `onPress` is async. While it runs the button is disabled and shows a spinner,
 * which is the whole double-submit guard on the client side — the server's
 * atomic claim is the real one, but a button that stays pressable while a cast
 * is in flight *looks* broken even when the second press is correctly refused.
 */
export function actionButton(label, onPress, opts = {}) {
  const {
    variant = "",
    glyph = null,
    disabled = false,
    block = false,
    size = "",
    title = null,
    onDone = null,
  } = opts;
  const classes = [
    "btn",
    variant && `btn--${variant}`,
    block && "btn--block",
    size && `btn--${size}`,
  ]
    .filter(Boolean)
    .join(" ");

  const content = () =>
    [glyph && h("span", { "aria-hidden": "true" }, glyph), h("span", {}, label)].filter(Boolean);

  const button = h(
    "button",
    {
      class: classes,
      type: "button",
      disabled,
      title,
      onClick: async () => {
        if (button.disabled) return;
        button.disabled = true;
        const previous = [...button.childNodes];
        fill(button, h("span", { class: "btn__spinner", "aria-hidden": "true" }), h("span", {}, label));
        try {
          const result = await onPress();
          if (onDone) onDone(result);
        } catch (error) {
          reportError(error);
        } finally {
          if (button.isConnected) {
            fill(button, ...previous);
            button.disabled = disabled;
          }
        }
      },
    },
    ...content()
  );
  return button;
}

/** A group of mutually exclusive options rendered as a segmented control. */
export function segmented(options, current, onPick, { label = "View" } = {}) {
  return h(
    "div",
    { class: "segmented", role: "tablist", "aria-label": label },
    ...options.map((option) =>
      h(
        "button",
        {
          class: "segmented__item",
          role: "tab",
          type: "button",
          "aria-selected": String(option.value === current),
          onClick: () => option.value !== current && onPick(option.value),
        },
        option.label
      )
    )
  );
}

/* ── Form controls ──────────────────────────────────────────────────────── */
export function switchRow(name, why, checked, onChange, { disabled = false } = {}) {
  const input = h("input", {
    type: "checkbox",
    checked,
    disabled,
    onChange: () => onChange(input.checked, input),
  });
  return h(
    "label",
    { class: "setting" },
    h(
      "span",
      { class: "setting__text" },
      h("span", { class: "setting__name" }, name),
      why && h("span", { class: "setting__why" }, why)
    ),
    h(
      "span",
      { class: "switch setting__control" },
      input,
      h("span", { class: "switch__track", "aria-hidden": "true" })
    )
  );
}

export function field(label, control, { hint = null, error = null } = {}) {
  const id = `f-${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h(
    "div",
    { class: "field" },
    h("label", { class: "field__label", for: id }, label),
    control,
    hint && h("div", { class: "field__hint" }, hint),
    error && h("div", { class: "field__error" }, error)
  );
}

/**
 * A control that saves itself.
 *
 * Debounced so typing doesn't fire a request per keystroke, and — the part that
 * matters — it reverts to the server's value when a save is refused. Optimism
 * that survives a rejection is how a dashboard ends up showing a setting nobody
 * actually has.
 */
export function autoSave(control, save, { delay = 700, status = null } = {}) {
  let timer = null;
  let last = control.type === "checkbox" ? control.checked : control.value;

  const setStatus = (text, kind) => {
    if (!status) return;
    fill(status, text ? h("span", { class: `tiny ${kind || "dim"}` }, text) : "");
  };

  const commit = async () => {
    const value = control.type === "checkbox" ? control.checked : control.value;
    setStatus("Saving…");
    try {
      await save(value);
      last = value;
      setStatus("Saved", "muted");
      setTimeout(() => setStatus(""), 1600);
    } catch (error) {
      if (control.type === "checkbox") control.checked = last;
      else control.value = last;
      setStatus("");
      reportError(error);
    }
  };

  const schedule = (immediate) => {
    clearTimeout(timer);
    timer = setTimeout(commit, immediate ? 0 : delay);
  };

  control.addEventListener("input", () => schedule(false));
  control.addEventListener("change", () => schedule(control.type !== "text"));
  control.addEventListener("blur", () => timer && schedule(true));
  return control;
}

/* ── Sheet (modal) ──────────────────────────────────────────────────────── */
/**
 * A bottom sheet on a phone, a centred dialog on a desktop.
 *
 * Traps focus, closes on Escape and on a backdrop tap, and returns focus to
 * whatever opened it — the three things a hand-rolled modal usually forgets and
 * a keyboard user immediately notices.
 */
export function sheet(title, body, { actions = [], onClose = null } = {}) {
  const opener = document.activeElement;
  const panel = h(
    "div",
    { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "sheet__grip", "aria-hidden": "true" }),
    h(
      "header",
      { class: "sheet__head" },
      h("h2", { tabindex: "-1" }, title),
      h("button", { class: "btn btn--ghost btn--sm", type: "button", onClick: () => close(), "aria-label": "Close" }, "✕")
    ),
    h("div", { class: "sheet__body" }, body),
    actions.length ? h("footer", { class: "sheet__foot" }, ...actions) : null
  );
  const scrim = h(
    "div",
    {
      class: "scrim",
      onClick: (event) => event.target === scrim && close(),
    },
    panel
  );

  // An in-app link inside a sheet is handled by the router, which swaps the
  // view underneath without touching this dialog — leaving it stranded over the
  // new page with the body still scroll-locked. Close on the way out.
  panel.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link || link.target || link.hasAttribute("download")) return;
    const href = link.getAttribute("href") || "";
    if (!href.startsWith("/") || href.startsWith("//") || href.startsWith("/api/")) return;
    close();
  });

  function close() {
    document.removeEventListener("keydown", onKey);
    scrim.remove();
    document.body.style.overflow = "";
    if (opener?.focus) opener.focus({ preventScroll: true });
    if (onClose) onClose();
  }

  function onKey(event) {
    if (event.key === "Escape") {
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = panel.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const lastEl = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      lastEl.focus();
    } else if (!event.shiftKey && document.activeElement === lastEl) {
      event.preventDefault();
      first.focus();
    }
  }

  document.addEventListener("keydown", onKey);
  document.body.style.overflow = "hidden";
  document.body.appendChild(scrim);
  panel.querySelector("h2").focus({ preventScroll: true });
  return { close, panel };
}

/**
 * Ask before doing something that can't be undone.
 *
 * Used for exactly the actions that deserve it — deleting a shop item, resetting
 * something. Everything reversible just happens, with an undo in the toast where
 * one is possible.
 */
export function confirm(title, message, { danger = false, confirmLabel = "Confirm" } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const view = sheet(title, h("p", { class: "muted" }, message), {
      actions: [
        h("button", { class: "btn", type: "button", onClick: () => { finish(false); view.close(); } }, "Cancel"),
        h(
          "button",
          {
            class: `btn ${danger ? "btn--danger" : "btn--primary"}`,
            type: "button",
            onClick: () => { finish(true); view.close(); },
          },
          confirmLabel
        ),
      ],
      onClose: () => finish(false),
    });
  });
}

/* ── Live countdown ─────────────────────────────────────────────────────── */
/** The wall-clock instant `seconds` from now — a duration pinned to when it
    was learned, which is the thing views should hold on to. */
export function deadline(seconds) {
  return Date.now() + Math.max(0, Number(seconds) || 0) * 1000;
}

/** Whole seconds left until `until`, never negative. */
export function remaining(until) {
  return Math.max(0, Math.ceil((until - Date.now()) / 1000));
}

/**
 * A span that counts down to `until` once a second and calls `onDone` at zero.
 *
 * Anchored to a deadline rather than decrementing a counter, for two reasons.
 * A background tab has its timers throttled to about one tick a minute, so a
 * subtract-one-per-tick clock reads minutes behind the moment someone comes
 * back to it. And a payload's `ready_in`/`next_in` is a duration measured when
 * the *server* answered: re-mounting a countdown built straight from one — on
 * a tab switch, say, which repaints from state already in hand — restarts the
 * clock at whatever it said then. That was the fishing cast timer jumping back
 * to 19s every time you looked at your bag and came back. Views therefore keep
 * `deadline(seconds)` from the moment they learn it and hand that here, so a
 * repaint can only ever show less time, never more.
 *
 * Cleans itself up when removed from the document, so a view swap mid-countdown
 * doesn't leave an interval running against a detached node — the leak that
 * makes a long-lived tab slowly get worse.
 */
export function countdownUntil(until, onDone = null, { prefix = "" } = {}) {
  const node = h("span", { class: "tabular" }, prefix + fmt.clock(remaining(until)));
  const timer = setInterval(() => {
    if (!node.isConnected) {
      clearInterval(timer);
      return;
    }
    const left = remaining(until);
    if (left <= 0) {
      clearInterval(timer);
      node.textContent = prefix + "ready";
      if (onDone) onDone();
      return;
    }
    node.textContent = prefix + fmt.clock(left);
  }, 1000);
  return node;
}

/** `countdownUntil` for a duration that starts *now* — the right call only when
    the caller has this second's number, not one that came with a payload. */
export function countdown(seconds, onDone = null, options = {}) {
  return countdownUntil(deadline(seconds), onDone, options);
}

/* ── Loot ───────────────────────────────────────────────────────────────── */
/** One caught fish, mined ore or found item, as a rarity-coloured card. */
export function loot(item, { fresh = false, index = 0 } = {}) {
  const rarity = item.rarity || "common";
  const meta = item.treasure
    ? `+${fmt.num(item.coins)}`
    : item.weight_label
      ? `${item.weight_label} · ${fmt.num(item.value)}`
      : item.qty
        ? `×${fmt.num(item.qty)}`
        : item.value
          ? fmt.num(item.value)
          : "";
  return h(
    "div",
    {
      class: [
        "loot",
        `loot-rarity-${rarity}`,
        `loot--${rarity}`,
        fresh && "loot--new",
      ]
        .filter(Boolean)
        .join(" "),
      style: fresh ? { animationDelay: `${index * 90}ms` } : null,
    },
    item.rarity_label && h("span", { class: "loot__rarity" }, item.rarity_label),
    h("span", { class: "loot__glyph", "aria-hidden": "true" }, item.emoji || "📦"),
    h("span", { class: "loot__name" }, item.name),
    meta && h("span", { class: "loot__meta" }, meta),
    item.personal_best && h("span", { class: "pill pill--warn" }, "Personal best")
  );
}

/** Charge pips: how many runs are banked right now. */
export function pips(ready, max) {
  return h(
    "div",
    { class: "pips", "aria-label": `${ready} of ${max} ready` },
    ...Array.from({ length: Math.min(max, 12) }, (_, i) =>
      h("span", { class: `pip${i < ready ? " pip--on" : ""}`, "aria-hidden": "true" })
    )
  );
}
