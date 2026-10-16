#!/usr/bin/env bash
# UI checks against the dev instance: screenshot smoke (port 8095), then the Playwright suite (port 8105).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run tools/uishot.py --smoke
uv run pytest -m ui -q "$@"
