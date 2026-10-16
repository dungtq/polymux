"""Shared dataclass contract. Sub-agents code against these; the integrator owns changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    VIEW = 0
    SAFE = 1
    MESSAGE = 2
    TERMINAL = 3  # not enabled in v1


class AgentKind(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    DEVIN = "devin"
    CURSOR = "cursor-agent"
    ANTIGRAVITY = "antigravity"
    OTHER = "other"


class AgentState(StrEnum):
    WAITING = "waiting"  # permission prompt / question
    RUNNING = "running"
    IDLE = "idle"  # finished, at prompt
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TmuxPane:
    id: str  # %n
    window_id: str  # @n
    session_id: str  # $n
    pid: int
    command: str
    cwd: str
    active: bool = False
    title: str = ""
    dead: bool = False  # remain-on-exit: process exited, pane still listed


@dataclass(frozen=True)
class TmuxWindow:
    id: str  # @n
    session_id: str
    index: int
    name: str
    active: bool = False
    panes: tuple[TmuxPane, ...] = ()


@dataclass(frozen=True)
class TmuxSession:
    id: str  # $n
    name: str
    attached: int = 0
    protected: bool = True  # fail closed
    windows: tuple[TmuxWindow, ...] = ()

    @property
    def current_window_id(self) -> str | None:
        """The one window tmux reports as this session's current (#{window_active}, read per session
        from the live list-panes). The single source for every `*` / highlight: None unless exactly one
        window is active, never the first window and never a guess."""
        act = [w.id for w in self.windows if w.active]
        return act[0] if len(act) == 1 else None


@dataclass(frozen=True)
class GitStatus:
    branch: str | None
    dirty: int = 0
    ahead: int = 0  # vs gitdev/<branch>
    behind: int = 0
    last_commit: str = ""
    has_gitdev: bool = False
    last_commit_ts: float = 0.0  # epoch seconds of HEAD commit; 0 when unknown (keep last: gitctl builds positionally)


@dataclass(frozen=True)
class StackStatus:
    running: int = 0
    total: int = 0
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class TodoProgress:
    done: int = 0
    total: int = 0
    open_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class Project:
    name: str
    path: str
    git: GitStatus | None = None
    stack: StackStatus | None = None
    todo: TodoProgress | None = None
    sessions: tuple[str, ...] = ()  # tmux session ids bound to it
    agent_ids: tuple[str, ...] = ()
    pane_ids: tuple[str, ...] = ()  # live tmux panes whose current path is in it


@dataclass(frozen=True)
class Agent:
    id: str  # permanent: "<kind>-<6 hex>" from the session identity, e.g. claude-7f3a9c (agents/registry.py)
    kind: AgentKind
    pane_id: str  # %n
    session_id: str  # $n
    pid: int
    cwd: str
    project: str | None = None
    state: AgentState = AgentState.UNKNOWN
    state_since: float = 0.0  # epoch seconds
    confidence: float = 1.0  # <1.0 for pane-text heuristics
    session_uuid: str | None = None  # Claude sessionId (plugin transcript links)
    cpu_pct: float = 0.0
    rss_mb: float = 0.0
    age_s: float = 0.0
    label: str = ""  # display name "<tmux session>-<window index>-<kind>", e.g. dev-pm-1-claude; id stays the URL key
    last_active: float = 0.0  # epoch s of REAL activity (agent transcript/session mtime, tmux window activity); 0 = unknown; never reset by pm restarts
    window_name: str = ""  # tmux window name (e.g. a1-hub), shown next to the label; label uses the window INDEX
    ident: str | None = None  # session identity (Claude sessionId, other kinds: session file id); source of the permanent id
    transcript: str = ""  # path of the agent's own session file (for the read-only page of an ended agent)
    done_at: float = 0.0  # epoch s of the agent's LAST RESPONSE (newest assistant entry of its session file); 0 = unknown. Unlike last_active it ignores tmux redraws and file touches

    def done_ts(self) -> float:
        """When the agent last finished a response ("Done at"); falls back to last_active, then state_since."""
        return self.done_at or self.last_active or self.state_since or 0.0


@dataclass(frozen=True)
class Launch:
    project: str
    kind: AgentKind
    model: str | None = None
    permission_mode: str | None = None
    worktree_branch: str | None = None
    prompt: str = ""
    effort: str | None = None  # claude --effort / codex model_reasoning_effort
    permission_extra: str | None = None  # codex approval policy / cursor sandbox


@dataclass(frozen=True)
class AuditEntry:
    ts: float
    client: str
    action: str
    target: str
    result: str  # ok | denied | error
    detail: dict[str, str | int] = field(default_factory=dict)  # never message text


@dataclass(frozen=True)
class AskOption:
    key: str  # quick key pm may send: 1 2 3 y n Enter Escape
    label: str
    description: str = ""  # AskUserQuestion option description (masked, <= 400 chars)
    preview: str = ""  # AskUserQuestion option preview, monospace/markdown (masked, <= 4 KB)


@dataclass(frozen=True)
class AskQuestion:
    """One question of a Claude Code AskUserQuestion form (from the transcript tool_use input)."""

    header: str  # short tab label (<= 40 chars)
    question: str  # masked, <= 1500 chars
    options: tuple[AskOption, ...] = ()  # keys valid only for the current question
    multi: bool = False  # multiSelect
    answered: bool = False  # tab bar shows it ticked


@dataclass(frozen=True)
class Ask:
    """What the agent is waiting on. text is masked and bounded (<= 1500 chars)."""

    kind: str  # "permission" | "question" | "choice"
    text: str
    options: tuple[AskOption, ...] = ()
    # AskUserQuestion form when the transcript has one that matches the pane (else empty: pane-only ask)
    questions: tuple[AskQuestion, ...] = ()
    current: int = 0  # index into questions of the one shown in the pane (== len(questions): Submit step)


@dataclass(frozen=True)
class ChatView:
    """Chat-style summary of an agent: only the latest message, plus any open question."""

    agent_id: str
    state: AgentState
    last_role: str = ""  # "assistant" | "user" | ""
    last_text: str = ""  # masked, bounded (<= 20000 chars; the UI shows it in full)
    last_ts: float = 0.0
    source: str = "none"  # "transcript" | "pane" | "none"
    asking: Ask | None = None
    task: str = ""  # current task: first line of the newest user message (may be empty)
    task_ts: float = 0.0  # timestamp of that message (0 = unknown)
    queued: str = ""  # Claude: message typed while busy, not processed yet (masked, bounded)


@dataclass(frozen=True)
class ToolCount:
    """Tool calls of one name inside one assistant turn (counts only, never inputs/outputs)."""

    name: str  # tool name, bounded (<= 64 chars), e.g. "Bash", "Read", "mcp__srv__tool"
    n: int


@dataclass(frozen=True)
class ChatStep:
    """One step of an assistant turn, shown as a single line (live turn box, folded steps list).

    Masked (``render_pane``) before it is stored, like the message text."""

    kind: str  # "text" | "tool" | "thinking"
    name: str = ""  # tool name for kind "tool" (<= 64 chars), else ""
    line: str = ""  # one line, no newlines, <= 160 chars: first line of the text / thinking, or the
    #                tool's target (Bash command, file path, pattern, URL, query); "" when none


@dataclass(frozen=True)
class ChatMsg:
    """One stored chat message (``pm.chat.log``, table ``chat_messages`` in ``settings.db_file``).

    A user message is one real user prompt. An assistant message is one whole turn: every
    assistant entry between two real user prompts, texts joined with a blank line, thinking
    joined, tool calls counted by name. Text comes from the agent's session store (never the
    pane) and is masked (``render_pane``) before it is stored.
    """

    seq: int  # chat_messages rowid: increases in conversation order for one agent
    uid: str  # stable id from the source (user entry uuid; assistant turn = "<user uid>:a")
    conv: str  # conversation key (one session file / session id); changes on /clear
    role: str  # "user" | "assistant"
    ts: float  # epoch s of the first entry of the message (0 = unknown)
    text: str  # masked, bounded (<= 20000 chars)
    thinking: str = ""  # masked, bounded (<= 20000 chars); "" when the store has none or it is redacted
    tools: tuple[ToolCount, ...] = ()  # assistant only, most used first
    updated: float = 0.0  # epoch s of the last ingest that changed this row (a turn grows while running)
    steps: tuple[ChatStep, ...] = ()  # assistant only, conversation order, newest 200 kept

    @property
    def tool_total(self) -> int:
        return sum(t.n for t in self.tools)


@dataclass(frozen=True)
class ChatItem:
    """One append-only chat item (``pm.chat.items``, table ``chat_items``; docs/CHAT-SYNC.md).

    A user prompt, one assistant text block, one tool call, or a turn end. Once stored, only a
    tool item's ``status``/``ended``/``note`` (and a user item's ``outbox`` link) may change; every
    change bumps ``rev``. Text is masked (``render_pane``) and bounded before it is stored.
    """

    seq: int  # rowid: conversation order for one agent; DOM id "i-<seq>"
    rev: int  # global change counter: a client cursor "rev > N" gets new AND changed items
    uid: str  # stable source id: entry uuid, "<entry uuid>:<block idx>", tool_use_id, "<turn uid>:end"
    conv: str
    kind: str  # "user" | "text" | "tool" | "end"
    turn: str  # uid of the user prompt that opened the turn ("" when unknown)
    ts: float  # epoch s (start for a tool)
    text: str = ""  # user / text: markdown source (masked, <= 20000 chars); end: ""
    name: str = ""  # tool: tool name (<= 64 chars); text: model name when known
    line: str = ""  # tool: one masked line, its target (command, path, pattern, URL); <= 160 chars
    status: str = ""  # tool: "run" | "ok" | "err" | "" (source has no results); others ""
    ended: float = 0.0  # tool: epoch s of its result (0 = still running / unknown)
    note: str = ""  # tool: short masked result note ("exit 1", "background"); end: "N replies · N tools · 41s"
    parent: int = 0  # tool: seq of the text item it follows (0 = none, tools before any text)
    outbox: int = 0  # user: chat_outbox id it was delivered from (0 = typed in the pane / older)


@dataclass(frozen=True)
class OutboxMsg:
    """One message typed in pm's composer (``pm.chat.outbox``, table ``chat_outbox``).

    state: "saved" (stored, not sent) -> "wait" (agent busy; held until the turn ends) ->
    "sending" -> "inpane" (typed + Enter) -> "done" (matched to a transcript user entry; ``item``
    set). Side exits: "queued" (Send now: typed into the agent's own queue), "failed" (``error``
    set; Retry puts it back to "saved"), "cancelled". Text is the user's own input (not masked).
    """

    id: int
    agent_id: str
    text: str  # <= message max length; attachments are already inlined as paths by the composer
    state: str
    created: float
    updated: float
    tries: int = 0
    error: str = ""  # short, no content ("pane gone", "not confirmed")
    item: int = 0  # chat_items seq once linked ("done")


@dataclass(frozen=True)
class NtfyItem:
    """One message from the ntfy topic (read-only mirror shown in pm)."""

    id: str
    time: float  # epoch seconds
    title: str
    message: str
    tags: tuple[str, ...] = ()
    project: str | None = None  # absolute project path taken from the tags, when one matches
    click: str = ""
    priority: int = 3


# ---- v5 shell: sidebar (project / tmux session / agent lists) ----


@dataclass(frozen=True)
class SideRow:
    key: str  # compared with Sidebar.current (project name, tmux session id, agent name)
    href: str
    name: str
    dot: str = ""  # "" = spacer, else running | waiting | failed | idle | unknown
    count: str = ""  # right-hand muted text ("3", "2w"); "" = none
    live: bool = True  # False = no tmux open; hidden by default when Sidebar.fold
    unread: int = 0  # unread ntfy messages for this row (muted pill); 0 = none


@dataclass(frozen=True)
class SideNode:
    """One window or pane row of the tmux tree (hrefs carry tmux ids, never names)."""

    href: str
    name: str
    num: str  # window index or pane id (%n)
    dot: str = ""
    tag: str = ""  # right-hand muted text: "3 panes", "exited"
    title: str = ""
    pane: bool = False
    on: bool = False  # the current agent's row, decided by the server
    kind: str = ""  # program/agent kind shown as a kpill (claude, devin, fish ...); "" = none
    agent: bool = False  # name is an agent label: drop the trailing -<kind> like the inbox
    cur: bool = False  # window row only: tmux's current window of the session (shown as a `*`)
    model: str = ""  # agent rows: short model name for the agent-name pill; "" = unknown (kind pill instead)


@dataclass(frozen=True)
class SideHist:
    """One ended agent of the same project."""

    id: str
    name: str
    kind: str
    ended: float  # epoch s, rendered with the `ago` filter
    dur: str = ""
    resume: bool = False  # same condition as agent_ended.html offers Resume
    model: str = ""  # short model name for the agent-name pill; "" = unknown (kind pill instead)


@dataclass(frozen=True)
class Sidebar:
    label: str  # "Projects" | "Sessions" | "Agents" (phone button + head)
    rows: tuple[SideRow, ...] = ()
    current: str = ""  # the row with this key is rendered aria-current server-side
    all_href: str = ""  # "All projects" link in the head ("" = none)
    all_label: str = ""
    filter_label: str = "Filter"
    fold: bool = False  # head shows Expand/Collapse; rows with live=False are hidden until expanded
    # tmux mode (agent chat page): TMUX tree of the agent's session, then HISTORY of ended agents.
    tmux: bool = False
    title: str = ""  # head title (the tmux session name); "" = label
    tree: tuple[SideNode, ...] = ()
    hist: tuple[SideHist, ...] = ()
    hist_total: int = 0
    proj: str = ""  # tmux mode: folder name of the viewed agent's cwd (the project the whole sidebar belongs to)
    proj_dir: str = ""  # tmux mode: that cwd in full, shown under the head


# ---- Agents inventory (agentsinv.py, read-only; rendered by the Agents pages) ----
# Secret rule: no field below may hold a secret value. MCP env is NAMES only, commands are reduced
# to binary + short args with credentials dropped.


@dataclass(frozen=True)
class AgentInfo:
    name: str  # slug used in URLs: claude, codex, devin, cursor, cline, copilot, windsurf, antigravity, ...
    label: str  # display name
    installed: bool = False
    version: str = ""  # "" = unknown
    binary: str = ""  # path of the executable ("" = not found)
    config_dir: str = ""  # e.g. ~/.claude (display form, ~ kept)
    rules_file: str = ""  # e.g. ~/.claude/CLAUDE.md
    skills_dir: str = ""  # "" = the agent has no skills dir of its own
    mcp_config: str = ""  # "" = none found
    skills_enabled: int = 0  # skills visible to this agent
    mcp_count: int = 0


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str = ""  # first line, <= 160 chars
    path: str = ""  # canonical SKILL.md directory, display form
    scope: str = "shared"  # shared (~/.agents/skills) | agent (lives in one agent's own dir)
    owner: str = ""  # agent name when scope == "agent"
    enabled_for: tuple[str, ...] = ()  # agent names that see it (symlink or direct read)
    kind: str = "skill"


@dataclass(frozen=True)
class RuleInfo:
    name: str  # file name shown in lists
    agent: str  # agent name ("" = canonical)
    path: str  # display form
    summary: str = ""
    mode: str = "file"  # file | symlink | copy
    target: str = ""  # symlink target, display form
    lines: int = 0
    status: str = "ok"  # ok | drift | broken | missing


@dataclass(frozen=True)
class ScriptInfo:
    name: str
    path: str
    kind: str = "script"  # script | hook | shim
    purpose: str = ""
    used_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpServerInfo:
    name: str
    agent: str  # agent whose config declares it
    transport: str = "stdio"  # stdio | http | sse | ws | unknown
    command: str = ""  # binary + short args, credentials dropped
    host: str = ""  # host only for URL servers (never userinfo or query)
    env_names: tuple[str, ...] = ()  # NAMES only
    config_path: str = ""  # display form
    enabled: bool | None = True  # None = unknown


@dataclass(frozen=True)
class Finding:
    severity: str  # info | warn
    text: str
    kind: str = ""  # security | rules | skills | index | shims | notes ("" = notes)
    agent: str = ""  # agent the finding is about ("" = none or several)
    fix: tuple[tuple[str, str], ...] = ()  # form fields for POST /agents/review (action first); () = no fix


@dataclass(frozen=True)
class Inventory:
    agents: tuple[AgentInfo, ...] = ()
    skills: tuple[SkillInfo, ...] = ()
    rules: tuple[RuleInfo, ...] = ()
    scripts: tuple[ScriptInfo, ...] = ()
    mcp: tuple[McpServerInfo, ...] = ()
    findings: tuple[Finding, ...] = ()
    built_at: float = 0.0
