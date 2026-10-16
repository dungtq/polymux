#!/usr/bin/env bash
# Fake `claude` for the dev pm instance (docs/DEV-TESTING-PLAN.md, P2 contract).
#   exec -a claude bash tools/devfakeagent.sh <busy|idle|waiting|failed> [--resume <uuid>]
# Writes $HOME/.claude/sessions/<pid>.json, echoes stdin lines, and on "/exit" removes the file and exits.
# Also reachable as $HOME/.local/bin/claude (symlink), so pm's Resume and Launch start it.
set -u
status=idle
sid=""
if [ "${1:-}" = "--resume" ]; then
  sid="${2:-}"
elif [ -n "${1:-}" ]; then
  status="$1"
  [ "${2:-}" = "--resume" ] && sid="${3:-}"
fi
case "$status" in busy | idle | waiting | failed) ;; *) status=idle ;; esac
[ -n "$sid" ] && status=idle  # a resumed agent starts idle
[ -n "$sid" ] || sid="${DEVFAKE_SESSION:-00000000-0000-4000-8000-0000000000ff}"

dir="$HOME/.claude/sessions"
mkdir -p "$dir"
file="$dir/$$.json"
stat=$(</proc/$$/stat)
rest=${stat##*) }
# shellcheck disable=SC2086
set -- $rest
start=${20}
write_session() {
  local ms
  ms=$(date +%s%3N)
  printf '{"pid": %s, "procStart": "%s", "sessionId": "%s", "status": "%s", "cwd": "%s", "updatedAt": %s, "statusUpdatedAt": %s}\n' \
    "$$" "$start" "$sid" "$status" "$PWD" "$ms" "$ms" >"$file"
}
write_session
trap 'rm -f "$file"' EXIT
printf 'fixture agent (%s) session %s\n' "$status" "$sid"
while IFS= read -r line; do
  if [ "$line" = "/exit" ]; then
    printf 'bye\n'
    exit 0
  fi
  if [ "$line" = "/clear" ]; then  # like the real CLI: a new conversation = a new session id
    sid=$(</proc/sys/kernel/random/uuid)
    write_session
    printf 'cleared; new session %s\n' "$sid"
    continue
  fi
  printf '> %s\n' "$line"
done
