"""Claude Code AskUserQuestion screens: numbered options with indented descriptions, tab bar, review;
and the "Needs you" card for a form matched to the pane."""
import re

from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import chat_view, login, set_chat_view

from pm.chat import live as chat
from pm.core.models import AgentState, Ask, AskOption, AskQuestion

RULE = "─" * 100
PROMPT = ["❯ Use the AskUserQuestion tool right now with 2 questions: header A (single select, 3 options each with a",
          "  description) and header B (multiSelect, 3 options each with a description). Do nothing else."]

PAGE_A = PROMPT + [RULE, "←  ☐ A  ☐ B  ✔ Submit  →", "Which option best describes your preference for header A?",
    "❯ 1. Option 1", "     First choice for header A, a single-select option with a short explanation.",
    "  2. Option 2", "     Second choice for header A, a single-select option with a short explanation.",
    "  3. Option 3", "     Third choice for header A, a single-select option with a short explanation.",
    "  4. Type something.", RULE, "  5. Chat about this",
    "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"]

PAGE_B = PROMPT + [RULE, "←  ☒ A  ☐ B  ✔ Submit  →", "Which options apply for header B? (select all that apply)",
    "❯ 1. [ ] Option 1", "         First choice for header B, a multi-select option with a short",
    "  2. [ ] Option 2", "         Second choice for header B, a multi-select option with a short",
    "  3. [ ] Option 3", "         Third choice for header B, a multi-select option with a short",
    "  4. [ ] Type something", "     Submit", RULE, "  5. Chat about this",
    "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"]

REVIEW = PROMPT + [RULE, "←  ☒ A  ☒ B  ✔ Submit  →", "Review your answers", " ● Which option best describes?",
    "   → Option 3", "Ready to submit your answers?", "❯ 1. Submit answers", "  2. Cancel"]


def opts(ask):
    return [(o.key, o.label) for o in ask.options]


def test_single_select_page_uses_digit_keys():
    ask, _ = chat.find_ask(PAGE_A)
    assert ask and ask.kind == "choice"
    assert opts(ask) == [("1", "Option 1"), ("2", "Option 2"), ("3", "Option 3"),
                         ("4", "Type something."), ("5", "Chat about this"),
                         ("Left", "← Back"), ("Right", "Next →")]
    assert ask.text.startswith("Which option best") and "A  ☐ B" not in ask.text and "AskUserQuestion" not in ask.text


def test_multiselect_page():
    ask, _ = chat.find_ask(PAGE_B)
    assert [k for k, _ in opts(ask)] == ["1", "2", "3", "4", "5", "Left", "Right"]
    assert "select all that apply" in ask.text and "☒" not in ask.text


def test_review_page():
    ask, _ = chat.find_ask(REVIEW)
    assert opts(ask)[:2] == [("1", "Submit answers"), ("2", "Cancel")]
    assert "Review your answers" in ask.text and "☒" not in ask.text


# ------------------------------------------------------------------ "Needs you" card for a matched form
OPTS_A = tuple(AskOption(str(i), f"Option {i}", f"Choice {i} <b>") for i in (1, 2, 3))
PANE_OPTS = (AskOption("1", "Option 1"), AskOption("2", "Option 2"), AskOption("3", "Option 3"),
             AskOption("4", "Type something."), AskOption("5", "Chat about this"),
             AskOption("Left", "← Back"), AskOption("Right", "Next →"))


def form_ask(current=0, preview=False, multi=False):
    pv = tuple(AskOption(o.key, o.label, o.description, "┌─┐\n│x│ <i>" if preview else "") for o in OPTS_A)
    q0 = AskQuestion("Backlog #", "Which count?", pv)
    q1 = AskQuestion("Canvas", "Builder?", tuple(AskOption("", f"B{i}") for i in (1, 2)), multi=multi, answered=False)
    if current == 1:
        q0 = AskQuestion(q0.header, q0.question, tuple(AskOption("", o.label) for o in OPTS_A), answered=True)
        q1 = AskQuestion("Canvas", "Builder?", tuple(AskOption(str(i), f"B{i}") for i in (1, 2)), multi=multi)
    return Ask("choice", (q0, q1)[current].question, PANE_OPTS, (q0, q1), current)


def card(env, monkeypatch, ask):
    s, c = env
    login(s, c)
    set_chat_view(monkeypatch, chat_view(state=AgentState.WAITING, asking=ask))
    return c.get("/a/claude-12/chat").text


def keyforms(t):
    return re.findall(r'<form[^>]*action="/a/claude-12/key"[^>]*>.*?</form>', t, re.S)


def test_card_numbered_rows_tabs_and_nav(env, monkeypatch):
    t = card(env, monkeypatch, form_ask())
    assert "Needs you" in t and 'class="ask-chip">Backlog #<' in t and "1 of 2" in t
    rows = re.findall(r'<li class="ask-row">.*?</li>', t, re.S)
    assert len(rows) == 3  # Type something / Chat about this / Back / Next are not option rows
    assert 'name="key" value="1"' in rows[0] and "<strong>Option 1</strong>" in rows[0]
    assert 'class="ask-d">Choice 1 &lt;b&gt;<' in rows[0]
    forms = keyforms(t)
    keys = [re.search(r'name="key" value="([^"]*)"', f).group(1) for f in forms]
    assert "Left" not in keys  # first question: no Back
    assert keys.count("Right") == 2  # Next + the Canvas tab
    assert "Right Right" in keys  # Submit tab: to the review step
    assert "5" in keys and "Escape" in keys  # Chat about this, Cancel
    assert 'aria-current="step">☐ Backlog #<' in t and "☐ Canvas" in t
    assert 'action="/a/claude-12/ask"' in t and 'action="/a/claude-12/message"' not in t
    assert "ask-pv" not in t and "Add note" not in t
    esc = next(f for f in forms if 'value="Escape"' in f)
    assert "hx-confirm=" in esc


