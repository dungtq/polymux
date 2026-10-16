#!/usr/bin/env bash
# The project gate: ruff + public leak scan + architecture check (docs/ARCHITECTURE.md). `make check` calls this.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests tools
# tracked files against the public leak patterns (home paths, 10/8 addresses, keys, .env/.db files)
./tools/leakcheck.sh --tree --patterns tools/leak-public.txt
# --root . scans src/pm as well; --static-dir adds the dt theme's static tree (zero tolerance, no baseline)
uv run python tools/archcheck.py --root . --static-dir src/pm/web/themes/dt/static "$@"
