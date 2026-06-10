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
