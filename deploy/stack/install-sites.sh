#!/usr/bin/env bash
# Render nginx/sites/*.conf.template with values from .env and install them into the
# nginx-ui container (sites-available + sites-enabled), then test and reload nginx.
# Safe to run again: it overwrites the same files and links.
#
# Usage:
#   ./install-sites.sh            # install pm and ntfy
#   ./install-sites.sh pm         # install only pm
#   ./install-sites.sh --print    # print the rendered files, change nothing
set -euo pipefail

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"
SERVICE="nginx-ui"
AVAILABLE="/etc/nginx/sites-available"
ENABLED="/etc/nginx/sites-enabled"

log() { printf '[install-sites] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Read KEY from the .env file without executing it. Handles KEY=value, KEY="value",
# KEY='value' and a trailing " # comment" on unquoted values. Last match wins.
env_get() {
  local key="$1" line val
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)"
  [ -n "$line" ] || return 0
  val="${line#*=}"
  val="${val#"${val%%[![:space:]]*}"}"
  case "$val" in
    \"*) val="${val#\"}"; val="${val%%\"*}" ;;
    \'*) val="${val#\'}"; val="${val%%\'*}" ;;
    *) val="${val%%[[:space:]]#*}"; val="${val%"${val##*[![:space:]]}"}" ;;
  esac
  printf '%s' "$val"
}

print_only=false
sites=()
for arg in "$@"; do
  case "$arg" in
    --print) print_only=true ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    -*) die "unknown option: $arg" ;;
    *) sites+=("$arg") ;;
  esac
done
[ "${#sites[@]}" -gt 0 ] || sites=(pm ntfy)

[ -f "$ENV_FILE" ] || die "missing ${ENV_FILE}; run: cp env.example .env"
command -v envsubst >/dev/null || die "envsubst not found (package gettext-base / gettext)"

PM_HOST="$(env_get PM_HOST)"
NTFY_HOST="$(env_get NTFY_HOST)"
PM_PORT="$(env_get PM_PORT)"
PM_PORT="${PM_PORT:-8093}"

host_re='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'
[[ "$PM_HOST" =~ $host_re ]] || die "PM_HOST is empty or not a host name: '${PM_HOST}'"
[[ "$NTFY_HOST" =~ $host_re ]] || die "NTFY_HOST is empty or not a host name: '${NTFY_HOST}'"
[[ "$PM_PORT" =~ ^[0-9]{1,5}$ ]] || die "PM_PORT is not a port: '${PM_PORT}'"
export PM_HOST NTFY_HOST PM_PORT

# Only these names are replaced; nginx variables such as $host stay as they are.
# shellcheck disable=SC2016
VARS='${PM_HOST} ${NTFY_HOST} ${PM_PORT}'

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

for site in "${sites[@]}"; do
  [[ "$site" =~ ^[a-z0-9_-]+$ ]] || die "bad site name: ${site}"
  tpl="${STACK_DIR}/nginx/sites/${site}.conf.template"
  [ -f "$tpl" ] || die "no template ${tpl}"
  envsubst "$VARS" <"$tpl" >"${tmpdir}/${site}.conf"
done

if $print_only; then
  for site in "${sites[@]}"; do
    printf '# ===== %s.conf =====\n' "$site"
    cat "${tmpdir}/${site}.conf"
  done
  exit 0
fi

dc() { docker compose --project-directory "$STACK_DIR" "$@"; }

[ -n "$(dc ps --status running -q "$SERVICE" 2>/dev/null)" ] || \
  die "service ${SERVICE} is not running; start the stack first: docker compose up -d --build"

dc exec -T "$SERVICE" grep -q "include ${ENABLED}/" /etc/nginx/nginx.conf || \
  die "/etc/nginx/nginx.conf in ${SERVICE} does not include ${ENABLED}/*; add that line inside http { }"

dc exec -T "$SERVICE" mkdir -p "$AVAILABLE" "$ENABLED"
for site in "${sites[@]}"; do
  dc cp "${tmpdir}/${site}.conf" "${SERVICE}:${AVAILABLE}/${site}.conf"
  dc exec -T "$SERVICE" ln -sfn "${AVAILABLE}/${site}.conf" "${ENABLED}/${site}.conf"
  log "installed ${site}.conf"
done

if ! dc exec -T "$SERVICE" nginx -t; then
  log "nginx -t failed; disabling the sites just installed so nginx keeps running"
  for site in "${sites[@]}"; do
    dc exec -T "$SERVICE" rm -f "${ENABLED}/${site}.conf"
  done
  die "fix the template or .env and run again (files stay in ${AVAILABLE})"
fi

dc exec -T "$SERVICE" nginx -s reload
log "nginx reloaded; sites: ${sites[*]}"
