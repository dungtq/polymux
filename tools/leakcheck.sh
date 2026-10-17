#!/usr/bin/env bash
# leakcheck: keep private values (host names, LAN addresses, home paths, keys) out of git.
#
# Usage:
#   tools/leakcheck.sh --staged                lines added in the index (the pre-commit hook)
#   tools/leakcheck.sh --msg <file>            a commit message (the commit-msg hook); comment lines and the
#                                              part below the scissors line, which git strips, are skipped
#                                              (git commit -m keeps '#' lines: --push still checks them)
#   tools/leakcheck.sh --push <remote> <url>   commits not yet on <remote>: added lines and full messages,
#                                              merges included (the pre-push hook; refs on stdin; a new
#                                              branch pushed to a bare URL scans its whole history)
#   tools/leakcheck.sh --tree                  every tracked text file (CI: --patterns tools/leak-public.txt)
#   tools/leakcheck.sh --install [--force]     install the hooks: pre-commit.d/leakcheck and
#                                              commit-msg.d/leakcheck (each run by a chain runner in
#                                              pre-commit / commit-msg) and pre-push
# Pattern file: --patterns FILE, else $PM_PRIVATE_PATTERNS, else ~/.config/pm/private-patterns.txt.
# One GNU ERE per line; '#' comments and blank lines are skipped. Patterns are numbered from 1.
#   ERE         content pattern (added lines, file text)
#   path:ERE    forbidden file path (repo-relative)
#   src:ERE     content pattern that does not apply to files under tests/
#   msg:ERE     commit message pattern only (--msg, --push), never applied to file text; e.g.
#               msg:#[0-9]+ blocks references to a private tracker's issue numbers, which a public
#               forge would link to its own, unrelated issues. Messages are also checked against the
#               ERE and src: patterns. The core ships no msg: pattern.
# A hit prints "file:line: pattern #N: <line with the match masked>" ("message:line" or "<sha> message:line"
# for a commit message); the matched text is never printed,
# the pattern itself and unmasked paths only when the pattern file is tracked in the repo (a public set).
# Exit: 0 clean (or no pattern file in --staged/--msg/--push), 1 hits, 2 usage or setup error.
# shellcheck disable=SC2016  # awk and hook bodies are literal text on purpose
set -euo pipefail
export LC_ALL=C

CHAIN_MARK='# git hooks chain runner (pre-commit.d)'
MSG_CHAIN_MARK='# git hooks chain runner (commit-msg.d)'
PM_MARK='# pm tracker hook'
PUSH_MARK='# leakcheck pre-push hook'
ZERO_RE='^0+$'
SELF=$(realpath -- "${BASH_SOURCE[0]}")
C_RE=() C_NUM=() C_SRC=() P_RE=() P_NUM=() M_RE=() M_NUM=()
TOP='' PAT_REL='' SHOW_RE=0 HITS=0 TMP=''

die() { echo "leakcheck: $*" >&2; exit 2; }
hit() { echo "leakcheck: $*" >&2; HITS=$((HITS + 1)); }

