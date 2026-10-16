"""pm.chat.ask: AskUserQuestion form from the transcript, matched to the pane (tabs, current, keys, previews)."""
import json
import sys
from pathlib import Path

from pm.chat import ask as A
from pm.chat import live
from pm.core.models import AgentKind, AgentState
from pm.security.mask import Masker

RULE = "─" * 100
PROMPT = ["❯ Ask me two questions."]


def entry(questions):
    return {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Two questions."},
        {"type": "tool_use", "name": "AskUserQuestion", "input": {"questions": questions}}]}}


FORM_AB = [
    {"question": "Which option best describes your preference for header A?", "header": "A", "multiSelect": False,
     "options": [{"label": f"Option {i}", "description": f"Choice {i} for A."} for i in (1, 2, 3)]},
    {"question": "Which options apply for header B? (select all that apply)", "header": "B", "multiSelect": True,
     "options": [{"label": f"Option {i}", "description": f"Choice {i} for B."} for i in (1, 2, 3)]},
]

PAGE_A = PROMPT + [RULE, "←  ☐ A  ☐ B  ✔ Submit  →", "Which option best describes your preference for header A?",
    "❯ 1. Option 1", "     First choice.", "  2. Option 2", "     Second choice.", "  3. Option 3", "     Third.",
    "  4. Type something.", RULE, "  5. Chat about this",
    "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"]

PAGE_B = PROMPT + [RULE, "←  ☒ A  ☐ B  ✔ Submit  →", "Which options apply for header B? (select all that apply)",
    "❯ 1. [✔] Option 1", "         First.", "  2. [ ] Option 2", "         Second.", "  3. [ ] Option 3",
    "         Third.", "  4. [ ] Type something", "     Submit", RULE, "  5. Chat about this",
    "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"]

REVIEW = PROMPT + [RULE, "←  ☒ A  ☒ B  ✔ Submit  →", "Review your answers", " ● Which option best describes?",
    "   → Option 3", "Ready to submit your answers?", "❯ 1. Submit answers", "  2. Cancel"]

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "askq"  # real Claude Code 2.1.296 captures


def cap(name):
    return [ln.rstrip() for ln in (FIX / name).read_text().splitlines()]


# the transcript tool_use input that produced the captures (Q1 with previews, Q2 plain)
FORM_REAL = [
    {"question": "Which layout for the demo page?", "header": "Layout", "multiSelect": False, "options": [
        {"label": "Grid", "description": "Cards in a 3-column grid",
         "preview": "+-----+-----+-----+\n| [1] | [2] | [3] |\n| [4] | [5] | [6] |\n+-----+-----+-----+"},
        {"label": "List", "description": "One row per item",
         "preview": "+-----------------+\n| 1. First item   |\n| 2. Second item  |\n| 3. Third item   |\n+-----------------+"}]},
    {"question": "Which theme should the demo use?", "header": "Theme", "multiSelect": False, "options": [
        {"label": "Light", "description": "White background"},
        {"label": "Dark", "description": "Near-black background"}]},
]


def merged(page, form):
    ask, start = A.find_ask(page)
    return A.merge(ask, A.ask_form(entry(form)), page, start)


def test_ask_form_reads_questions_bounded_and_masked(monkeypatch):
    monkeypatch.setattr(sys.modules["pm.security.mask"], "_active", Masker(literals=("hunter2-secret",)))
    qs = [{"question": "Use hunter2-secret?", "header": "H" * 99, "multiSelect": True, "options": [
        {"label": f"L{i}", "description": "d" * 999, "preview": "hunter2-secret " + "p" * 9000} for i in range(9)]}] * 6
    form = A.ask_form(entry(qs))
    assert len(form) == A.MAX_QUESTIONS and len(form[0].options) == A.MAX_FORM_OPTIONS
    q = form[0]
    assert q.multi and "hunter2-secret" not in q.question and len(q.header) <= A.MAX_HEADER
    o = q.options[0]
    assert o.key == "" and len(o.description) <= A.MAX_DESC and len(o.preview) == A.MAX_PREVIEW
    assert "hunter2-secret" not in o.preview
    assert A.ask_form({"message": {"content": [{"type": "text", "text": "x"}]}}) == ()
    assert A.ask_form(entry([{"question": ""}, "junk", {"question": "ok", "options": "x"}]))[0].options == ()


def test_merge_first_page_keys_and_tabs():
    ask = merged(PAGE_A, FORM_AB)
    assert ask.current == 0 and len(ask.questions) == 2
    q0, q1 = ask.questions
    assert [(o.key, o.label, o.description) for o in q0.options] == [
        ("1", "Option 1", "Choice 1 for A."), ("2", "Option 2", "Choice 2 for A."), ("3", "Option 3", "Choice 3 for A.")]
    assert all(o.key == "" for o in q1.options) and q1.multi and not q0.answered
    assert ask.text == FORM_AB[0]["question"]
    assert A.other_key(ask) == "4" and [o.key for o in ask.options][-2:] == ["Left", "Right"]


def test_merge_second_page_answered_and_multiselect():
    ask = merged(PAGE_B, FORM_AB)
    assert ask.current == 1 and ask.questions[0].answered and not ask.questions[1].answered
    assert [o.key for o in ask.questions[1].options] == ["1", "2", "3"]
    assert A.other_key(ask) == "4"


def test_merge_review_step():
    ask = merged(REVIEW, FORM_AB)
    assert ask.current == 2 and all(q.answered for q in ask.questions)
    assert ask.text.startswith("Review your answers")


