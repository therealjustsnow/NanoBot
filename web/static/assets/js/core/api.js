/**
 * core/api.js
 * The single door to the server.
 *
 * Everything the app fetches goes through here so four things are guaranteed
 * once rather than remembered thirty times:
 *
 *   - the CSRF token rides every mutating request;
 *   - an error comes back as an `ApiError` carrying the server's own sentence,
 *     which is what the toast shows — the frontend never invents error copy;
 *   - a 401 signs the app out cleanly instead of leaving a half-dead session;
 *   - GETs are de-duplicated and briefly cached, so a page that asks for the
 *     same overview twice in a second makes one request.
 *
 * The cache is *only* for GETs and any mutation clears it. Nothing that decides
 * something is ever read from it.
 */

import { apiUrl, isCrossOrigin } from "./config.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
const CACHE_MS = 2500;

let csrf = null;
const inflight = new Map();
const cache = new Map();
const listeners = new Set();

export class ApiError extends Error {
  constructor(status, payload) {
    super(payload?.message || "Something went wrong.");
    this.status = status;
    this.code = payload?.code || "error";
    this.hint = payload?.hint || null;
    this.retryAfter = payload?.retry_after ?? null;
    this.payload = payload || {};
  }
}

export function setCsrf(token) {
  csrf = token || null;
}

/** Called when the server says the session is gone, so the shell can react. */
export function onUnauthorized(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function clearCache(prefix = "") {
  for (const key of [...cache.keys()]) {
    if (!prefix || key.startsWith(prefix)) cache.delete(key);
  }
}

async function request(method, path, body, { signal } = {}) {
  // `include` rather than `same-origin` when the API is elsewhere: a
  // separately-hosted frontend still authenticates with the session cookie, and
  // the server answers with an explicit CORS allow-list (see web/http.py) so
  // this is never a blanket grant.
  const options = {
    method,
    credentials: isCrossOrigin() ? "include" : "same-origin",
    signal,
    headers: {},
  };
  if (body !== undefined) {
    options.headers = { ...JSON_HEADERS };
    options.body = JSON.stringify(body);
  }
  if (method !== "GET" && csrf) options.headers["X-NanoBot-CSRF"] = csrf;

  let response;
  try {
    response = await fetch(apiUrl(path), options);
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    // A network failure is not an application error and shouldn't read like
    // one — the user's connection dropped, which is worth saying plainly.
    throw new ApiError(0, {
      code: "offline",
      message: "Couldn't reach the server. Check your connection and try again.",
    });
  }

  if (response.status === 204) return {};

  let payload = null;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    payload = await response.json().catch(() => null);
  }

  if (!response.ok) {
    const error = new ApiError(response.status, payload?.error || null);
    if (response.status === 401) {
      for (const fn of listeners) fn(error);
    }
    throw error;
  }
  return payload ?? {};
}

/**
 * A GET, de-duplicated and briefly memoised.
 *
 * Two components asking for the same thing while the first request is still in
 * flight is the normal case on a page built from small cards, and it should
 * cost one request, not two.
 */
export function get(path, { fresh = false, signal } = {}) {
  if (!fresh) {
    const hit = cache.get(path);
    if (hit && Date.now() - hit.at < CACHE_MS) return Promise.resolve(hit.value);
    const pending = inflight.get(path);
    if (pending) return pending;
  }
  const promise = request("GET", path, undefined, { signal })
    .then((value) => {
      cache.set(path, { at: Date.now(), value });
      return value;
    })
    .finally(() => inflight.delete(path));
  inflight.set(path, promise);
  return promise;
}

function mutate(method) {
  return async (path, body) => {
    const result = await request(method, path, body ?? {});
    // Any write can change any read — a cast changes the wallet, a setting
    // changes the overview. Clearing wholesale is cheap and can't go stale.
    clearCache();
    return result;
  };
}

export const post = mutate("POST");
export const patch = mutate("PATCH");
export const del = mutate("DELETE");

/** Build an API path, escaping every interpolated segment. */
export function url(strings, ...values) {
  return strings.reduce(
    (out, part, i) =>
      out + part + (i < values.length ? encodeURIComponent(String(values[i])) : ""),
    ""
  );
}
