/**
 * core/router.js
 * A hash-free client router over the History API.
 *
 * Real paths rather than `#/fragments` because the server already serves the app
 * shell for any non-API path — so `/g/123/fishing` is a link somebody can send,
 * bookmark and reload. A hash router would have been less work and would have
 * made every one of those slightly wrong.
 *
 * Views are `async (params, ctx) => Node`. They may be slow (they usually fetch)
 * so the router paints a loading state first and drops any result that arrives
 * after the user has already navigated somewhere else — the stale-response race
 * that otherwise shows the previous page's content under the new page's title.
 */

import { swap, announceView } from "./dom.js";
import { appPath, appUrl } from "./config.js";

const routes = [];
let host = null;
let onNavigate = null;
let token = 0;
let current = null;

/** Register a route. `path` uses `:name` segments and a trailing `*`. */
export function route(path, view, meta = {}) {
  const names = [];
  const pattern = path
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/:(\w+)/g, (_, name) => {
      names.push(name);
      return "([^/]+)";
    })
    .replace(/\*/g, ".*");
  routes.push({ path, regex: new RegExp(`^${pattern}/?$`), names, view, meta });
}

export function start(mount, { onNavigate: hook = null } = {}) {
  host = mount;
  onNavigate = hook;
  window.addEventListener("popstate", () => render(appPath() + location.search));
  document.addEventListener("click", intercept);
  render(appPath() + location.search);
}

/** Turn same-origin anchor clicks into route changes. */
function intercept(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey) return;
  const link = event.target.closest("a[href]");
  if (!link) return;
  const href = link.getAttribute("href");
  if (!href || href.startsWith("http") || href.startsWith("//") || link.target) return;
  if (href.startsWith("/api/") || link.hasAttribute("download")) return;
  event.preventDefault();
  go(appPath(href));
}

export function go(path, { replace = false } = {}) {
  if (path === appPath() + location.search) return;
  // The history entry carries the deployment's base path; everything inside the
  // app talks in app-relative paths, so a subpath deployment needs no other
  // code to know about it.
  history[replace ? "replaceState" : "pushState"]({}, "", appUrl(path));
  render(path);
}

export const back = () => history.back();

export function match(path) {
  const clean = path.split("?")[0];
  for (const entry of routes) {
    const found = entry.regex.exec(clean);
    if (!found) continue;
    const params = {};
    entry.names.forEach((name, i) => (params[name] = decodeURIComponent(found[i + 1])));
    return { entry, params };
  }
  return null;
}

export const currentRoute = () => current;

export const query = (key, fallback = null) =>
  new URLSearchParams(location.search).get(key) ?? fallback;

/** Update the query string without re-rendering — used by tab and page state. */
export function setQuery(key, value) {
  const params = new URLSearchParams(location.search);
  if (value === null || value === undefined || value === "") params.delete(key);
  else params.set(key, value);
  const search = params.toString();
  history.replaceState({}, "", location.pathname + (search ? `?${search}` : ""));
}

async function render(path) {
  const found = match(path);
  if (!found) {
    go("/", { replace: true });
    return;
  }
  const mine = ++token;
  current = { ...found, path };
  if (onNavigate) onNavigate(current);

  let node;
  try {
    node = await found.entry.view(found.params, { path });
  } catch (error) {
    if (mine !== token) return;
    node = errorView(error);
  }
  // Somebody navigated while this was loading — their view wins.
  if (mine !== token) return;
  swap(host, node);
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  announceView(host);
}

/** Re-run the current route — for a "retry" button or after a mutation. */
export const reload = () => render(appPath() + location.search);

function errorView(error) {
  const div = document.createElement("div");
  div.className = "card stack";
  const title = document.createElement("h2");
  title.textContent = "This page didn't load";
  const message = document.createElement("p");
  message.className = "muted";
  message.textContent = error?.message || "Something went wrong.";
  const retry = document.createElement("button");
  retry.className = "btn btn--primary";
  retry.textContent = "Try again";
  retry.addEventListener("click", reload);
  div.append(title, message, retry);
  return div;
}
