"""Open asks in an agent pane, and the Claude Code AskUserQuestion form behind them.

``find_ask`` reads the (ANSI-stripped, masked) pane tail: numbered option lists, cursor menus, y/n and
Enter prompts. ``ask_form`` reads the pending AskUserQuestion tool_use input from a transcript entry
(questions, headers, option labels, descriptions, previews), and ``merge`` matches it to the pane: the
question under the tab bar is the current one, ticked tabs are answered, and the pane's digits become
the option keys. Every string passes ``render_pane`` (strip ANSI, then mask) and is bounded; nothing
here logs question, option or preview text.
"""

from __future__ import annotations

import re
from dataclasses import replace

from pm.chat.text import clean_text as _clean
from pm.core.models import Ask, AskOption, AskQuestion
from pm.security.mask import render_pane

MAX_ASK_TEXT = 1500
MAX_OPT_GAP = 4  # non-option lines allowed between two numbered options
MAX_OPTIONS = 9  # keys 1-9
ASK_WINDOW = 18  # non-blank tail lines searched for an open ask
FORM_WINDOW = 60  # non-blank tail lines searched when an AskUserQuestion form is open (tab bar / footer)
MAX_QUESTIONS = 4
MAX_FORM_OPTIONS = 5
MAX_HEADER = 40
MAX_LABEL = 200
MAX_DESC = 400
MAX_PREVIEW = 4096

_BOX = "─━│┃┌┐└┘├┤┬┴┼╭╮╯╰═║╔╗╚╝"
_BORDER_ONLY = re.compile(rf"^[\s{_BOX}\-=_]*$")
_TABBAR = re.compile(r"^[←→]?\s*(?:[☐☒☑✔]\s*\S+\s+)+.*Submit")
_RULE = re.compile(r"^[─━\-=]{20,}$")
_NUMBERED = re.compile(r"^\s*[❯>›●*]?\s*(\d{1,2})[.)]\s+(\S.*)$")
_YN = re.compile(r"[\(\[]\s*(y/n|Y/n|y/N|yes/no)\s*[\)\]]|\(y\)|\[y\]", re.IGNORECASE)
_ENTER = re.compile(r"press\s+enter(\s+to\s+\w+)?|enter\s+to\s+(continue|confirm|proceed)", re.IGNORECASE)
_PERM_WORDS = re.compile(r"proceed|allow|permission|approve|trust|\bYes\b.*\bNo\b", re.IGNORECASE)
_SPINNER = re.compile(r"^[✠-❏⠀-⣿·•●○*]?\s*\w+(ing|ed)?…?\s*\(.*(esc|tokens|\ds)\b.*\)\s*$", re.IGNORECASE)
_STATUS = re.compile(
    r"esc to interrupt|\? for shortcuts|shift\+tab|bypass permissions|auto-accept|"
    r"ctrl\+[a-z] to|tokens\s*[·|]|context left|\d+%\s*(context|left)|^\s*\w*\s*esc to",
    re.IGNORECASE,
)
_PROMPT = re.compile(r"^\s*([❯>›$#%]|┃\s*>)(\s|$)")
_CURSOR = re.compile(r"^[❯›>]\s+(\S.*)$")
_MENU_FOOT = re.compile(r"enter\s+to\s+(confirm|select|choose)|esc\s+to\s+cancel", re.IGNORECASE)
_FORM_FOOT = re.compile(r"enter\s+to\s+select.*esc\s+to\s+cancel", re.IGNORECASE)  # AskUserQuestion footer
_NOTES_FOOT = re.compile(r"\bn\s+to\s+add\s+notes", re.IGNORECASE)  # preview question footer
_PV_COL = re.compile(r"\s{2,}[─-╿].*$")  # preview box drawn right of an option label
_CHECK = re.compile(r"^\[[ x✔✓☒×]?\]\s*", re.IGNORECASE)  # multiSelect "[ ] " / "[✔] "
_TICK = re.compile(r"([☐☒☑✔])\s*([^☐☒☑✔]+)")
_CUR_NUM = re.compile(r"^\s*[❯›>]\s*(\d{1,2})[.)]\s")


_NAV_FOOT = re.compile(r"\bto\s+(navigate|switch\s+questions|cancel|amend)\b", re.IGNORECASE)


def hint_line(line: str) -> bool:
    """True for a key-hint footer ("Enter to select · ↑/↓ to navigate · Esc to cancel") or the status
    bar: chrome, never a question."""
    s = _unbox(line)
    return bool(_FORM_FOOT.search(s) or _MENU_FOOT.search(s) or _NOTES_FOOT.search(s)
                or _NAV_FOOT.search(s) or _STATUS.search(s))


