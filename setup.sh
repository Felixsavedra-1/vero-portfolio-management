#!/usr/bin/env bash
# setup.sh — Install Vero: creates venv, installs deps, and writes
# the `brief` and `portfolio` commands to /usr/local/bin/.
#
# Usage:
#   bash setup.sh            # if /usr/local/bin is writable
#   sudo bash setup.sh       # on most macOS systems (requires sudo)

set -e

REPO_DIR="$( cd "$(dirname "$0")" && pwd )"
VENV_PYTHON="$REPO_DIR/venv/bin/python"

# Require Python 3.9+ (zoneinfo is stdlib from 3.9)
PY_OK=$(python3 -c 'import sys; print(sys.version_info >= (3,9))' 2>/dev/null || echo "False")
if [ "$PY_OK" != "True" ]; then
  echo "Error: Python 3.9 or higher is required (found: $(python3 --version 2>&1))."
  exit 1
fi

echo "==> Setting up virtual environment..."
python3 -m venv "$REPO_DIR/venv"

echo "==> Installing dependencies..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet -r "$REPO_DIR/requirements.txt"

echo "==> Installing commands to /usr/local/bin/ ..."

cat > /usr/local/bin/brief << EOF
#!/bin/sh
exec "$VENV_PYTHON" "$REPO_DIR/morning_brief.py" "\$@"
EOF
chmod +x /usr/local/bin/brief

cat > /usr/local/bin/portfolio << EOF
#!/bin/sh
exec "$VENV_PYTHON" "$REPO_DIR/portfolio.py" "\$@"
EOF
chmod +x /usr/local/bin/portfolio

echo ""
echo "Done."
echo ""
echo "  brief               — run your morning brief"
echo "  portfolio show      — view current holdings"
echo "  portfolio buy AAPL  — log a trade"
echo ""
echo "Copy config_local.py.example to config_local.py to add your watchlist and personal settings."