load_patterns() {  # $1 absolute pattern file
  local line re n=0 rc
  if [[ $1 == "$TOP"/* ]]; then  # skipped by the scans; public (shown) only when tracked, not merely ignored
    PAT_REL=${1#"$TOP"/}
    git ls-files --error-unmatch -- "$PAT_REL" >/dev/null 2>&1 && SHOW_RE=1
  fi
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
    n=$((n + 1))
    case $line in
      path:*) re=${line#path:}; P_RE+=("$re"); P_NUM+=("$n") ;;
      src:*) re=${line#src:}; C_RE+=("$re"); C_NUM+=("$n"); C_SRC+=(1) ;;
      msg:*) re=${line#msg:}; M_RE+=("$re"); M_NUM+=("$n") ;;
      *) re=$line; C_RE+=("$re"); C_NUM+=("$n"); C_SRC+=(0) ;;
    esac
    rc=0; grep -qE -e "$re" </dev/null || rc=$?
    ((rc < 2)) || die "pattern #$n is not a valid ERE"
  done < "$1"
}

label() {  # $1 pattern number, $2 regex
  if ((SHOW_RE)); then printf 'pattern #%s (%s)' "$1" "$2"; else printf 'pattern #%s' "$1"; fi
}

mask() {  # $1 text [$2 path|msg: also path / message patterns] -> every match replaced by ***, cut to 200 chars
  local t=$1 re rec m k offs lens res=("${C_RE[@]}")
  case ${2:-} in path) res+=("${P_RE[@]}") ;; msg) res+=("${M_RE[@]}") ;; esac
  for re in "${res[@]}"; do
    offs=() lens=()
    while IFS= read -r rec; do
      offs+=("${rec%%:*}"); m=${rec#*:}; lens+=("${#m}")
    done < <(printf '%s\n' "$t" | grep -aobE -e "$re" || true)
    for ((k = ${#offs[@]} - 1; k >= 0; k--)); do
      t="${t:0:offs[k]}***${t:offs[k]+lens[k]}"
    done
  done
  if ((${#t} > 200)); then t="${t:0:200}..."; fi
  printf '%s' "$t"
}

where() {  # $1 file[:line] label -> as is for a public set, else with private values masked
  if ((SHOW_RE)); then printf '%s' "$1"; else mask "$1" path; fi
}

skip_file() {  # $1 repo path, $2 1 for a src: pattern
  [[ $1 == "$PAT_REL" ]] || [[ $2 == 1 && $1 == tests/* ]]
}

grep_into_hits() {  # grep args...: matches into $TMP/hits; dies on a grep error
  local rc=0
  grep "$@" > "$TMP/hits" || rc=$?
  ((rc < 2)) || die "grep failed"
}

# Splits a unified diff (-U0, optionally with "commit <sha>" lines) into parallel row files:
# text = added line, meta = "file<TAB>label"; paths = new file path, pmeta = its label.
DIFF_AWK='
function unq(p) { if (p ~ /^".*"$/) p = substr(p, 2, length(p) - 2); return p }
function newpath(p) { print p > pf; print sha p > pm }
/^commit [0-9a-f]+$/ { sha = substr($2, 1, 10) " "; inh = 0; next }
/^diff --git / { inh = 0; np = 0; dg = ""; r = substr($0, 14)
  if ($0 ~ /^diff --git a\//) dg = substr(r, 1, (length(r) - 3) / 2); file = dg; next }
/^diff --cc / { inh = 0; np = 0; dg = ""; file = unq(substr($0, 11)); next }
/^@@@+ / { match($0, /^@+/); np = RLENGTH - 1; h = $0; ln = 0
  while (match(h, /\+[0-9]+/)) { ln = substr(h, RSTART + 1, RLENGTH - 1) + 0; h = substr(h, RSTART + RLENGTH) }
  inh = 1; next }
# merge commit (--cc): np parent columns; a line with no "-" column is in the result, a "+" column marks it added
inh && np { pre = substr($0, 1, np); if (pre ~ /-/) next
  if (pre ~ /\+/) { print substr($0, np + 1) > tf; print file "\t" sha file ":" ln > mf }; ln++; next }
inh && /^\+/ { print substr($0, 2) > tf; print file "\t" sha file ":" ln > mf; ln++; next }
/^@@ / { if (match($0, /\+[0-9]+/)) ln = substr($0, RSTART + 1, RLENGTH - 1) + 0; inh = 1; next }
inh { next }
/^new file mode/ { if (dg != "") newpath(dg); next }
/^(rename|copy) to / { newpath(unq(substr($0, index($0, " to ") + 4))); next }
/^\+\+\+ / { p = substr($0, 5); if (p != "/dev/null") { p = unq(p); sub(/^b\//, "", p); file = p }; next }
'
parse_diff() { awk -v tf="$TMP/text" -v mf="$TMP/meta" -v pf="$TMP/paths" -v pm="$TMP/pmeta" "$DIFF_AWK"; }

scan_rows() {  # content patterns over $TMP/text, path patterns over $TMP/paths
  local i rec n meta=() pmeta=()
  mapfile -t meta < "$TMP/meta"
  mapfile -t pmeta < "$TMP/pmeta"
  for i in "${!C_RE[@]}"; do
    grep_into_hits -naE -e "${C_RE[i]}" -- "$TMP/text"
    while IFS= read -r rec; do
      n=$((${rec%%:*} - 1))
      skip_file "${meta[n]%%$'\t'*}" "${C_SRC[i]}" && continue
      hit "$(where "${meta[n]#*$'\t'}"): $(label "${C_NUM[i]}" "${C_RE[i]}"): $(mask "${rec#*:}")"
    done < "$TMP/hits"
  done
  for i in "${!P_RE[@]}"; do
    grep_into_hits -naE -e "${P_RE[i]}" -- "$TMP/paths"
    while IFS= read -r rec; do
      hit "$(where "${pmeta[${rec%%:*} - 1]}"): path $(label "${P_NUM[i]}" "${P_RE[i]}")"
    done < "$TMP/hits"
  done
}

# Message rows: text = message line, meta = "[<sha> ]message:<line>". --msg input is a message file
# (comment lines and everything below the scissors line skipped, as git strips them; line numbers stay
# those of the file); --push input is "git log -z --format=%H%n%B" with NUL turned into \001.
MSG_AWK='
BEGIN { hdr = gl }
gl && /^\001[0-9a-f]+$/ { hdr = 1; sub(/^\001/, "") }
hdr { sha = substr($0, 1, 10) " "; hdr = 0; ln = 0; next }
gl { ln++; print > tf; print sha "message:" ln > mf; next }
index($0, cc " ------------------------ >8 ------------------------") == 1 { exit }
index($0, cc) == 1 { next }
{ print > tf; print "message:" NR > mf }
'
parse_msg() {  # $1 1 for git log input, $2 comment char
  awk -v gl="$1" -v cc="${2:-#}" -v tf="$TMP/mtext" -v mf="$TMP/mmeta" "$MSG_AWK"
}

scan_msg_rows() {  # content (src: included) and msg: patterns over $TMP/mtext
  local i rec meta=() res=("${C_RE[@]}" "${M_RE[@]}") nums=("${C_NUM[@]}" "${M_NUM[@]}")
  mapfile -t meta < "$TMP/mmeta"
  for i in "${!res[@]}"; do
    grep_into_hits -naE -e "${res[i]}" -- "$TMP/mtext"
    while IFS= read -r rec; do
      hit "${meta[${rec%%:*} - 1]}: $(label "${nums[i]}" "${res[i]}"): $(mask "${rec#*:}" msg)"
    done < "$TMP/hits"
  done
}

scan_msg() {  # $1 commit message file
  local cc
  [[ -f $1 ]] || die "message file $1 not found"
  cc=$(git config --get core.commentChar 2>/dev/null || true)
  [[ -n $cc && $cc != auto ]] || cc='#'
  parse_msg 0 "$cc" < "$1"
  scan_msg_rows
}

scan_staged() {
  git -c core.quotepath=off diff --cached -U0 --no-color --no-ext-diff | parse_diff
  scan_rows
}

scan_push() {  # $1 remote name or url; stdin: <local ref> <local sha> <remote ref> <remote sha>
  local lsha rsha pos=() neg=()
  while read -r _ lsha _ rsha; do
    [[ -z ${lsha:-} || $lsha =~ $ZERO_RE ]] && continue
    pos+=("$lsha")
    if [[ -n ${rsha:-} && ! $rsha =~ $ZERO_RE ]] && git cat-file -e "$rsha^{commit}" 2>/dev/null; then
      neg+=("$rsha")
    fi
  done
  ((${#pos[@]})) || return 0
  if [[ -n $1 ]] && git config --get "remote.$1.url" >/dev/null 2>&1; then neg+=("--remotes=$1"); fi
  git -c core.quotepath=off log -p --cc -U0 --no-color --no-ext-diff --format='commit %H' \
    "${pos[@]}" --not "${neg[@]}" -- | parse_diff
  scan_rows
  git log -z --format='%H%n%B' "${pos[@]}" --not "${neg[@]}" -- | tr '\0' '\001' | parse_msg 1
  scan_msg_rows
}

scan_tree() {
  local f i rec all=() files=() src=()
  while IFS= read -r -d '' f; do
    all+=("$f")
    [[ $f != "$PAT_REL" && -f $f && ! -L $f ]] || continue
    files+=("$f")
    [[ $f == tests/* ]] || src+=("$f")
  done < <(git ls-files -z)
  printf '%s\n' "${all[@]}" > "$TMP/paths"
  cp "$TMP/paths" "$TMP/pmeta"
  scan_rows  # path patterns only: $TMP/text is empty
  for i in "${!C_RE[@]}"; do
    if ((C_SRC[i])); then set -- "${src[@]}"; else set -- "${files[@]}"; fi
    (($#)) || continue
    grep_into_hits -nIHZE -e "${C_RE[i]}" -- "$@"
    while IFS= read -r f && IFS= read -r rec; do
      hit "$(where "$f"):${rec%%:*}: $(label "${C_NUM[i]}" "${C_RE[i]}"): $(mask "${rec#*:}")"
    done < <(tr '\0' '\n' < "$TMP/hits")
  done
  ((HITS)) || echo "leakcheck: OK (${#files[@]} files, $((${#C_RE[@]} + ${#P_RE[@]})) patterns)"
}

put() {  # $1 path: write stdin there atomically, mode 755
  local tmp
  tmp=$(mktemp "$1.XXXXXX")
  cat > "$tmp"
  chmod 755 "$tmp"
  mv -f -- "$tmp" "$1"
}

hook_state() {  # $1 hook path, $2 marker that makes it ours -> new | ours | pm | foreign
  local cur
  [[ -L $1 ]] && die "$1 is a symlink; refused"
  [[ -e $1 ]] || { echo new; return; }
  cur=$(<"$1")
  if [[ $cur == *"$2"* || ($cur == *"$PM_MARK"* && $cur == *"${1##*/}.d"*) ]]; then echo ours
  elif [[ $cur == *"$PM_MARK"* ]]; then echo pm
  else echo foreign; fi
}

install_chain() {  # $1 hooks dir, $2 hook name, $3 its state, $4 leakcheck args: <name>.d/leakcheck + runner
  local h=$1/$2 d=$1/$2.d
  mkdir -p -- "$d"
  printf '#!/bin/sh\nexec "${0%%/*}/../leakcheck.sh" %s\n' "$4" | put "$d/leakcheck"
  echo "leakcheck: installed $2.d/leakcheck"
  case $3 in
    ours) echo "leakcheck: $h already runs $2.d; kept" ;;
    pm) echo "leakcheck: $h is pm's tracker hook without $2.d; run 'pmctl hooks install' so it" \
      "chains $2.d (this check is off until then)" >&2 ;;
    *)
      if [[ $3 == foreign ]]; then  # --force: the old hook keeps running, first in the chain
        cp -p -- "$h" "$h.leakcheck-old"; cp -p -- "$h" "$d/00-previous"
        echo "leakcheck: the old hook runs as $2.d/00-previous (copy: $h.leakcheck-old)"
      fi
      put "$h" <<EOF