def _tail_lines(text: str) -> list[str]:
    lines = text.splitlines()
    return [ln.rstrip() for ln in lines]


def _unbox(line: str) -> str:
    s = line.strip()
    s = s.strip("".join(c for c in _BOX if c in "│┃║")).strip()
    return s


def _find_menu(lines: list[str], window: list[tuple[int, str]]) -> tuple[Ask, int] | None:
    """Cursor menu without numbers ("❯ No, exit" / "Yes, I trust this folder" / "Enter to confirm").

    Each option's key is a space-separated key sequence: arrows from the cursor, then Enter.
    """
    foot = next((p for p in range(len(window) - 1, -1, -1) if _MENU_FOOT.search(window[p][1])), None)
    if foot is None:
        return None
    cur = next((p for p in range(foot - 1, -1, -1) if _CURSOR.match(window[p][1])), None)
    if cur is None:
        return None
    labels = [(_CURSOR.match(window[cur][1]).group(1).strip())]
    labels += [window[p][1].strip() for p in range(cur + 1, foot) if not _BORDER_ONLY.match(window[p][1])]
    labels = labels[:MAX_OPTIONS]
    if len(labels) < 2 or any(len(x) > 120 for x in labels):
        return None
    options = tuple(AskOption(" ".join(["Down"] * i + ["Enter"]), x) for i, x in enumerate(labels))
    qlines = [s for _, s in window[max(0, cur - 8):cur] if not _BORDER_ONLY.match(s)]
    joined = " ".join(qlines + labels)
    kind = "permission" if _PERM_WORDS.search(joined) else "choice"
    return Ask(kind, _clean("\n".join(qlines), MAX_ASK_TEXT), options), window[max(0, cur - 8)][0]


def _form_open(lines: list[str], idx: list[int]) -> bool:
    """An AskUserQuestion form fills the pane bottom: its footer, or a tab bar near the end."""
    tail = idx[-3:]
    if any(_FORM_FOOT.search(_unbox(lines[i])) for i in tail):
        return True
    return any(_TABBAR.search(_unbox(lines[i])) for i in idx[-ASK_WINDOW:])


_CHOSEN = " \u2714"  # an answered option, revisited: "2. List ✔" (kept on the pane option as the chosen mark)


def chosen(label: str) -> bool:
    """True for a pane option label that carries the answered mark."""
    return label.endswith(_CHOSEN)


def _numbered(lines: list[str], window: list[tuple[int, str]], form: bool) -> list[tuple[int, int, str]]:
    """(window pos, number, label) of every numbered line. In a form only left-column rows count (a preview
    box beside or below the options may hold its own "1." lines) and the preview column is cut off."""
    nums: list[tuple[int, int, str]] = []
    for pos, (i, s) in enumerate(window):
        m = _NUMBERED.match(lines[i].strip() if form else s)
        if m:
            label = _PV_COL.sub("", m.group(2)) if form else m.group(2)
            nums.append((pos, int(m.group(1)), label.strip(" " + _BOX)))
    return nums


def _preview_keys(options: tuple[AskOption, ...], after: list[str]) -> tuple[AskOption, ...]:
    """Preview question (footer "n to add notes"): a digit only moves the cursor, Enter answers, so option N
    is "N Enter". Its "Chat about this" has no digit: the last option's digit, Down past the divider, Enter."""
    out = tuple(AskOption(f"{o.key} Enter", o.label) for o in options)
    if options and any(s.lstrip("\u276f\u203a> ").lower().startswith("chat about this") for s in after):
        out += (AskOption(f"{options[-1].key} Down Enter", "Chat about this"),)
    return out


def _startup_trust_keys(options: tuple[AskOption, ...], window: list[tuple[int, str]]) -> tuple[AskOption, ...]:
    """Codex startup trust shortcuts only highlight; Enter grants the selected trust.

    The other shortcuts submit immediately, so adding Enter to those could act on the next screen.
    Match both the screen heading and the trust row, not arbitrary numbered permission prompts.
    """
    headings = {s for _, s in window}
    trust = {("Folder access", "1", "Trust and continue"),
             ("Hooks need review", "2", "Trust all and continue")}
    return tuple(replace(o, key=f"{o.key} Enter")
                 if any(h in headings and (o.key, o.label) == (k, label) for h, k, label in trust) else o
                 for o in options)


