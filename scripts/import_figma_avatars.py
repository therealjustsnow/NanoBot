#!/usr/bin/env python3
"""
scripts/import_figma_avatars.py

Bulk-import avatar images from a Figma file into the gatekeeper stock-avatar
catalog. Discord's pickable "stock" avatars (the illustrated blob characters)
upload as real per-user avatars, so the bot can't detect them with a null
check — it matches them by perceptual hash against a catalog of reference
images. This script fills that catalog from a Figma design file (e.g. a
community library of Discord's default avatars).

It only ADDS images; the bot's perceptual-hash match still decides what counts
as a hit, so a few stray frames in the file are harmless (and easy to prune).

Auth: needs a Figma personal access token. Create one at
https://www.figma.com/developers/api#access-tokens (Settings → Security →
Personal access tokens). Pass it with --token or the FIGMA_TOKEN env var so it
never has to be typed inline.

Usage:
    # Preview what would be exported (no download):
    python scripts/import_figma_avatars.py --token FIGMA_xxx --list

    # Export every COMPONENT/FRAME node into the bundled catalog:
    python scripts/import_figma_avatars.py --token FIGMA_xxx

    # Narrow it down (recommended) — only square nodes <=256px on a given page:
    python scripts/import_figma_avatars.py --token FIGMA_xxx \
        --page "Avatars" --square --max-size 256

The file key defaults to the community Discord library referenced in chat; pass
--file-key to target a different file. The key is the path segment after
/design/ or /file/ in the Figma URL.

No third-party dependencies — uses only the Python standard library.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.figma.com/v1"
DEFAULT_FILE_KEY = "Ube4utEx4TUpndu7qqVvYw"
DEFAULT_OUT = os.path.join("assets", "gatekeeper_avatars")


def _api_get(path: str, token: str) -> dict:
    """GET a Figma API endpoint and return parsed JSON."""
    req = urllib.request.Request(f"{API}{path}", headers={"X-Figma-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if exc.code == 403:
            sys.exit("Figma API 403 — bad/expired token or no access to this file.")
        if exc.code == 404:
            sys.exit("Figma API 404 — file key not found.")
        sys.exit(f"Figma API error {exc.code}: {body}")
    except urllib.error.URLError as exc:
        sys.exit(f"Network error reaching Figma: {exc.reason}")


def _walk(node: dict, page_name: str):
    """Yield (node, page_name) for every node in the subtree."""
    yield node, page_name
    for child in node.get("children", ()):
        yield from _walk(child, page_name)


def _sanitize(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name[:60] or "node"


def collect_nodes(doc: dict, args) -> list[dict]:
    """Find renderable nodes matching the type/name/size filters."""
    types = {t.strip().upper() for t in args.types.split(",") if t.strip()}
    name_filter = (args.name_contains or "").lower()
    matches: list[dict] = []

    for page in doc.get("children", ()):  # top level = CANVAS (pages)
        if page.get("type") != "CANVAS":
            continue
        if args.page and page.get("name", "").lower() != args.page.lower():
            continue
        for node, _page in _walk(page, page.get("name", "")):
            if node.get("type") not in types:
                continue
            if name_filter and name_filter not in node.get("name", "").lower():
                continue
            box = node.get("absoluteBoundingBox") or {}
            w, h = box.get("width"), box.get("height")
            if args.square and w and h:
                longer = max(w, h)
                if longer and abs(w - h) / longer > 0.1:
                    continue  # not roughly square
            if args.max_size and w and h and max(w, h) > args.max_size:
                continue
            if args.min_size and w and h and max(w, h) < args.min_size:
                continue
            matches.append(node)

    if args.limit:
        matches = matches[: args.limit]
    return matches


def export_images(file_key: str, ids: list[str], token: str, scale: float) -> dict:
    """Ask Figma to render the given node ids as PNGs; return {id: url}."""
    out: dict = {}
    # Chunk ids to keep the query string a sane length.
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        query = urllib.parse.urlencode(
            {"ids": ",".join(chunk), "format": "png", "scale": scale}
        )
        data = _api_get(f"/images/{file_key}?{query}", token)
        if data.get("err"):
            sys.exit(f"Figma image render error: {data['err']}")
        out.update({k: v for k, v in (data.get("images") or {}).items() if v})
    return out


def download(url: str, dest: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            payload = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"  ! download failed: {exc}", file=sys.stderr)
        return False
    with open(dest, "wb") as fh:
        fh.write(payload)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Import Figma avatars into the catalog.")
    ap.add_argument("--token", default=os.environ.get("FIGMA_TOKEN"))
    ap.add_argument("--file-key", default=DEFAULT_FILE_KEY)
    ap.add_argument("--out", default=DEFAULT_OUT, help="catalog folder to write into")
    ap.add_argument(
        "--types",
        default="COMPONENT,FRAME",
        help="comma-separated Figma node types to export",
    )
    ap.add_argument("--page", help="only export nodes on this page (by name)")
    ap.add_argument("--name-contains", help="only nodes whose name contains this")
    ap.add_argument("--square", action="store_true", help="only ~square nodes")
    ap.add_argument("--max-size", type=float, help="skip nodes larger than N px")
    ap.add_argument("--min-size", type=float, help="skip nodes smaller than N px")
    ap.add_argument("--scale", type=float, default=1.0, help="render scale (1 = 1x)")
    ap.add_argument("--limit", type=int, help="cap the number of nodes")
    ap.add_argument(
        "--list",
        action="store_true",
        help="print matched nodes and exit (no download)",
    )
    args = ap.parse_args()

    if not args.token:
        sys.exit("No token. Pass --token or set FIGMA_TOKEN.")

    print(f"Fetching file {args.file_key} …")
    doc = _api_get(f"/files/{args.file_key}", args.token).get("document", {})
    nodes = collect_nodes(doc, args)
    print(f"Matched {len(nodes)} node(s) (types={args.types}).")

    if args.list or not nodes:
        for n in nodes:
            box = n.get("absoluteBoundingBox") or {}
            dims = f"{box.get('width', '?')}x{box.get('height', '?')}"
            print(f"  {n['id']:>12}  {dims:>10}  {n.get('name', '')}")
        if not nodes:
            print("Nothing to export — adjust --types/--page/--name-contains.")
        return

    os.makedirs(args.out, exist_ok=True)
    urls = export_images(
        args.file_key, [n["id"] for n in nodes], args.token, args.scale
    )

    saved = 0
    for n in nodes:
        url = urls.get(n["id"])
        if not url:
            continue
        fname = f"figma_{_sanitize(n.get('name', ''))}_{n['id'].replace(':', '-')}.png"
        if download(url, os.path.join(args.out, fname)):
            saved += 1
            print(f"  ✓ {fname}")

    print(f"\nSaved {saved}/{len(nodes)} image(s) to {args.out}/")
    print("Reload the bot (or run /gatekeeper status) to pick up the new catalog.")


if __name__ == "__main__":
    main()
