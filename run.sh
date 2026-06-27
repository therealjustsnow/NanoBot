#!/usr/bin/env bash
# run.sh — NanoBot launcher for Linux/macOS.
# Finds a suitable Python (3.11+), prefers a local venv, then hands off to
# run.py (pre-flight check + launch). All arguments are passed through, so
# `./run.sh --check` works the same as `python run.py --check`.

set -u

cd "$(dirname "$0")" || exit 1

case "$(uname -s)" in
    CYGWIN* | MINGW* | MSYS*)
        echo "Windows detected — please use run.bat instead."
        exit 1
        ;;
esac

# ── Optional: bgutil PO-token provider ──────────────────────────────────────
# YouTube gates audio formats behind a GVS PO token; the music cog's yt-dlp
# fetches one from a local bgutil provider (see the Self-Hosting wiki). The
# provider is a separate process, so start it here — before exec hands off to
# the bot — or it dies on every restart and playback 403s. This is a no-op
# unless the provider is installed (deno + the cloned server), so it stays
# harmless on hosts that don't use it. Override the paths/port with the env
# vars below if your layout differs.
POT_DIR="${BGUTIL_POT_DIR:-$HOME/bgutil-pot/server}"
POT_PORT="${BGUTIL_POT_PORT:-4416}"
DENO_BIN="${DENO_BIN:-$HOME/.deno/bin/deno}"
if [ -d "${POT_DIR}/node_modules" ] && [ -x "${DENO_BIN}" ]; then
    # Only start it if nothing already answers on the port (idempotent).
    if ! curl -fsS "http://127.0.0.1:${POT_PORT}/ping" > /dev/null 2>&1; then
        echo "Starting bgutil PO-token provider on :${POT_PORT}…"
        (
            cd "${POT_DIR}/node_modules" \
                && setsid "${DENO_BIN}" run -A ../src/main.ts --port "${POT_PORT}" \
                    > "${HOME}/potserver.log" 2>&1 &
        )
    fi
fi

# Prefer a project-local virtual environment when one exists.
for venv_dir in venv .venv; do
    if [ -x "${venv_dir}/bin/python" ]; then
        exec "${venv_dir}/bin/python" run.py "$@"
    fi
done

# No venv — look for a system Python that is new enough.
PY_CANDIDATES=(python3 python3.13 python3.12 python3.11 python)

for py in "${PY_CANDIDATES[@]}"; do
    if command -v "$py" > /dev/null 2>&1; then
        if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2> /dev/null; then
            exec "$py" run.py "$@"
        fi
    fi
done

echo "ERROR: No suitable Python found. NanoBot requires Python 3.11 or newer."
echo "       Run ./install.sh to set everything up, or install Python manually:"
echo "       https://www.python.org/downloads/"
exit 1
