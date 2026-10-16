"""New project dialog (PRD §6.4): plan the steps of a spec, then run them one by one and stop at the first failure.

Core steps: folder (under ``dev_root``), template files, README, git init, the ``push_remote`` remote (from
``git_remote_template``; skipped when not configured), first commit, first agent. Docker stack, hostname, VM and the
like come only from plugins (``new_project_steps`` hook in ``pm.web.plugins``). The agent launch and the plugin steps
are injected by the caller: this package never imports ``pm.agents`` or ``pm.web``.

``run`` yields one ``StepResult`` per step: ``done`` / ``skipped`` until a step fails, then ``failed`` for that one
and ``not_run`` for every later one, so the caller always knows what was done and what not. Nothing is undone.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pm.core.config import Settings
from pm.core.errors import PmError
from pm.core.fsutil import atomic_write, safe_join
from pm.core.models import AgentKind
from pm.git import ctl as gitctl
from pm.git import remote as gitremote
from pm.projects.projects import NEW_NAME, SKIP_DIRS, NewProjectError, create_project

StepFn = Callable[[Settings, "Spec", Path], str]  # (settings, spec, project dir) -> result text; raises on failure

STEP_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}")
OPTION_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_OPTIONS, MAX_OPTION_LEN = 20, 200
MAX_TEMPLATE_FILES, MAX_TEMPLATE_BYTES = 50, 256 * 1024
MAX_PROMPT = 20_000
COMMIT_MSG = "Initial commit"
_TPL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
# form fields parse_spec reads itself; every other well-formed key goes to Spec.options (plugin steps)
CORE_FIELDS = frozenset({"csrf", "name", "template", "readme", "git", "remote", "commit", "agent", "model",
                         "effort", "permission_mode", "permission_extra", "prompt", "confirm_bypass"})


@dataclass(frozen=True)
class AgentSpec:
    kind: str
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    permission_extra: str | None = None
    prompt: str = ""


@dataclass(frozen=True)
class Spec:
    name: str
    template: str = "empty"
    readme: bool = True
    git: bool = True
    remote: bool = True
    commit: bool = False
    agent: AgentSpec | None = None
    options: Mapping[str, str] = field(default_factory=dict)  # extra form fields for plugin steps (stack, vm, ...)


@dataclass(frozen=True)
class Step:
    """One planned step. ``run`` is None for a step that is only reported (skipped)."""
    id: str
    text: str
    kind: str
    run: StepFn | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class StepResult:
    id: str
    ok: bool
    text: str
    state: str = "done"  # done | skipped | failed | not_run


def _flag(v: str | None, default: bool) -> bool:
    return default if v is None else v in ("1", "on", "true")


def _opt(v: str | None) -> str | None:
    v = (v or "").strip()
    return v[:100] or None


def parse_spec(fields: Mapping[str, str]) -> Spec:
    """A Spec from form/query fields (strings). Bad agent kind or prompt raises NewProjectError (400); the name is
    checked by ``check``."""
    kind = (fields.get("agent") or "").strip()
    agent = None
    if kind:
        try:
            if AgentKind(kind) is AgentKind.OTHER:
                raise ValueError(kind)
        except ValueError:
            raise NewProjectError("agent: unknown kind") from None
        prompt = fields.get("prompt") or ""
        if len(prompt) > MAX_PROMPT:
            raise NewProjectError("prompt too long")
        agent = AgentSpec(kind, _opt(fields.get("model")), _opt(fields.get("effort")),
                          _opt(fields.get("permission_mode")), _opt(fields.get("permission_extra")), prompt)
    options = {k: v[:MAX_OPTION_LEN] for k, v in fields.items()
               if isinstance(k, str) and isinstance(v, str) and k not in CORE_FIELDS and OPTION_KEY.fullmatch(k)}
    if len(options) > MAX_OPTIONS:
        raise NewProjectError("too many fields")
    git = _flag(fields.get("git"), True)
    return Spec(name=(fields.get("name") or "").strip(), template=(fields.get("template") or "empty").strip(),
                readme=_flag(fields.get("readme"), True), git=git,
                remote=git and _flag(fields.get("remote"), True), commit=git and _flag(fields.get("commit"), False),
                agent=agent, options=options)


# ------------------------------------------------------------------ templates
def templates(settings: Settings) -> list[dict]:
    """``project_templates`` with valid entries only: {name, description, files}; ``empty`` when none is valid."""
    out: list[dict] = []
    for t in settings.project_templates or ():
        name = t.get("name") if isinstance(t, dict) else None
        if not isinstance(name, str) or not _TPL_NAME.fullmatch(name) or any(x["name"] == name for x in out):
            continue
        files = t.get("files") or {}
        ok = (isinstance(files, dict) and len(files) <= MAX_TEMPLATE_FILES
              and all(isinstance(k, str) and isinstance(v, str) and len(v) <= MAX_TEMPLATE_BYTES
                      for k, v in files.items()))
        if ok:
            desc = t.get("description")
            out.append({"name": name, "description": desc[:200] if isinstance(desc, str) else "", "files": dict(files)})
    return out or [{"name": "empty", "description": "", "files": {}}]


def _template(settings: Settings, name: str) -> dict:
    t = next((t for t in templates(settings) if t["name"] == name), None)
    if t is None:
        raise NewProjectError("unknown template")
    return t


def remote_url(settings: Settings, name: str) -> str:
    """The URL of the new project's ``push_remote`` from ``git_remote_template`` ("" = not configured)."""
    tpl = settings.git_remote_template or ""
    return tpl.replace("{name}", name) if tpl else ""


