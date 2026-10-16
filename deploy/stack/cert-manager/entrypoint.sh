#!/usr/bin/env bash
# cert-manager: keeps a private root CA and one leaf certificate (wildcard SANs) for nginx-ui.
#
# - Root CA: generated once (CA_MODE=generate) or imported (CA_MODE=import) in /pki/ca.
# - Leaf: ECDSA P-256 in /pki/live/nginx-ui/{fullchain,privkey}.pem, reissued when it is
#   missing, close to expiry, or when CERT_DOMAINS / CERT_IPS changed.
# - After a reissue it restarts the nginx-ui container (through /var/run/docker.sock) so the
#   panel (EnableHTTPS) and nginx load the new files. No socket or empty NGINX_UI_CONTAINER:
#   the restart is skipped and logged; restart nginx-ui yourself.
# - Optional Pushover messages on renew and failure (PUSHOVER_TOKEN + PUSHOVER_USER).
set -euo pipefail

PKI_ROOT="/pki"
CA_DIR="${PKI_ROOT}/ca"
LIVE_DIR="${PKI_ROOT}/live/nginx-ui"
LEAF_CERT="${LIVE_DIR}/fullchain.pem"
LEAF_KEY="${LIVE_DIR}/privkey.pem"
LEAF_SANS="${LIVE_DIR}/.sans"

CA_MODE="${CA_MODE:-generate}"
CA_DAYS="${CA_DAYS:-3650}"
CA_KEY_ALGO="${CA_KEY_ALGO:-rsa4096}"
CA_COMMON_NAME="${CA_COMMON_NAME:-pm stack private root CA}"
CERT_LEAF_DAYS="${CERT_LEAF_DAYS:-397}"
CERT_RENEW_BEFORE_DAYS="${CERT_RENEW_BEFORE_DAYS:-30}"
CERT_CHECK_INTERVAL="${CERT_CHECK_INTERVAL:-12h}"
CERT_DOMAINS="${CERT_DOMAINS:-}"
CERT_IPS="${CERT_IPS:-127.0.0.1}"
NGINX_UI_CONTAINER="${NGINX_UI_CONTAINER:-}"
ROOT_CA_CERT_FILE="${ROOT_CA_CERT_FILE:-ca.crt}"
ROOT_CA_EXPORT_PATH="${ROOT_CA_EXPORT_PATH:-${CA_DIR}/root-ca.crt}"

PUSHOVER_TOKEN="${PUSHOVER_TOKEN:-}"
PUSHOVER_USER="${PUSHOVER_USER:-}"

log() {
  printf '[cert-manager] %s\n' "$*" >&2
}

fail() {
  log "ERROR: $*"
  exit 1
}

truncate_msg() {
  local text="$1"
  local max="${2:-900}"
  if [ "${#text}" -gt "$max" ]; then
    printf '%s…' "${text:0:$max}"
  else
    printf '%s' "$text"
  fi
}

pushover_notify() {
  local title="$1"
  local message="$2"
  local priority="${3:-0}"

  if [ -z "$PUSHOVER_TOKEN" ] || [ -z "$PUSHOVER_USER" ]; then
    return 0
  fi

  curl -fsS -m 30 \
    --data-urlencode "token=${PUSHOVER_TOKEN}" \
    --data-urlencode "user=${PUSHOVER_USER}" \
    --data-urlencode "title=${title}" \
    --data-urlencode "message=${message}" \
    --data-urlencode "priority=${priority}" \
    https://api.pushover.net/1/messages.json >/dev/null || \
    log "Pushover notification failed (non-fatal)"
}

interval_to_seconds() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    echo "$raw"
    return
  fi
  if [[ "$raw" =~ ^([0-9]+)([smhd])$ ]]; then
    local n="${BASH_REMATCH[1]}"
    local u="${BASH_REMATCH[2]}"
    case "$u" in
      s) echo "$n" ;;
      m) echo $((n * 60)) ;;
      h) echo $((n * 3600)) ;;
      d) echo $((n * 86400)) ;;
    esac
    return
  fi
  fail "Invalid CERT_CHECK_INTERVAL: ${raw}"
}

