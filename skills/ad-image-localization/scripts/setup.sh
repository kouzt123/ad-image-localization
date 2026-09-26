#!/usr/bin/env bash
# Create the skill's Python environment: <skill>/.venv
# Needs uv (preferred) or python3.10+. Rendering uses the installed Google Chrome;
# if Chrome is missing, Playwright's own Chromium is installed instead.
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing --python 3.11 "$VENV"
  uv pip install --python "$VENV/bin/python" -r "$SKILL_DIR/requirements.txt"
  uv pip install --python "$VENV/bin/python" --no-deps simple-lama-inpainting==0.1.2
else
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$SKILL_DIR/requirements.txt"
  "$VENV/bin/python" -m pip install --no-deps simple-lama-inpainting==0.1.2
fi

if ! "$VENV/bin/python" - <<'EOF' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch(channel="chrome").close()
EOF
then
  echo "Google Chrome not usable by Playwright; installing bundled Chromium..."
  "$VENV/bin/python" -m playwright install chromium
fi

echo "Ready: $VENV/bin/python"
echo "Models download on first decompose (~180 MB rembg isnet, ~200 MB big-lama)."
