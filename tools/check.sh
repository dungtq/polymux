#!/usr/bin/env bash
# The project gate: ruff + architecture check (docs/ARCHITECTURE.md). `make check` calls this.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests tools
# --root . scans src/pm as well; --static-dir adds the dt theme's static tree (zero tolerance, no baseline)
uv run python tools/archcheck.py --root . --static-dir src/pm/web/themes/dt/static "$@"