clamp_leaf_days() {
  local days="$1"
  if [ "$days" -gt 397 ]; then
    log "CERT_LEAF_DAYS=${days} exceeds 397; using 397"
    days=397
  fi
  if [ "$days" -lt 1 ]; then
    fail "CERT_LEAF_DAYS must be at least 1"
  fi
  echo "$days"
}

days_until_expiry() {
  local cert_file="$1"
  local enddate end_epoch now_epoch
  enddate="$(openssl x509 -in "$cert_file" -noout -enddate | cut -d= -f2-)"
  end_epoch="$(date -d "$enddate" +%s)"
  now_epoch="$(date +%s)"
  echo $(( (end_epoch - now_epoch) / 86400 ))
}

# Normalised SAN request (no spaces), stored next to the leaf to detect .env changes.
requested_sans() {
  printf 'dns=%s ip=%s\n' "${CERT_DOMAINS// /}" "${CERT_IPS// /}"
}

leaf_cn() {
  local first
  first="${CERT_DOMAINS%%,*}"
  first="${first// /}"
  if [ -n "$first" ]; then
    echo "$first"
  else
    echo "nginx-ui"
  fi
}

write_leaf_openssl_cnf() {
  local cn="$1"
  local cnf="$2"
  local dns_idx=1 ip_idx=1 domain ip

  cat >"$cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ${cn}

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
EOF

  IFS=',' read -ra domains <<< "$CERT_DOMAINS"
  for domain in "${domains[@]}"; do
    domain="${domain// /}"
    [ -z "$domain" ] && continue
    echo "DNS.${dns_idx} = ${domain}" >>"$cnf"
    dns_idx=$((dns_idx + 1))
  done

  IFS=',' read -ra ips <<< "$CERT_IPS"
  for ip in "${ips[@]}"; do
    ip="${ip// /}"
    [ -z "$ip" ] && continue
    echo "IP.${ip_idx} = ${ip}" >>"$cnf"
    ip_idx=$((ip_idx + 1))
  done

  if [ "$dns_idx" -eq 1 ] && [ "$ip_idx" -eq 1 ]; then
    fail "Set at least one of CERT_DOMAINS or CERT_IPS"
  fi
}

generate_ca_key() {
  local key_path="$1"
  case "$CA_KEY_ALGO" in
    ec384)
      openssl ecparam -name secp384r1 -genkey -noout -out "$key_path"
      ;;
    rsa4096)
      openssl genrsa -out "$key_path" 4096
      ;;
    *)
      fail "Unsupported CA_KEY_ALGO: ${CA_KEY_ALGO} (use ec384 or rsa4096)"
      ;;
  esac
}

validate_ca_material() {
  local step=""
  if ! step="$(openssl x509 -in "${CA_DIR}/ca.crt" -noout 2>&1)"; then
    pushover_notify "cert-manager failure" "$(truncate_msg "Invalid ca.crt: ${step}")" 1
    fail "Invalid ca.crt"
  fi
  if ! step="$(openssl x509 -in "${CA_DIR}/ca.crt" -noout -checkend 0 2>&1)"; then
    pushover_notify "cert-manager failure" "$(truncate_msg "Root CA expired: ${step}")" 1
    fail "Root CA is expired"
  fi
  if [ ! -f "${CA_DIR}/ca.key" ]; then
    pushover_notify "cert-manager failure" "Missing ca.key for import mode" 1
    fail "Missing ca.key"
  fi
  chmod 400 "${CA_DIR}/ca.key"
  if openssl pkey -in "${CA_DIR}/ca.key" -check -noout >/dev/null 2>&1; then
    :
  elif openssl rsa -in "${CA_DIR}/ca.key" -check -noout >/dev/null 2>&1; then
    :
  else
    local err
    err="$(openssl pkey -in "${CA_DIR}/ca.key" -noout 2>&1 || true)"
    pushover_notify "cert-manager failure" "$(truncate_msg "Invalid ca.key: ${err}")" 1
    fail "Invalid ca.key"
  fi
}