def test_card_second_question_multiselect(env, monkeypatch):
    ask = form_ask(current=1, multi=True)
    ask = Ask(ask.kind, ask.text, (AskOption("1", "[✔] B1"), AskOption("2", "[ ] B2")) + PANE_OPTS[2:],
              ask.questions, 1)
    t = card(env, monkeypatch, ask)
    assert "2 of 2" in t and 'class="ask-chip">Canvas<' in t and "☒ Backlog #" in t
    assert 'aria-pressed="true"' in t and 'aria-pressed="false"' in t and "toggle" in t
    keys = [re.search(r'name="key" value="([^"]*)"', f).group(1) for f in keyforms(t)]
    assert "Left" in keys and "Right" in keys  # Back (also the Backlog # tab) and Next / Submit


def test_card_previews_and_notes(env, monkeypatch):
    t = card(env, monkeypatch, form_ask(preview=True))
    assert 'class="ask-rows has-pv"' in t
    assert t.count('<pre class="ask-pv"') == 3 and "│x│ &lt;i&gt;" in t
    notes = re.findall(r'<form class="ask-noteform"[^>]*>.*?</form>', t, re.S)
    assert len(notes) == 3 and 'name="note" value="2"' in notes[1] and 'action="/a/claude-12/ask"' in notes[1]


def test_card_review_step(env, monkeypatch):
    ask = form_ask()
    review = Ask("choice", "Review your answers", (AskOption("1", "Submit answers"), AskOption("2", "Cancel"),
                 AskOption("Left", "← Back"), AskOption("Right", "Next →")),
                 tuple(AskQuestion(q.header, q.question, q.options, answered=True) for q in ask.questions), 2)
    t = card(env, monkeypatch, review)
    assert "Review" in t and 'aria-current="step">✔ Submit<' in t and "<strong>Submit answers</strong>" in t
    assert 'name="text"' not in t  # no free text on the review step


def test_card_preview_question_keys_and_chat(env, monkeypatch):
    pv = tuple(AskOption(f"{i} Enter", f"L{i}", "", "+--+") for i in (1, 2))
    q0 = AskQuestion("Layout", "Which layout?", pv)
    q1 = AskQuestion("Theme", "Which theme?", (AskOption("", "Light"), AskOption("", "Dark")))
    pane = pv + (AskOption("2 Down Enter", "Chat about this"), AskOption("BTab", "← Back"),
                 AskOption("Tab", "Next →"))
    t = card(env, monkeypatch, Ask("choice", "Which layout?", pane, (q0, q1), 0))
    rows = re.findall(r'<li class="ask-row">.*?</li>', t, re.S)
    assert len(rows) == 2 and 'value="1 Enter"' in rows[0] and '<span class="ask-n">1</span>' in rows[0]
    assert 'name="note" value="2"' in rows[1]
    keys = [re.search(r'name="key" value="([^"]*)"', f).group(1) for f in keyforms(t)]
    assert "2 Down Enter" in keys and "Escape" in keys
    assert "Tab" in keys and "Tab Tab" in keys and "Right" not in keys  # Next, Submit tab: Tab on a preview question
    assert 'placeholder="Type an answer&hellip;"' not in t  # no "Type something" on a preview question


def test_card_review_lists_answers(env, monkeypatch):
    ask = form_ask()
    text = "Review your answers\n● Which count?\n→ Option 2\n● Builder?\n→ B1\nReady to submit your answers?"
    review = Ask("choice", text, (AskOption("1", "Submit answers"), AskOption("2", "Cancel"),
                 AskOption("Left", "← Back"), AskOption("Right", "Next →")),
                 tuple(AskQuestion(q.header, q.question, q.options, answered=True) for q in ask.questions), 2)
    t = card(env, monkeypatch, review)
    assert "<dt>Which count?</dt><dd>Option 2</dd>" in t and "<dd>B1</dd>" in t
    keys = [re.search(r'name="key" value="([^"]*)"', f).group(1) for f in keyforms(t)]
    assert "1" in keys and "2" in keys and "Escape" not in keys  # Cancel is the pane's row 2 here


def test_card_marks_chosen_option(env, monkeypatch):
    pv = (AskOption("1 Enter", "Grid", "", "+--+"), AskOption("2 Enter", "List", "", "+--+"))
    q0 = AskQuestion("Layout", "Which layout?", pv, answered=True)
    q1 = AskQuestion("Theme", "Which theme?", (AskOption("", "Light"),))
    pane = (AskOption("1 Enter", "Grid"), AskOption("2 Enter", "List ✔"),
            AskOption("2 Down Enter", "Chat about this"), AskOption("BTab", "← Back"), AskOption("Tab", "Next →"))
    t = card(env, monkeypatch, Ask("choice", "Which layout?", pane, (q0, q1), 0))
    rows = re.findall(r'<li class="ask-row">.*?</li>', t, re.S)
    assert 'aria-current="true"' not in rows[0] and 'aria-current="true"' in rows[1]
    assert "<strong>List</strong>" in rows[1] and "List ✔" not in t and "(chosen)" in rows[1]
