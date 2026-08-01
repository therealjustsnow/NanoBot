#!/usr/bin/env bash
#
# Assemble the dashboard frontend for a static host.
#
# There is nothing to compile — the frontend is plain ES modules, and that is a
# deliberate property rather than an omission (the API's Content-Security-Policy
# forbids inline script and external script hosts, so the files served are the
# files in the repository). "Building" is copying `web/static/` and writing the
# two things that differ per deployment:
#
#   assets/config.json   the API origin and the base path (see core/config.js)
#   404.html             a copy of index.html, which is how a static host with
#                        no rewrite rules serves a single-page app with real
#                        paths — a deep link 404s, and the 404 page boots the
#                        app, which then routes on the URL it was asked for.
#
# This is the same script `.github/workflows/pages.yml` inlines. Use it for a
# local preview, or for any static host that isn't GitHub Pages.
#
# Usage:
#   scripts/build_dashboard.sh [output-dir]
#
# Configured by environment (see .env.example):
#   NANOBOT_API_BASE    https://bot.example.com   API origin; empty = same-origin
#   NANOBOT_BASE_PATH   /NanoBot                  URL prefix; empty = served at /
#
# Preview it (from the output directory, so the base path resolves):
#   python -m http.server 8080 --directory dist
#
# A frontend on a different origin to the API needs the API's own
# `dashboard_allowed_origins` to list it. docs/deployment.md has the rest.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:-dist}"
api_base="${NANOBOT_API_BASE:-}"
base_path="${NANOBOT_BASE_PATH:-}"

# Normalise: a leading slash, no trailing one. "/" means the root, i.e. none.
base_path="${base_path%/}"
[ -n "$base_path" ] && [ "${base_path#/}" = "$base_path" ] && base_path="/$base_path"

rm -rf "$out"
mkdir -p "$out"
cp -r "$repo_root/web/static/." "$out/"

cat > "$out/assets/config.json" <<JSON
{
  "apiBase": "${api_base}",
  "basePath": "${base_path}",
  "label": "${api_base:-same origin}"
}
JSON

cp "$out/index.html" "$out/404.html"

# GitHub Pages runs uploads through Jekyll by default, which drops anything
# whose name starts with an underscore. Harmless everywhere else.
touch "$out/.nojekyll"

# Re-point the <base href> the shell ships with, so relative asset URLs resolve
# under the prefix — and so the router knows it before config.json has loaded.
if [ -n "$base_path" ]; then
  for page in "$out/index.html" "$out/404.html"; do
    sed -i.bak "s#<base href=\"/\">#<base href=\"${base_path}/\">#" "$page"
    rm -f "$page.bak"
  done
fi

echo "Built $out for base path '${base_path:-/}' against API '${api_base:-same origin}'"
