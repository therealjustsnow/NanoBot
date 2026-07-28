#!/usr/bin/env python3
"""
scripts/fetch_cosmetic_art.py

Fetch the bundled cosmetic artwork listed in assets/profile/art_manifest.json.

The renderer generates every cosmetic from a palette, and any file at
``assets/profile/<slot>/<key>.<ext>`` wins over that generated art (see
utils/profile_card.cosmetic_image). This script is what fills that directory:
it resolves each manifest entry against its source API, checks the licence,
downloads the image, crops it to the card's aspect ratio, and writes a WebP
next to the others — plus a CREDITS.md naming every artist and source.

Two sources, both chosen because their licence is unambiguous and machine
readable:

  * **Wikimedia Commons** — used only for faithful reproductions of works whose
    copyright has expired. The API reports the licence, and this script REFUSES
    anything that is not public domain / CC0, so a mistyped filename can't
    quietly pull in an encumbered image.
  * **NASA** — U.S. federal works are not subject to copyright. NASA asks to be
    credited, which CREDITS.md does. (The NASA insignia is *not* public domain;
    this fetches imagery only, never logos.)

Re-running is safe: an image already on disk is skipped unless --force is
given. Nothing here runs at bot startup — the bot only ever reads the files.

Usage:
    python scripts/fetch_cosmetic_art.py                # fetch what's missing
    python scripts/fetch_cosmetic_art.py --force        # re-fetch everything
    python scripts/fetch_cosmetic_art.py --list         # resolve + report only
    python scripts/fetch_cosmetic_art.py --key banner_art_kiss

Needs Pillow (already a bot dependency); everything else is the standard
library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the script is useless without it
    sys.exit("This script needs Pillow: pip install -r requirements.txt")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART_DIR = os.path.join(_REPO_ROOT, "assets", "profile")
MANIFEST = os.path.join(ART_DIR, "art_manifest.json")
CREDITS_MD = os.path.join(ART_DIR, "CREDITS.md")
CREDITS_JSON = os.path.join(ART_DIR, "credits.json")

# A courteous, identifying agent — Wikimedia blocks the default urllib one.
UA = {
    "User-Agent": (
        "NanoBot-cosmetic-art/1.0 "
        "(https://github.com/therealjustsnow/NanoBot; Discord bot artwork fetch)"
    )
}

# Target sizes, matching the two card renderers. Art is stored at card size:
# the renderer only ever scales it down from here.
SLOT_SIZES = {"banner": (1000, 560), "wallet": (920, 450)}

# Licences that may be redistributed inside the repository. Anything else is a
# hard failure rather than a warning — the point of the check is that nobody
# has to audit the manifest by hand later.
_OK_LICENCES = re.compile(
    r"public\s*domain|^pd|cc0|no\s*restrictions|^us\s*government", re.I
)


def _get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout
    ) as resp:
        return resp.read()


def _get_json(url: str) -> dict:
    return json.loads(_get(url).decode("utf-8"))


def _strip_html(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw or "")).strip()


def _clean_title(raw: str) -> str:
    """Commons object names often carry Wikidata qualifiers and every
    translation run together — keep the first readable title."""
    text = _strip_html(raw)
    text = re.split(r"title QS:|label QS:", text)[0]
    for marker in ("English:", "english:"):
        if marker in text:
            text = text.split(marker, 1)[1]
    text = re.split(r"(?:Japanese|Deutsch|Français|Español|Italiano|Русский):", text)[0]
    return text.strip(" -–—:") or "Untitled"


# ── Source resolvers ──────────────────────────────────────────────────────────
def resolve_commons(entry: dict) -> dict:
    """Resolve a Commons file to a download URL + licence + attribution."""
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": "2000",
            "titles": "File:" + entry["file"],
        }
    )
    data = _get_json("https://commons.wikimedia.org/w/api.php?" + query)
    pages = list(data.get("query", {}).get("pages", {}).values())
    if not pages or "imageinfo" not in pages[0]:
        raise LookupError(f"Commons has no file named {entry['file']!r}")
    info = pages[0]["imageinfo"][0]
    meta = info.get("extmetadata", {})
    return {
        "url": info.get("thumburl") or info["url"],
        "licence": _strip_html(meta.get("LicenseShortName", {}).get("value", "")),
        "credit": _strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown",
        "title": _clean_title(meta.get("ObjectName", {}).get("value", ""))
        or entry["file"].rsplit(".", 1)[0],
        "page": info.get("descriptionurl", ""),
        "source": "Wikimedia Commons",
    }


def resolve_nasa(entry: dict) -> dict:
    """Resolve a NASA image id to the largest sensible asset + its metadata."""
    nasa_id = entry["nasa_id"]
    search = _get_json(
        "https://images-api.nasa.gov/search?"
        + urllib.parse.urlencode({"nasa_id": nasa_id, "media_type": "image"})
    )
    items = search.get("collection", {}).get("items", [])
    if not items:
        raise LookupError(f"NASA has no image with id {nasa_id!r}")
    data = items[0]["data"][0]
    assets = _get_json(
        f"https://images-api.nasa.gov/asset/{urllib.parse.quote(nasa_id)}"
    )
    hrefs = [a["href"] for a in assets["collection"]["items"]]
    # Prefer ~large-but-not-enormous renditions; the originals can be 100 MB+.
    for want in ("~large.jpg", "~medium.jpg", "~orig.jpg", ".jpg"):
        match = [h for h in hrefs if h.lower().endswith(want)]
        if match:
            url = match[0]
            break
    else:
        raise LookupError(f"No JPEG rendition for {nasa_id!r}")
    return {
        "url": url.replace("http://", "https://"),
        "licence": "Public domain (U.S. Government work)",
        "credit": data.get("photographer") or data.get("center") or "NASA",
        "title": data.get("title", nasa_id),
        "page": f"https://images.nasa.gov/details/{nasa_id}",
        "source": "NASA",
    }


_RESOLVERS = {"commons": resolve_commons, "nasa": resolve_nasa}


# ── Image processing ──────────────────────────────────────────────────────────
def cover_crop(img: Image.Image, size: tuple[int, int], focus: str = "center"):
    """Scale-and-crop so the image fills `size` without distorting it.

    A card banner is a fixed aspect and the sources are not, so something has
    to give: stretching a painting is the one option that always looks wrong.
    """
    target_w, target_h = size
    scale = max(target_w / img.width, target_h / img.height)
    resized = img.resize(
        (max(target_w, int(img.width * scale)), max(target_h, int(img.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    if focus == "top":
        top = 0
    elif focus == "bottom":
        top = resized.height - target_h
    else:
        top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def process(raw: bytes, slot: str, focus: str) -> Image.Image:
    from io import BytesIO

    img = Image.open(BytesIO(raw))
    img = img.convert("RGB")
    return cover_crop(img, SLOT_SIZES[slot], focus)


# ── Credits ───────────────────────────────────────────────────────────────────
def write_credits(records: list[dict]) -> None:
    """Merge these records into the credits files.

    A `--key` run resolves only part of the manifest, and replacing the file
    with that subset would silently drop everyone else's attribution — which is
    the one thing in here that must never be lossy.
    """
    merged: dict[str, dict] = {}
    if os.path.exists(CREDITS_JSON):
        try:
            with open(CREDITS_JSON, "r", encoding="utf-8") as fh:
                merged = {r["key"]: r for r in json.load(fh)}
        except (OSError, ValueError, KeyError, TypeError):
            merged = {}
    merged.update({r["key"]: r for r in records})
    records = sorted(merged.values(), key=lambda r: r["key"])
    with open(CREDITS_JSON, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    lines = [
        "# Cosmetic artwork credits",
        "",
        "Every image bundled in `assets/profile/` is public domain or CC0 —",
        "`scripts/fetch_cosmetic_art.py` re-checks the licence against the source",
        "API on every fetch and refuses anything else. Re-generate this file by",
        "running that script.",
        "",
        "NASA imagery is used under NASA's media usage guidelines: NASA content is",
        "generally not subject to copyright, and NASA is credited as the source.",
        "The NASA insignia is *not* public domain and is not used here.",
        "",
        "| Cosmetic | Artwork | Credit | Licence | Source |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        page = f"[{r['source']}]({r['page']})" if r["page"] else r["source"]
        lines.append(
            f"| `{r['key']}` | {r['title']} | {r['credit']} | {r['licence']} | {page} |"
        )
    lines.append("")
    with open(CREDITS_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-fetch existing files")
    parser.add_argument(
        "--list", action="store_true", help="resolve only, download nothing"
    )
    parser.add_argument(
        "--key", action="append", help="only this cosmetic key (repeatable)"
    )
    parser.add_argument(
        "--quality", type=int, default=88, help="WebP quality (default 88)"
    )
    args = parser.parse_args()

    with open(MANIFEST, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    entries = manifest["art"]
    if args.key:
        wanted = set(args.key)
        entries = [e for e in entries if e["key"] in wanted]
        if not entries:
            return print(f"No manifest entry matches {sorted(wanted)}") or 1

    records, failures = [], []
    for entry in entries:
        key, slot = entry["key"], entry["slot"]
        if slot not in SLOT_SIZES:
            failures.append(f"{key}: unknown slot {slot!r}")
            continue
        out_path = os.path.join(ART_DIR, slot, f"{key}.webp")
        try:
            meta = _RESOLVERS[entry["source"]](entry)
        except (LookupError, KeyError, urllib.error.URLError, OSError) as exc:
            failures.append(f"{key}: {exc}")
            continue
        # Manifest overrides for the two fields the source APIs return badly:
        # a Japanese object name comes back untranslated, and a Google Art
        # Project credit is a whole biography. The licence is never overridable.
        for field in ("title", "credit"):
            if entry.get(field):
                meta[field] = entry[field]

        if not _OK_LICENCES.search(meta["licence"]):
            failures.append(
                f"{key}: refusing licence {meta['licence']!r} — only public "
                "domain / CC0 may be redistributed here"
            )
            continue

        record = dict(meta, key=key, slot=slot, file=f"{slot}/{key}.webp")
        records.append(record)
        print(f"  {key:28s} {meta['licence'][:20]:22s} {meta['title'][:44]}")
        if args.list:
            continue
        if os.path.exists(out_path) and not args.force:
            print(f"  {'':28s} already downloaded — skipping")
            continue
        try:
            raw = _get(meta["url"])
            img = process(raw, slot, entry.get("focus", "center"))
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            img.save(out_path, "WEBP", quality=args.quality, method=5)
        except (OSError, urllib.error.URLError) as exc:
            failures.append(f"{key}: download/processing failed — {exc}")
            continue
        print(
            f"  {'':28s} → {os.path.relpath(out_path, _REPO_ROOT)} "
            f"({os.path.getsize(out_path) // 1024} KB)"
        )

    if records and not args.list:
        write_credits(records)
        print(f"\nWrote {os.path.relpath(CREDITS_MD, _REPO_ROOT)}")
    if failures:
        print("\nFailures:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
