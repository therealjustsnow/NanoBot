/**
 * core/config.js
 * Where the API is, and where this copy of the app is mounted.
 *
 * The dashboard ships two ways and this file is the only thing that differs
 * between them:
 *
 *   **Served by the bot** (the normal case). Same origin, no base path, cookies
 *   ride along automatically. Nothing to configure — the defaults below are
 *   already right, which is why there is no build step and no generated file in
 *   the repository.
 *
 *   **Hosted separately** — GitHub Pages, Netlify, an S3 bucket. The API is on
 *   another origin, and the app may be under a repository subpath like
 *   `/NanoBot/`. Both are read from `/assets/config.json`, fetched once at boot,
 *   so switching hosts is a file edit rather than a code change.
 *
 * Why a fetched JSON file rather than a bundler's `import.meta.env`: there is no
 * bundler, and the CSP forbids the inline `<script>` that would otherwise carry
 * the values. A static JSON file is the one mechanism that works in both modes
 * without either.
 *
 * `apiBase` must be an origin with no trailing slash. `basePath` must start
 * with `/` and not end with one — `/NanoBot`, never `NanoBot/`. Both are
 * normalised here rather than trusted, because getting a slash wrong is the
 * most likely deployment mistake and it should not be the user's problem.
 */

const DEFAULTS = {
  // Empty means "same origin as this page" — the bot-served case.
  apiBase: "",
  // Empty means "mounted at the root".
  basePath: "",
  // Shown on the login screen when the API is somewhere else, so a failure to
  // reach it is diagnosable without opening the network tab.
  label: "",
};

let config = { ...DEFAULTS };
let loaded = false;

function normalise(raw) {
  const out = { ...DEFAULTS, ...(raw || {}) };
  out.apiBase = String(out.apiBase || "").replace(/\/+$/, "");
  let base = String(out.basePath || "").trim();
  if (base && !base.startsWith("/")) base = `/${base}`;
  out.basePath = base.replace(/\/+$/, "");
  return out;
}

/**
 * Load the deployment config, once.
 *
 * A missing file is the *expected* case when the bot serves the app, so a 404
 * is not an error and is not logged — it just means "use the defaults".
 */
export async function loadConfig() {
  if (loaded) return config;
  loaded = true;
  try {
    const response = await fetch(`${basePath()}/assets/config.json`, {
      cache: "no-cache",
    });
    if (response.ok) config = normalise(await response.json());
  } catch {
    /* no config file: same-origin defaults, which is the common case */
  }
  return config;
}

/** The origin the API lives on. Empty string = this one. */
export const apiBase = () => config.apiBase;

/**
 * The path prefix this copy of the app is mounted under.
 *
 * Guessed from the document's own `<base href>` before the config file has
 * loaded, so the very first fetch — the config file itself — lands in the right
 * place on a subpath deployment.
 */
export function basePath() {
  if (config.basePath) return config.basePath;
  const base = document.querySelector("base")?.getAttribute("href") || "";
  return base === "/" ? "" : base.replace(/\/+$/, "");
}

/** Whether the API is on another origin — which changes how requests are sent. */
export const isCrossOrigin = () =>
  Boolean(config.apiBase) && !config.apiBase.startsWith(location.origin);

export const deploymentLabel = () => config.label;

/** Build a full API URL from an app-relative path like `/api/me`. */
export const apiUrl = (path) => `${apiBase()}${path}`;

/** Build an in-app link, honouring the base path. */
export const appUrl = (path) => `${basePath()}${path}`;

/** Strip the base path off a browser path, so the router sees app-relative paths. */
export function appPath(pathname = location.pathname) {
  const base = basePath();
  if (base && pathname.startsWith(base)) {
    return pathname.slice(base.length) || "/";
  }
  return pathname || "/";
}
