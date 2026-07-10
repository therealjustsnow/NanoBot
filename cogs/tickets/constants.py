"""Constants for the tickets cog."""

# Static custom_ids for the persistent views. The panel button and the
# in-thread Close/Claim buttons are registered once at startup (cog_load) and
# resolve their ticket from the interaction's channel/guild at click time, so
# no per-ticket view restore is needed after a restart.
_PANEL_BUTTON_CID = "ticket:panel:open"
_CLOSE_BUTTON_CID = "ticket:thread:close"
_CLAIM_BUTTON_CID = "ticket:thread:claim"

# Modal field caps. Thread names cap at 100 chars, so the subject stays well
# under that once "ticket-NNNN-" is prepended.
SUBJECT_MAX = 80
DETAILS_MAX = 1000

# How many messages a close-time transcript includes (oldest first).
TRANSCRIPT_MESSAGE_LIMIT = 500

# 7 days — the longest Discord allows. Auto-archive is cosmetic (an archived
# ticket is still open in the DB and un-archives on close), it keeps stale
# tickets out of the active-threads list.
THREAD_AUTO_ARCHIVE_MINUTES = 10080

DEFAULT_MAX_OPEN = 3

DEFAULT_PANEL_TITLE = "🎫 Support Tickets"
DEFAULT_PANEL_MESSAGE = (
    "Need help from the staff team? Press the button below to open a "
    "private ticket — only you and the staff can see it."
)