# ------------------------------------------------------------------ check + plan
def check(settings: Settings, spec: Spec) -> None:
    """Everything that can be refused before a step runs: name, root, existing path, template, remote URL."""
    if not isinstance(spec.name, str) or not NEW_NAME.fullmatch(spec.name) or spec.name in SKIP_DIRS:
        raise NewProjectError("name: letters, digits, . _ - only (max 64, no leading dot)")
    root = Path(settings.dev_root).resolve()
    if not root.is_dir():
        raise NewProjectError("projects root is missing", 500)
    if os.path.lexists(root / spec.name):
        raise NewProjectError("a file or directory with that name already exists", 409)
    for rel in _template(settings, spec.template)["files"]:
        try:
            safe_join(root / spec.name, rel)
        except PmError:
            raise NewProjectError(f"template {spec.template!r}: bad file path") from None
    url = remote_url(settings, spec.name) if spec.remote else ""
    if url:
        try:
            gitctl.check_remote_url(url, tuple(settings.git_allowed_hosts) or gitctl.DEFAULT_GIT_HOSTS)
        except gitctl.GitDenied as e:
            raise NewProjectError(f"git_remote_template: {e}") from None


def plan(settings: Settings, spec: Spec, extra_steps: Sequence[Step] = (), *,
         launch: StepFn | None = None) -> list[Step]:
    """The ordered steps for ``spec`` (does not check it: see ``check``). ``extra_steps``: plugin steps, run after
    the git steps and before the agent; ``launch``: the first-agent launcher (``pm.web`` passes it)."""
    root = Path(settings.dev_root)
    steps = [Step("folder", f"Create folder {root / spec.name}", "folder", _mkdir)]
    files = (next((t for t in templates(settings) if t["name"] == spec.template), None) or {}).get("files") or {}
    if files:
        steps.append(Step("template", f"Write {len(files)} file(s) of template {spec.template}", "file", _template_files))
    if spec.readme and "README.md" not in files:
        steps.append(Step("readme", "Write README.md", "file", _readme))
    if spec.git:
        steps.append(Step("git", "git init", "git", _git_init))
    if spec.remote:
        url = remote_url(settings, spec.name)
        rn = settings.push_remote
        steps.append(Step("remote", f"Add remote {rn} {url}", "remote", _remote) if url else
                     Step("remote", f"Add remote {rn}: not configured (git_remote_template), skipped", "skip"))
    if spec.commit:
        steps.append(Step("commit", f"First commit ({COMMIT_MSG!r})", "commit", _commit))
    steps += list(extra_steps)
    if spec.agent is not None:
        a = spec.agent
        what = " ".join(x for x in (a.kind, a.model or "", f"({a.permission_mode})" if a.permission_mode else "") if x)
        steps.append(Step("agent", f"Launch {what} in {spec.name}", "agent", launch or _no_launcher))
    return steps


# ------------------------------------------------------------------ run
def run(settings: Settings, spec: Spec, extra_steps: Sequence[Step] = (), *,
        launch: StepFn | None = None) -> Iterator[StepResult]:
    """Check ``spec`` (raises NewProjectError before anything is changed), then return the step iterator."""
    check(settings, spec)
    return run_steps(settings, spec, plan(settings, spec, extra_steps, launch=launch))


def _short(e: BaseException) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def run_steps(settings: Settings, spec: Spec, steps: list[Step]) -> Iterator[StepResult]:
    """The step iterator over a ``plan`` of a ``check``-ed spec; each ``next`` runs one step (the caller picks the
    thread per step)."""
    path = Path(settings.dev_root).resolve() / spec.name
    for i, st in enumerate(steps):
        if st.run is None:
            yield StepResult(st.id, True, st.text, "skipped")
            continue
        try:
            text = st.run(settings, spec, path)
        except Exception as e:  # noqa: BLE001 - any failure (plugin code too) stops the run with its message
            yield StepResult(st.id, False, f"{st.text}: {_short(e)}", "failed")
            for rest in steps[i + 1:]:
                yield StepResult(rest.id, False, rest.text, "not_run")
            return
        yield StepResult(st.id, True, text if isinstance(text, str) and text else st.text, "done")


def _mkdir(settings: Settings, spec: Spec, path: Path) -> str:
    return f"Created {create_project(settings, spec.name)}"


def _template_files(settings: Settings, spec: Spec, path: Path) -> str:
    files = _template(settings, spec.template)["files"]
    for rel, text in files.items():
        f = safe_join(path, rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(f, text.replace("{name}", spec.name), mode=0o644)
    return f"Wrote {len(files)} file(s) of template {spec.template}"


def _readme(settings: Settings, spec: Spec, path: Path) -> str:
    atomic_write(path / "README.md", f"# {spec.name}\n", mode=0o644)
    return "Wrote README.md"


def _git_init(settings: Settings, spec: Spec, path: Path) -> str:
    try:
        gitctl.init_repo(path)
    except gitctl.GitError as e:
        raise NewProjectError(f"git init failed: {_short(e)[:160]}", 502) from None
    return "Initialised a git repository"


def _remote(settings: Settings, spec: Spec, path: Path) -> str:
    url = remote_url(settings, spec.name)
    rn = settings.push_remote
    gitremote.remote_add(path, rn, url, tuple(settings.git_allowed_hosts) or gitctl.DEFAULT_GIT_HOSTS)
    return f"Added remote {rn} {url}"


def _commit(settings: Settings, spec: Spec, path: Path) -> str:
    gitctl.run_git(path, ["add", "-A"])
    gitctl.run_git(path, ["commit", "-q", "--allow-empty", "-m", COMMIT_MSG])
    return f"Committed {COMMIT_MSG!r}"


def _no_launcher(settings: Settings, spec: Spec, path: Path) -> str:
    raise NewProjectError("agent launch is not available here", 500)