#!/bin/sh
# git hooks chain runner ($2.d)
# Runs every executable in $2.d/ in name order and stops at the first failure.
for h in "\${0%/*}/$2.d"/*; do
  [ -f "\$h" ] && [ -x "\$h" ] || continue
  "\$h" "\$@" || exit \$?
done
exit 0
EOF
      echo "leakcheck: installed the chain runner $h" ;;
  esac
}

install_hooks() {  # $1 1 with --force
  local hooks common pp pcs pms pps
  hooks=$(realpath -m -- "$(git rev-parse --git-path hooks)")
  common=$(realpath -m -- "$(git rev-parse --git-common-dir)")
  [[ $hooks == "$common"/* ]] ||
    die "core.hooksPath ($hooks) is outside this repo's git dir; install the hooks there by hand"
  pp=$hooks/pre-push
  pcs=$(hook_state "$hooks/pre-commit" "$CHAIN_MARK")
  pms=$(hook_state "$hooks/commit-msg" "$MSG_CHAIN_MARK")
  pps=$(hook_state "$pp" "$PUSH_MARK")
  [[ $pcs != foreign || $1 == 1 ]] ||
    die "$hooks/pre-commit exists and is not a chain runner; refused (--force moves it to pre-commit.d/00-previous)"
  [[ $pms != foreign || $1 == 1 ]] ||
    die "$hooks/commit-msg exists and is not a chain runner; refused (--force moves it to commit-msg.d/00-previous)"
  [[ $pps != foreign || $1 == 1 ]] ||
    die "$pp exists and is not leakcheck's; refused (--force keeps it as pre-push.leakcheck-old)"
  [[ $SELF == "$hooks/leakcheck.sh" ]] || put "$hooks/leakcheck.sh" < "$SELF"
  echo "leakcheck: installed $hooks/leakcheck.sh"
  install_chain "$hooks" pre-commit "$pcs" --staged
  install_chain "$hooks" commit-msg "$pms" '--msg "$1"'
  if [[ $pps == foreign ]]; then
    cp -p -- "$pp" "$pp.leakcheck-old"; echo "leakcheck: kept the old hook as $pp.leakcheck-old"
  fi
  printf '#!/bin/sh\n%s\nexec "${0%%/*}/leakcheck.sh" --push "$@"\n' "$PUSH_MARK" | put "$pp"
  echo "leakcheck: installed $pp"
}

main() {
  local mode='' force=0 pats='' explicit=0 args=()
  while (($#)); do
    case $1 in
      --staged | --push | --tree | --install) [[ -z $mode ]] || die "one mode only"; mode=${1#--} ;;
      --msg) [[ -z $mode ]] || die "one mode only"; (($# > 1)) || die "--msg needs a file"
        mode=msg; args+=("$(realpath -m -- "$2")"); shift ;;
      --force) force=1 ;;
      --patterns) (($# > 1)) || die "--patterns needs a file"; pats=$2 explicit=1; shift ;;
      -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$SELF"; exit 0 ;;
      -*) die "unknown option $1 (see --help)" ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  [[ -n $mode ]] || die "usage: leakcheck.sh --staged | --msg <file> | --push <remote> <url> | --tree | --install [--force]"
  TOP=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git work tree"
  TOP=$(realpath -- "$TOP")
  if [[ $mode == install ]]; then cd "$TOP"; install_hooks "$force"; return; fi
  [[ -n $pats ]] || pats=${PM_PRIVATE_PATTERNS:-${HOME:-}/.config/pm/private-patterns.txt}
  if [[ ! -f $pats ]]; then
    [[ $mode == tree && $explicit == 1 ]] && die "pattern file $pats not found"
    echo "leakcheck: no pattern file ($pats); skipped" >&2
    exit 0
  fi
  pats=$(realpath -- "$pats")
  cd "$TOP"
  load_patterns "$pats"
  TMP=$(mktemp -d)
  trap 'rm -rf -- "$TMP"' EXIT
  : > "$TMP/text"; : > "$TMP/meta"; : > "$TMP/paths"; : > "$TMP/pmeta"; : > "$TMP/mtext"; : > "$TMP/mmeta"
  case $mode in
    staged) scan_staged ;;
    msg) scan_msg "${args[0]}" ;;
    push) scan_push "${args[0]:-}" ;;
    tree) scan_tree ;;
  esac
  if ((HITS)); then
    [[ $mode == msg ]] && echo "leakcheck: reword the commit message (it is public once pushed)" >&2
    echo "leakcheck: $HITS hit(s); move the value into pm.toml or a private plugin (docs/CONFIG.md)," \
      "or fix the pattern file ($pats)" >&2
    exit 1
  fi
}

main "$@"
