#!/usr/bin/env bash
# One-shot setup on macOS. Run from inside the canvas-buddy folder.
set -euo pipefail

command -v python3 >/dev/null || { echo "python3 not found. Install it first."; exit 1; }

echo "Creating virtual environment..."
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env — open it and paste your Canvas token, then run:"
  echo "  ./.venv/bin/python -m canvas_buddy.run test"
else
  echo ".env already exists, leaving it alone."
fi

echo
echo "Setup done. Next:"
echo "  1. Edit .env  (CANVAS_TOKEN, and Telegram details)"
echo "  2. ./.venv/bin/python -m canvas_buddy.run test"
echo "  3. ./.venv/bin/python -m canvas_buddy.run check --dry-run"
