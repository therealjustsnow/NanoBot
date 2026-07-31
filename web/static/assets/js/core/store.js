/**
 * core/store.js
 * The little state that outlives a page: who is signed in, which server is open,
 * and the theme.
 *
 * Not a state management library — a plain object with a subscribe list. The
 * only cross-page state a dashboard like this genuinely has is identity and
 * context; everything else belongs to the screen showing it and should die with
 * it, so it can't go stale behind the user's back.
 */

import { setCsrf } from "./api.js";

const listeners = new Set();

export const state = {
  user: null,
  csrf: null,
  bot: null,
  configured: false,
  playEnabled: true,
  missing: [],
  guilds: [],
  guild: null, // the server currently open
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function set(patch) {
  Object.assign(state, patch);
  if ("csrf" in patch) setCsrf(patch.csrf);
  for (const fn of listeners) fn(state);
}

export const guildById = (id) => state.guilds.find((g) => g.id === String(id)) || null;

/* ── Theme ──────────────────────────────────────────────────────────────── */
const THEME_KEY = "nanobot.theme";

/**
 * Themes are "system", "dark" or "light". "system" removes the attribute so the
 * media query decides; the other two stamp `data-theme`, which the stylesheet
 * gives priority over the media query in both directions.
 */
export function theme() {
  try {
    return localStorage.getItem(THEME_KEY) || "system";
  } catch {
    return "system";
  }
}

export function setTheme(value) {
  try {
    if (value === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, value);
  } catch {
    /* private browsing — the choice just won't persist */
  }
  applyTheme();
}

export function applyTheme() {
  const value = theme();
  const root = document.documentElement;
  if (value === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", value);
}

/* ── Last server ────────────────────────────────────────────────────────── */
// Remembering the last server turns "open the dashboard" into one tap instead
// of three for the overwhelmingly common case of managing one server.
const LAST_KEY = "nanobot.lastGuild";

export function rememberGuild(id) {
  try {
    localStorage.setItem(LAST_KEY, String(id));
  } catch {
    /* ignore */
  }
}

export function lastGuild() {
  try {
    return localStorage.getItem(LAST_KEY);
  } catch {
    return null;
  }
}
