#!/usr/bin/env bash
# Terminal-only setup for yt-artist on macOS.
# Run from repo root: ./scripts/install-mac.sh
# One script: venv, install, PATH, verify. Then use: yt-artist
set -e

INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/yt-artist}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
PYTHON="${PYTHON:-python3}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v "$PYTHON" &>/dev/null; then
  echo "Error: $PYTHON not found." >&2
  echo "Install Python 3.9+:" >&2
  echo "  • Homebrew:  brew install python" >&2
  echo "  • Xcode CLT: xcode-select --install" >&2
  echo "  • Manual:    https://www.python.org/downloads/" >&2
  echo "Then re-run this script, or: PYTHON=/path/to/python3 ./scripts/install-mac.sh" >&2
  exit 1
fi

ver=$("$PYTHON" -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2>/dev/null || true)
if [[ -z "$ver" ]] || [[ "$ver" < "3 9" ]]; then
  echo "Error: Need Python 3.9 or newer. Found: $($PYTHON --version 2>&1)" >&2
  echo "Upgrade with: brew install python   (then re-run this script)" >&2
  exit 1
fi

echo "Setting up yt-artist..."
if ! mkdir -p "$INSTALL_DIR" 2>/dev/null || ! mkdir -p "$BIN_DIR" 2>/dev/null; then
  echo "Error: Cannot create $INSTALL_DIR or $BIN_DIR (permission denied)." >&2
  echo "" >&2
  echo "Fix: use the developer setup instead:" >&2
  echo "  cd $(pwd)" >&2
  echo "  python3 -m venv .venv && source .venv/bin/activate" >&2
  echo "  pip install -e \".[dev]\"" >&2
  echo "  yt-artist --help" >&2
  echo "" >&2
  echo "Or override the install directory:" >&2
  echo "  INSTALL_DIR=/tmp/yt-artist BIN_DIR=/tmp/bin ./scripts/install-mac.sh" >&2
  exit 1
fi
VENV="$INSTALL_DIR/venv"

if [[ ! -d "$VENV" ]]; then
  echo "Creating venv at $VENV ..."
  "$PYTHON" -m venv "$VENV"
fi

source "$VENV/bin/activate"
pip install -q --upgrade pip

if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
  echo "Installing yt-artist from repo (with dependencies)..."
  pip install -q "$REPO_ROOT"
else
  echo "Installing yt-artist from PyPI..."
  pip install -q yt-artist
fi

WRAPPER="$BIN_DIR/yt-artist"
cat > "$WRAPPER" << EOF
#!/usr/bin/env bash
# PYTHONUNBUFFERED=1 so output appears in terminal (no buffering)
exec env PYTHONUNBUFFERED=1 "$VENV/bin/python" -m yt_artist.cli "\$@"
EOF
chmod +x "$WRAPPER"

# Ensure PATH in current session and in shell config so future terminals see yt-artist
PATH="$BIN_DIR:$PATH"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  export PATH="$BIN_DIR:$PATH"
fi

# Add to shell config if not already there (terminal-only setup)
SHELL_RC=""
ADDED_PATH=""
[[ -f "$HOME/.zshrc" ]] && SHELL_RC="$HOME/.zshrc"
[[ -z "$SHELL_RC" && -f "$HOME/.bash_profile" ]] && SHELL_RC="$HOME/.bash_profile"
[[ -z "$SHELL_RC" ]] && SHELL_RC="$HOME/.zshrc"

if [[ -n "$SHELL_RC" ]]; then
  if ! grep -q "yt-artist\|$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# yt-artist (added by install-mac.sh)" >> "$SHELL_RC"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$SHELL_RC"
    ADDED_PATH=1
    echo "Added PATH to $SHELL_RC"
  fi
fi

echo ""
echo "Setup complete. Verifying..."
echo "---"
"$WRAPPER" --help
echo "---"
echo ""
echo "Binary: $WRAPPER"
echo "Use: yt-artist --help"
if [[ -n "$ADDED_PATH" ]]; then
  echo "In new terminals, run: source $SHELL_RC   (or open a new tab)"
fi
echo ""
echo "For AI summaries: install Ollama (https://ollama.com) and run:"
echo "  ollama run mistral"
echo ""
echo "YouTube authentication:"
echo "  PO token provider (rustypipe) is installed automatically."
echo "  Subtitle downloads should work out of the box."
echo "  Run: yt-artist doctor    — to verify your setup"
echo ""
echo "To update after git pull: re-run ./scripts/install-mac.sh"
