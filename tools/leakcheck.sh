#!/usr/bin/env bash
# leakcheck: keep private values (host names, LAN addresses, home paths, keys) out of git.
#
# Usage:
#   tools/leakcheck.sh --staged                lines added in the index (the pre-commit hook)
#   tools/leakcheck.sh --push <remote> <url>   commits not yet on <remote> (the pre-push hook; refs on stdin;
#                                              a new branch pushed to a bare URL scans its whole history)
#   tools/leakcheck.sh --tree                  every tracked text file (CI: --patterns tools/leak-public.txt)
#   tools/leakcheck.sh --install [--force]     install the hooks: pre-commit.d/leakcheck (run by a chain
#                                              runner in pre-commit) and pre-push
# Pattern file: --patterns FILE, else $PM_PRIVATE_PATTERNS, else ~/.config/pm/private-patterns.txt.
# One GNU ERE per line; '#' comments and blank lines are skipped. Patterns are numbered from 1.
#   ERE         content pattern (added lines, file text)
#   path:ERE    forbidden file path (repo-relative)
#   src:ERE     content pattern that does not apply to files under tests/
# A hit prints "file:line: pattern #N: <line with the match masked>"; the matched text is never printed,
# the pattern itself and unmasked paths only when the pattern file is tracked in the repo (a public set).
# Exit: 0 clean (or no pattern file in --staged/--push), 1 hits, 2 usage or setup error.
# shellcheck disable=SC2016  # awk and hook bodies are literal text on purpose
set -euo pipefail
export LC_ALL=C

CHAIN_MARK='# git hooks chain runner (pre-commit.d)'
PM_MARK='# pm tracker hook'
PUSH_MARK='# leakcheck pre-push hook'
ZERO_RE='^0+$'
SELF=$(realpath -- "${BASH_SOURCE[0]}")
C_RE=() C_NUM=() C_SRC=() P_RE=() P_NUM=()
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
      *) re=$line; C_RE+=("$re"); C_NUM+=("$n"); C_SRC+=(0) ;;
    esac
    rc=0; grep -qE -e "$re" </dev/null || rc=$?
    ((rc < 2)) || die "pattern #$n is not a valid ERE"
  done < "$1"
}

label() {  # $1 pattern number, $2 regex
  if ((SHOW_RE)); then printf 'pattern #%s (%s)' "$1" "$2"; else printf 'pattern #%s' "$1"; fi
}

mask() {  # $1 text [$2 1: also path patterns] -> every match replaced by ***, cut to 200 chars
  local t=$1 re rec m k offs lens res=("${C_RE[@]}")
  [[ ${2:-0} == 1 ]] && res+=("${P_RE[@]}")
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
  if ((SHOW_RE)); then printf '%s' "$1"; else mask "$1" 1; fi
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
  if [[ $cur == *"$2"* || ($cur == *"$PM_MARK"* && $cur == *pre-commit.d*) ]]; then echo ours
  elif [[ $cur == *"$PM_MARK"* ]]; then echo pm
  else echo foreign; fi
}

install_hooks() {  # $1 1 with --force
  local hooks common pc pp pcs pps
  hooks=$(realpath -m -- "$(git rev-parse --git-path hooks)")
  common=$(realpath -m -- "$(git rev-parse --git-common-dir)")
  [[ $hooks == "$common"/* ]] ||
    die "core.hooksPath ($hooks) is outside this repo's git dir; install the hooks there by hand"
  pc=$hooks/pre-commit pp=$hooks/pre-push
  pcs=$(hook_state "$pc" "$CHAIN_MARK")
  pps=$(hook_state "$pp" "$PUSH_MARK")
  [[ $pcs != foreign || $1 == 1 ]] ||
    die "$pc exists and is not a chain runner; refused (--force moves it to pre-commit.d/00-previous)"
  [[ $pps != foreign || $1 == 1 ]] ||
    die "$pp exists and is not leakcheck's; refused (--force keeps it as pre-push.leakcheck-old)"
  mkdir -p -- "$hooks/pre-commit.d"
  [[ $SELF == "$hooks/leakcheck.sh" ]] || put "$hooks/leakcheck.sh" < "$SELF"
  printf '#!/bin/sh\nexec "${0%%/*}/../leakcheck.sh" --staged\n' | put "$hooks/pre-commit.d/leakcheck"
  echo "leakcheck: installed $hooks/leakcheck.sh and pre-commit.d/leakcheck"
  case $pcs in
    ours) echo "leakcheck: $pc already runs pre-commit.d; kept" ;;
    pm) echo "leakcheck: $pc is pm's tracker hook without pre-commit.d; run 'pmctl hooks install' so it" \
      "chains pre-commit.d (the staged check is off until then)" >&2 ;;
    *)
      if [[ $pcs == foreign ]]; then  # --force: the old hook keeps running, first in the chain
        cp -p -- "$pc" "$pc.leakcheck-old"; cp -p -- "$pc" "$hooks/pre-commit.d/00-previous"
        echo "leakcheck: the old hook runs as pre-commit.d/00-previous (copy: $pc.leakcheck-old)"
      fi
      put "$pc" <<EOF
#!/bin/sh
$CHAIN_MARK
# Runs every executable in pre-commit.d/ in name order and stops at the first failure.
for h in "\${0%/*}/pre-commit.d"/*; do
  [ -f "\$h" ] && [ -x "\$h" ] || continue
  "\$h" "\$@" || exit \$?
done
exit 0
EOF
      echo "leakcheck: installed the chain runner $pc" ;;
  esac
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
      --force) force=1 ;;
      --patterns) (($# > 1)) || die "--patterns needs a file"; pats=$2 explicit=1; shift ;;
      -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$SELF"; exit 0 ;;
      -*) die "unknown option $1 (see --help)" ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  [[ -n $mode ]] || die "usage: leakcheck.sh --staged | --push <remote> <url> | --tree | --install [--force]"
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
  : > "$TMP/text"; : > "$TMP/meta"; : > "$TMP/paths"; : > "$TMP/pmeta"
  case $mode in
    staged) scan_staged ;;
    push) scan_push "${args[0]:-}" ;;
    tree) scan_tree ;;
  esac
  if ((HITS)); then
    echo "leakcheck: $HITS hit(s); move the value into pm.toml or a private plugin (docs/CONFIG.md)," \
      "or fix the pattern file ($pats)" >&2
    exit 1
  fi
}

main "$@"
