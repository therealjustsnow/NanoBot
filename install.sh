#!/usr/bin/env bash
# install.sh — NanoBot setup for Linux/macOS.
#
# Installs system packages (git, ffmpeg, libopus, Python 3.11+), creates a
# local virtual environment in ./venv, installs Python dependencies, and
# creates config.ini from the example if it doesn't exist yet.
#
# Usage:
#     ./install.sh                → full setup
#     ./install.sh --no-system    → skip system packages (pip/venv/config only)
#
# Safe to re-run: every step is idempotent.

set -u

cd "$(dirname "$0")" || exit 1

SKIP_SYSTEM=0
for arg in "$@"; do
    case "$arg" in
        --no-system) SKIP_SYSTEM=1 ;;
        -h | --help)
            sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m !  %s\033[0m\n' "$1"; }
die() {
    printf '\033[1;31m !! %s\033[0m\n' "$1"
    exit 1
}

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo > /dev/null 2>&1; then
    SUDO="sudo"
fi

# ── 1. System packages ────────────────────────────────────────────────────────
if [ "$SKIP_SYSTEM" -eq 0 ]; then
    say "Installing system packages"
    if command -v apt-get > /dev/null 2>&1; then
        $SUDO apt-get update -y
        $SUDO apt-get install -y git curl ffmpeg libopus0 \
            python3 python3-venv python3-pip ||
            warn "Some apt packages failed to install — continuing anyway."
    elif command -v dnf > /dev/null 2>&1; then
        $SUDO dnf install -y git curl python3 python3-pip opus ||
            warn "Some dnf packages failed to install — continuing anyway."
        $SUDO dnf install -y ffmpeg ||
            warn "ffmpeg install failed (Fedora needs RPM Fusion: https://rpmfusion.org). Music cog won't work without it."
    elif command -v pacman > /dev/null 2>&1; then
        $SUDO pacman -Sy --noconfirm --needed git curl ffmpeg opus python python-pip ||
            warn "Some pacman packages failed to install — continuing anyway."
    elif command -v zypper > /dev/null 2>&1; then
        $SUDO zypper install -y git curl ffmpeg libopus0 python311 python311-pip ||
            warn "Some zypper packages failed to install — continuing anyway."
    elif command -v brew > /dev/null 2>&1; then
        brew install git ffmpeg opus python@3.12 ||
            warn "Some brew packages failed to install — continuing anyway."
    else
        warn "No supported package manager found (apt/dnf/pacman/zypper/brew)."
        warn "Install git, ffmpeg, libopus and Python 3.11+ manually, then re-run with --no-system."
    fi
else
    say "Skipping system packages (--no-system)"
fi

# ── 2. Find Python 3.11+ ─────────────────────────────────────────────────────
say "Looking for Python 3.11+"
PYTHON=""
for py in python3 python3.13 python3.12 python3.11 python; do
    if command -v "$py" > /dev/null 2>&1; then
        if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2> /dev/null; then
            PYTHON="$py"
            break
        fi
    fi
done
[ -n "$PYTHON" ] || die "No Python 3.11+ found. Install it (https://www.python.org/downloads/) and re-run."
echo "    Using: $PYTHON ($("$PYTHON" --version 2>&1))"

# ── 3. Virtual environment ───────────────────────────────────────────────────
say "Setting up virtual environment (./venv)"
if [ ! -x "venv/bin/python" ]; then
    "$PYTHON" -m venv venv || die "venv creation failed. On Debian/Ubuntu make sure python3-venv is installed."
else
    echo "    venv already exists — reusing it."
fi
VPY="venv/bin/python"

# ── 4. Python dependencies ───────────────────────────────────────────────────
say "Installing Python dependencies"
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install --upgrade -r requirements.txt || die "pip install failed — see output above."

# ── 5. Config file ───────────────────────────────────────────────────────────
say "Config"
if [ -f "config.ini" ]; then
    echo "    config.ini already exists — leaving it untouched."
else
    cp example_config.ini config.ini
    echo "    Created config.ini from example_config.ini."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
printf '\n\033[1;32m✅ Setup complete!\033[0m\n\n'
echo "Next steps:"
echo "  1. Edit config.ini and paste your bot token"
echo "     (https://discord.com/developers/applications → Bot → Token)"
echo "  2. Enable Server Members, Message Content and Presence intents"
echo "     in the Developer Portal (same page)."
echo "  3. Start the bot:  ./run.sh"