def find_ask(lines: list[str]) -> tuple[Ask | None, int]:
    """Open ask in the pane tail, plus the index of its first line in ``lines``."""
    idx = [i for i, ln in enumerate(lines) if _unbox(ln)]
    form = bool(idx) and _form_open(lines, idx)
    idx = idx[-(FORM_WINDOW if form else ASK_WINDOW):]
    if not idx:
        return None, len(lines)
    window = [(i, _unbox(lines[i])) for i in idx]

    # numbered option list: the last contiguous run ending at the 1.. sequence
    run: list[tuple[int, int, str]] = []
    for item in _numbered(lines, window, form):
        # indented description / divider lines may sit between options (AskUserQuestion)
        # (a form's preview box, notes hint and divider may sit before "Chat about this": any gap there)
        if run and 0 < item[0] - run[-1][0] <= (len(window) if form else MAX_OPT_GAP) and item[1] == run[-1][1] + 1:
            run.append(item)
        elif item[1] == 1:
            run = [item]
        else:
            run = []
    if len(run) >= 2 and run[0][1] == 1:
        first_pos = run[0][0]
        options = tuple(AskOption(str(n), label) for _, n, label in run[:MAX_OPTIONS] if n <= 9)
        # only footer-ish lines (hints) may follow the options; a form may also show a preview, notes hint, ...
        after = [s for _, s in window[run[-1][0] + 1:]]
        if (len(after) <= 3 or form) and options:
            lo = first_pos
            tabbed = False
            up = 2 * MAX_QUESTIONS + 4 if form else 8  # a form's review lists "● question" / "→ answer" pairs
            for pos in range(first_pos - 1, max(-1, first_pos - up - 1), -1):
                head = lines[window[pos][0]].lstrip()[:1]
                if head in ("╰", "└"):
                    break
                if _TABBAR.search(window[pos][1]):
                    tabbed = True
                    break  # the question starts below the tab bar
                if _RULE.match(window[pos][1]):
                    break  # full-width rule above the dialog
                lo = pos
                if head in ("╭", "┌"):
                    break
            qlines = [s for _, s in window[lo:first_pos] if not _BORDER_ONLY.match(s)]
            joined = " ".join(qlines + [o.label for o in options])
            kind = "permission" if _PERM_WORDS.search(joined) else "choice"
            preview = form and any(_NOTES_FOOT.search(s) for _, s in window[-3:])
            if preview:
                options = _preview_keys(options, after)
            elif not form:
                options = _startup_trust_keys(options, window)
            if tabbed:  # multi-question form: Left/Right switch tabs (the last tab is the review); a preview
                # question's arrows move the cursor, its footer says "Tab to switch questions"
                options += ((AskOption("BTab", "← Back"), AskOption("Tab", "Next →")) if preview
                            else (AskOption("Left", "← Back"), AskOption("Right", "Next →")))
            return Ask(kind, _clean("\n".join(qlines), MAX_ASK_TEXT), options), window[lo][0]

    menu = _find_menu(lines, window)
    if menu:
        return menu

    # y/n or Enter prompts: must be on one of the last 3 lines
    for pos in range(len(window) - 1, max(-1, len(window) - 4), -1):
        s = window[pos][1]
        if _YN.search(s):
            q = [t for _, t in window[max(0, pos - 3):pos + 1] if not _BORDER_ONLY.match(t)]
            return (Ask("permission" if _PERM_WORDS.search(" ".join(q)) else "question",
                        _clean("\n".join(q), MAX_ASK_TEXT),
                        (AskOption("y", "Yes"), AskOption("n", "No"))), window[max(0, pos - 3)][0])
        if _ENTER.search(s):
            q = [t for _, t in window[max(0, pos - 3):pos + 1] if not _BORDER_ONLY.match(t)]
            return (Ask("question", _clean("\n".join(q), MAX_ASK_TEXT),
                        (AskOption("Enter", "Continue"),)), window[max(0, pos - 3)][0])
    return None, len(lines)


def _box_top(lines: list[str], start: int) -> int:
    """If ``start`` is inside a drawn box, move up to the box's top border."""
    for i in range(min(start, len(lines)) - 1, max(-1, start - 13), -1):
        s = lines[i].lstrip()
        if s[:1] in ("╭", "┌"):
            return i
        if s[:1] in ("╰", "└"):
            break
    return start


# --------------------------------------------------------------------------
# AskUserQuestion form (transcript) and its match to the pane
# --------------------------------------------------------------------------

def _s(v: object) -> str:
    return v if isinstance(v, str) else ""


def _option(o: object) -> AskOption | None:
    if not isinstance(o, dict) or not _s(o.get("label")).strip():
        return None
    preview = render_pane(_s(o.get("preview"))).strip("\n")
    return AskOption("", _clean(o["label"], MAX_LABEL), _clean(_s(o.get("description")), MAX_DESC),
                     preview[:MAX_PREVIEW])