def test_merge_mismatch_keeps_pane_ask():
    other = [dict(FORM_AB[0], question="Something else entirely?"), FORM_AB[1]]
    ask, start = A.find_ask(PAGE_A)
    assert A.merge(ask, A.ask_form(entry(other)), PAGE_A, start) is ask
    assert A.merge(ask, A.ask_form(entry(FORM_AB[:1])), PAGE_A, start) is ask  # 1 form question, 2 tabs
    relabeled = [dict(FORM_AB[0], options=[{"label": "Nope"}]), FORM_AB[1]]
    assert A.merge(ask, A.ask_form(entry(relabeled)), PAGE_A, start) is ask
    assert A.merge(None, A.ask_form(entry(FORM_AB)), PAGE_A, 0) is None


def test_real_preview_question_digit_then_enter():
    for name in ("cap_q1_preview.txt", "cap_submit.txt"):  # in cap_submit "2" only moved the cursor
        page = cap(name)
        ask, start = A.find_ask(page)
        assert [(o.key, o.label) for o in ask.options] == [
            ("1 Enter", "Grid"), ("2 Enter", "List"), ("2 Down Enter", "Chat about this"),
            ("BTab", "← Back"), ("Tab", "Next →")], name
        assert ask.text == "Which layout for the demo page?"
        m = A.merge(ask, A.ask_form(entry(FORM_REAL)), page, start)
        assert m.current == 0 and not any(q.answered for q in m.questions)
        assert [o.key for o in m.questions[0].options] == ["1 Enter", "2 Enter"]
        assert m.questions[0].options[1].preview.startswith("+---") and m.questions[0].options[0].description
        assert A.other_key(m) is None
        assert A.note_keys(page, 2) == "2 n"  # preview question: the digit only moves the cursor


def test_real_plain_question_after_first_answered():
    page = cap("cap_q2.txt")
    ask, start = A.find_ask(page)
    assert [(o.key, o.label) for o in ask.options] == [
        ("1", "Light"), ("2", "Dark"), ("3", "Type something."), ("4", "Chat about this"),
        ("Left", "← Back"), ("Right", "Next →")]
    m = A.merge(ask, A.ask_form(entry(FORM_REAL)), page, start)
    assert m.current == 1 and m.questions[0].answered and not m.questions[1].answered
    assert [(o.key, o.label, o.description) for o in m.questions[1].options] == [
        ("1", "Light", "White background"), ("2", "Dark", "Near-black background")]
    assert all(o.key == "" for o in m.questions[0].options)
    assert A.other_key(m) == "3"


def test_real_review_step():
    page = cap("cap_after2.txt")
    ask, start = A.find_ask(page)
    assert [(o.key, o.label) for o in ask.options][:2] == [("1", "Submit answers"), ("2", "Cancel")]
    m = A.merge(ask, A.ask_form(entry(FORM_REAL)), page, start)
    assert m.current == 2 and all(q.answered for q in m.questions)
    assert m.text.splitlines() == ["Review your answers", "● Which layout for the demo page?", "→ List",
                                   "● Which theme should the demo use?", "→ Dark", "Ready to submit your answers?"]


def test_real_answered_question_revisited_chat_focused():
    page = cap("cap_q1_answered_chat.txt")  # "2. List ✔" keeps its ❯ while "❯ Chat about this" has focus
    ask, start = A.find_ask(page)
    assert [(o.key, o.label) for o in ask.options] == [
        ("1 Enter", "Grid"), ("2 Enter", "List ✔"), ("2 Down Enter", "Chat about this"),
        ("BTab", "← Back"), ("Tab", "Next →")]
    assert [A.chosen(o.label) for o in ask.options[:2]] == [False, True]
    m = A.merge(ask, A.ask_form(entry(FORM_REAL)), page, start)
    assert m.current == 0 and m.questions[0].answered and not m.questions[1].answered
    assert [(o.key, o.label) for o in m.questions[0].options] == [("1 Enter", "Grid"), ("2 Enter", "List")]
    assert A.note_keys(page, 1) == "1 n"


def test_note_keys_plain_question_uses_arrows():
    page = cap("cap_q2.txt")
    assert A.note_keys(page, 1) == "n" and A.note_keys(page, 2) == "Down n"


def test_note_keys_without_cursor():
    assert A.note_keys(["plain text"], 1) is None


def test_chat_view_uses_form_from_transcript(tmp_path, monkeypatch):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": "ask me"}}) + "\n"
                    + json.dumps(entry(FORM_AB)) + "\n")
    info = live.read_transcript_info(path)
    assert [q.header for q in info.ask_form] == ["A", "B"]
    assert live.read_transcript(path)[3] == FORM_AB[0]["question"]
    from pm.core.models import Agent
    agent = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/",
                  state=AgentState.WAITING)
    monkeypatch.setattr(live, "_claude_info", lambda a: (info, 1.0))
    view = live.chat_view(None, agent, "\n".join(PAGE_B))
    assert view.asking.questions[1].options[0].key == "1"
    assert view.asking.current == 1 and view.asking.questions[0].answered


def test_chat_view_real_preview_capture(tmp_path, monkeypatch):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(entry(FORM_REAL)) + "\n")
    info = live.read_transcript_info(path)
    from pm.core.models import Agent
    agent = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=1, cwd="/",
                  state=AgentState.WAITING)
    monkeypatch.setattr(live, "_claude_info", lambda a: (info, 1.0))
    raw = (FIX / "cap_q1_preview.txt").read_text()
    ask = live.chat_view(None, agent, raw).asking
    assert ask.current == 0 and ask.questions[0].header == "Layout"
    assert [o.key for o in ask.questions[0].options] == ["1 Enter", "2 Enter"]
    ask = live.chat_view(None, agent, (FIX / "cap_after2.txt").read_text()).asking
    assert ask.current == 2 and "→ Dark" in ask.text