# Copy the public root certificate to a stable name. The target must stay inside /pki/ca
# (= ./data/pki/ca on the host); nothing is written outside the stack's data directory.
export_root_ca() {
  local src="${CA_DIR}/${ROOT_CA_CERT_FILE}"
  local dest="$ROOT_CA_EXPORT_PATH"

  case "$dest" in
    "${CA_DIR}"/*) ;;
    *)
      log "Root CA export skipped: ROOT_CA_EXPORT_PATH must be under ${CA_DIR}"
      return 0
      ;;
  esac
  case "$dest" in
    */ca.key|*/ca.crt|*/ca.srl) log "Root CA export skipped: refusing to overwrite ${dest}"; return 0 ;;
  esac

  if [ ! -f "$src" ]; then
    src="${CA_DIR}/ca.crt"
    log "Using ca.crt for export (${ROOT_CA_CERT_FILE} not found)"
  fi

  install -m 0644 "$src" "$dest"
  log "Exported root CA to ${dest}"

  # Public copy next to the leaf, so nginx can offer it for download without seeing ca.key.
  install -m 0644 "${CA_DIR}/ca.crt" "${LIVE_DIR}/ca.crt"
}

ensure_ca() {
  mkdir -p "$CA_DIR" "$LIVE_DIR"

  case "$CA_MODE" in
    generate)
      if [ -f "${CA_DIR}/ca.crt" ] && [ -f "${CA_DIR}/ca.key" ]; then
        validate_ca_material
        log "Using existing root CA"
        return
      fi
      if [ -f "${CA_DIR}/ca.crt" ] || [ -f "${CA_DIR}/ca.key" ]; then
        fail "Partial CA material in ${CA_DIR}; remove or switch to import mode"
      fi
      log "Generating root CA (${CA_KEY_ALGO}, ${CA_DAYS} days)"
      generate_ca_key "${CA_DIR}/ca.key"
      chmod 400 "${CA_DIR}/ca.key"
      openssl req -x509 -new -key "${CA_DIR}/ca.key" -days "$CA_DAYS" \
        -out "${CA_DIR}/ca.crt" \
        -subj "/CN=${CA_COMMON_NAME}"
      chmod 644 "${CA_DIR}/ca.crt"
      ;;
    import)
      if [ ! -f "${CA_DIR}/ca.crt" ] || [ ! -f "${CA_DIR}/ca.key" ]; then
        pushover_notify "cert-manager failure" "CA_MODE=import requires data/pki/ca/ca.crt and ca.key" 1
        fail "CA_MODE=import requires ca.crt and ca.key in ${CA_DIR}"
      fi
      validate_ca_material
      log "Imported root CA validated"
      ;;
    *)
      fail "Unknown CA_MODE: ${CA_MODE} (use generate or import)"
      ;;
  esac
}

issue_leaf() {
  local leaf_days cn cnf tmpdir key csr cert not_after
  leaf_days="$(clamp_leaf_days "$CERT_LEAF_DAYS")"
  cn="$(leaf_cn)"
  cnf="$(mktemp)"
  tmpdir="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmpdir' '$cnf'" RETURN

  write_leaf_openssl_cnf "$cn" "$cnf"
  key="${tmpdir}/privkey.pem"
  csr="${tmpdir}/req.csr"
  cert="${tmpdir}/leaf.pem"

  openssl ecparam -name prime256v1 -genkey -noout -out "$key"
  openssl req -new -key "$key" -out "$csr" -config "$cnf"
  openssl x509 -req -in "$csr" \
    -CA "${CA_DIR}/ca.crt" -CAkey "${CA_DIR}/ca.key" -CAcreateserial \
    -out "$cert" -days "$leaf_days" -extensions v3_req -extfile "$cnf"

  install -m 0644 "$cert" "$LEAF_CERT"
  install -m 0600 "$key" "$LEAF_KEY"
  requested_sans >"$LEAF_SANS"
  not_after="$(openssl x509 -in "$LEAF_CERT" -noout -enddate | cut -d= -f2-)"
  log "Issued leaf CN=${cn}, notAfter=${not_after}"
  echo "$not_after"
}

