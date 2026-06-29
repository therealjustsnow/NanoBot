"""Gatekeeper constants: avatar-catalog dirs, dHash threshold, mute-role name,
the verify button custom_id, captcha attempt caps, and the default prompt.
"""

import os

# Hard cap on bytes pulled from a learnavatar URL. The SSRF filter only proves
# the host is public, not that it won't stream gigabytes, so bound the read.
_MAX_AVATAR_BYTES = 8 * 1024 * 1024

# Reference images for stock-avatar detection. Two folders are scanned:
#   • assets/gatekeeper_avatars/ — bundled seeds shipped with the repo (read-only)
#   • data/gatekeeper_avatars/   — runtime additions from /gatekeeper learnavatar
# Each file's perceptual hash is matched against joining members.
BUNDLED_AVATAR_DIR = os.path.join("assets", "gatekeeper_avatars")
AVATAR_CATALOG_DIR = os.path.join("data", "gatekeeper_avatars")

# Perceptual (difference) hash size: 8x8 comparison grid → 64-bit hash.
# A joining avatar within this Hamming distance of any catalog hash is a match.
# Stock avatars are visually distinct, so a small distance avoids false hits.
_DHASH_THRESHOLD = 8

MUTE_ROLE_NAME = "Muted (NanoBot)"

_VERIFY_BUTTON_CID = "gk:verify:button"

# The math captcha is deliberately easy for humans, so the answer space is tiny
# (sums of 2..9 → 4..18). Without an attempt cap a script could blind-guess past
# it in a handful of tries, so lock a user out for a short cooldown after this
# many wrong answers. In-memory only — a restart resets it, which is fine for a
# speed bump.
_MAX_VERIFY_ATTEMPTS = 5
_VERIFY_LOCKOUT_SECONDS = 60

DEFAULT_VERIFY_MESSAGE = (
    "You've been temporarily muted in **{server}** while we check new accounts.\n\n"
    "To get unmuted, press the **Verify** button below and solve the quick math "
    "problem. If you don't verify in time you'll be removed from the server."
)