def _question(q: object) -> AskQuestion | None:
    if not isinstance(q, dict) or not _s(q.get("question")).strip():
        return None
    raw = q.get("options")
    opts = [o for o in map(_option, raw if isinstance(raw, list) else []) if o][:MAX_FORM_OPTIONS]
    return AskQuestion(_clean(_s(q.get("header")), MAX_HEADER), _clean(q["question"], MAX_ASK_TEXT),
                       tuple(opts), q.get("multiSelect") is True)


def ask_form(entry: dict) -> tuple[AskQuestion, ...]:
    """Questions of the AskUserQuestion tool_use in a transcript entry (masked, bounded), else ()."""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return ()
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
            qs = (block.get("input") or {}).get("questions")
            if isinstance(qs, list):
                return tuple(q for q in map(_question, qs[:MAX_QUESTIONS]) if q)
    return ()


def _fp(text: str) -> str:
    """Alphanumerics only: pane wrapping, bullets and ellipses still match the transcript."""
    return re.sub(r"[\W_]+", "", text.lower())


def _same(a: str, b: str, n: int = 48) -> bool:
    fa, fb = _fp(a), _fp(b)
    if len(fa) < 3 or len(fb) < 3:
        return False
    k = min(n, len(fa), len(fb))
    return fa[:k] == fb[:k]


def tab_states(lines: list[str], start: int) -> list[bool] | None:
    """Answered flags of the question tabs in the tab bar right above line ``start`` (Submit excluded)."""
    for i in range(min(start, len(lines)) - 1, max(-1, start - 4), -1):
        s = _unbox(lines[i])
        if not s:
            continue
        if not _TABBAR.search(s):
            return None
        tabs = [(t, name.strip(" ←→")) for t, name in _TICK.findall(s)]
        return [t != "☐" for t, name in tabs if not name.startswith("Submit")]
    return None


def _keyed(form_opts: tuple[AskOption, ...], pane_opts: list[AskOption]) -> tuple[AskOption, ...] | None:
    """Form options with the pane digit of the row that shows the same label, None on any mismatch."""
    out = []
    for o in form_opts:
        hit = next((p for p in pane_opts if _same(o.label, _CHECK.sub("", p.label))), None)
        if hit is None:
            return None
        out.append(replace(o, key=hit.key))
    return tuple(out)


def merge(ask: Ask | None, form: tuple[AskQuestion, ...], lines: list[str], start: int) -> Ask | None:
    """``ask`` with the transcript form attached when the pane shows that form, else ``ask`` unchanged.

    ``start`` is the ask's first line in ``lines`` (find_ask). The current question is the form question
    whose text is in the pane; the review step (``current == len(questions)``) has a ticked tab bar and a
    "Submit answers" row. Keys are set only on the current question's options.
    """
    if ask is None or not form:
        return ask
    ticks = tab_states(lines, start)
    if ticks is not None and len(ticks) != len(form):
        return ask
    digits = [o for o in ask.options if o.key[:1].isdigit()]  # "N", or "N Enter" on a preview question
    cur = next((i for i, q in enumerate(form) if _same(ask.text, q.question)), None)
    if cur is None:
        if ticks is None or not any(_same(o.label, "Submit answers") for o in digits):
            return ask
        cur = len(form)
    questions = []
    for i, q in enumerate(form):
        opts = _keyed(q.options, digits) if i == cur else q.options
        if opts is None:
            return ask
        questions.append(replace(q, options=opts, answered=bool(ticks and ticks[i])))
    text = form[cur].question if cur < len(form) else ask.text
    return Ask("choice", text, ask.options, tuple(questions), cur)


def note_keys(lines: list[str], n: int) -> str | None:
    """Keys that move the form cursor to option ``n`` and open its note ("n"), None without a cursor.

    On a preview question (footer "n to add notes") the digit only moves the cursor: "N n", whatever row has
    focus (an answered option keeps its ❯ while "❯ Chat about this" is focused). Else arrows from the ❯ row."""
    if any(_NOTES_FOOT.search(ln) for ln in [x for x in lines if x.strip()][-3:]):
        return f"{n} n"
    for ln in reversed(lines[-FORM_WINDOW * 2:]):
        m = _CUR_NUM.match(ln)
        if m:
            c = int(m.group(1))
            return " ".join(["Down"] * (n - c) + ["Up"] * (c - n) + ["n"])
    return None


def other_key(ask: Ask | None) -> str | None:
    """Digit of the form's "Type something" row (the free-text answer), None when the pane has none."""
    if ask is None:
        return None
    return next((o.key for o in ask.options if o.key.isdigit()
                 and _CHECK.sub("", o.label).lower().startswith("type something")), None)