needs_reissue() {
  if [ ! -f "$LEAF_CERT" ] || [ ! -f "$LEAF_KEY" ]; then
    return 0
  fi
  if ! openssl x509 -in "$LEAF_CERT" -checkend 0 -noout 2>/dev/null; then
    return 0
  fi
  if [ "$(cat "$LEAF_SANS" 2>/dev/null || true)" != "$(requested_sans)" ]; then
    log "CERT_DOMAINS / CERT_IPS changed; reissuing"
    return 0
  fi
  local remaining
  remaining="$(days_until_expiry "$LEAF_CERT")"
  if [ "$remaining" -le "$CERT_RENEW_BEFORE_DAYS" ]; then
    log "Leaf expires in ${remaining} day(s); reissuing"
    return 0
  fi
  return 1
}

RELOAD_METHOD=""
RELOAD_DETAIL=""

# Restart nginx-ui so the panel (EnableHTTPS) and nginx pick up the new PEM files.
restart_nginx_ui() {
  local container="$NGINX_UI_CONTAINER"

  RELOAD_METHOD="skipped"
  RELOAD_DETAIL=""

  if [ -z "$container" ]; then
    RELOAD_DETAIL="NGINX_UI_CONTAINER is empty"
  elif [ ! -S /var/run/docker.sock ]; then
    RELOAD_DETAIL="no /var/run/docker.sock mounted"
  elif [ "$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]; then
    RELOAD_DETAIL="container ${container} not running"
  else
    log "Restarting ${container} to load the new certificate"
    if docker restart "$container" >/dev/null 2>&1; then
      RELOAD_METHOD="restart"
      return 0
    fi
    RELOAD_METHOD="failed"
    RELOAD_DETAIL="$(docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null || echo unknown)"
    return 1
  fi
  log "nginx-ui restart skipped (${RELOAD_DETAIL}); restart it yourself to load the new certificate"
  return 0
}

handle_post_issue() {
  local reason="$1"
  local not_after="$2"
  local cn

  cn="$(leaf_cn)"

  # On a fresh stack nginx-ui waits for this container to be healthy, so it is not running
  # yet and restart_nginx_ui skips. On a restart with changed SANs it is running and restarts.
  if ! restart_nginx_ui; then
    pushover_notify "cert-manager failure" \
      "$(truncate_msg "Issued leaf but nginx-ui restart failed: ${RELOAD_DETAIL}")" 1
    fail "nginx-ui restart failed after issue (${reason})"
  fi

  if [ "$reason" = "renew" ]; then
    pushover_notify "cert-manager: leaf renewed" \
      "CN: ${cn}
notAfter: ${not_after}
nginx-ui: ${RELOAD_METHOD}${RELOAD_DETAIL:+ (${RELOAD_DETAIL})}"
  fi
}

run_once() {
  local reason="$1"
  local not_after=""

  if needs_reissue; then
    not_after="$(issue_leaf)"
    handle_post_issue "$reason" "$not_after"
  else
    log "Leaf still valid ($(days_until_expiry "$LEAF_CERT") days remaining)"
  fi
}

CHECK_SECONDS="$(interval_to_seconds "$CERT_CHECK_INTERVAL")"

if [ -z "${CERT_DOMAINS// /}" ]; then
  log "CERT_DOMAINS is empty; the leaf will only cover CERT_IPS (${CERT_IPS})"
fi

ensure_ca
export_root_ca
run_once "initial"

while true; do
  sleep "$CHECK_SECONDS"
  run_once "renew"
done
