#!/usr/bin/env bash
# Run yt-artist from the repo without relying on PATH. Use after install-mac.sh.
# Usage: ./scripts/yt-artist.sh --help   (from repo root)
# Or:    ./scripts/yt-artist.sh --db ./yt.db fetch-channel "https://www.youtube.com/@channel"
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Prefer install-mac.sh venv; fall back to repo .venv
VENV="${YT_ARTIST_VENV:-$HOME/.local/yt-artist/venv}"
[[ ! -d "$VENV" && -d "$REPO_ROOT/.venv" ]] && VENV="$REPO_ROOT/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "yt-artist venv not found. Run from repo root: ./scripts/install-mac.sh" >&2
  exit 1
fi

# Use current repo code when run from repo (so fixes apply without reinstall)
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
# Unbuffered so output shows in terminal
export PYTHONUNBUFFERED=1
exec "$VENV/bin/python" -m yt_artist.cli "$@"
