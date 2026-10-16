"""All HTTP routers, in app inclusion order (route order matters)."""

from __future__ import annotations

from fastapi import APIRouter

from pm.web.routes import (
    agents as _agents,
)
from pm.web.routes import (
    agentsauth as _agentsauth,
)
from pm.web.routes import (
    agentsedit as _agentsedit,
)
from pm.web.routes import (
    agentspage as _agentspage,
)
from pm.web.routes import (
    ask as _ask,
)
from pm.web.routes import (
    atfiles as _atfiles,
)
from pm.web.routes import (
    auditpage as _auditpage,
)
from pm.web.routes import (
    autoexit as _autoexit,
)
from pm.web.routes import (
    backlog as _backlog,
)
from pm.web.routes import (
    builder as _builder,
)
from pm.web.routes import (
    canvas as _canvas,
)
from pm.web.routes import (
    chat as _chat,
)
from pm.web.routes import (
    chatitems as _chatitems,
)
from pm.web.routes import (
    clienterrors as _clienterrors,
)
from pm.web.routes import (
    control as _control,
)
from pm.web.routes import (
    events as _events,
)
from pm.web.routes import (
    exit as _exit,
)
from pm.web.routes import (
    fork as _fork,
)
from pm.web.routes import (
    gitops as _gitops,
)
from pm.web.routes import (
    gitremotes as _gitremotes,
)
from pm.web.routes import (
    issues as _issues,
)
from pm.web.routes import (
    nav as _nav,
)
from pm.web.routes import (
    newproject as _newproject,
)
from pm.web.routes import (
    next as _next,
)
from pm.web.routes import (
    ntfy as _ntfy,
)
from pm.web.routes import (
    outbox as _outbox,
)
from pm.web.routes import (
    pages as _pages,
)
from pm.web.routes import (
    passkey as _passkey,
)
from pm.web.routes import (
    plans as _plans,
)
from pm.web.routes import (
    projectdocs as _projectdocs,
)
from pm.web.routes import (
    projectsettings as _projectsettings,
)
from pm.web.routes import (
    qchat as _qchat,
)
from pm.web.routes import (
    recap as _recap,
)
from pm.web.routes import (
    reloadneed as _reloadneed,
)
from pm.web.routes import (
    search as _search,
)
from pm.web.routes import (
    servers as _servers,
)
from pm.web.routes import (
    settings as _settings,
)
from pm.web.routes import (
    slash_output as _slash_output,
)
from pm.web.routes import (
    ssh as _ssh,
)
from pm.web.routes import (
    stackops as _stackops,
)
from pm.web.routes import (
    subagents as _subagents,
)
from pm.web.routes import (
    term as _term,
)
from pm.web.routes import (
    tmuxpages as _tmuxpages,
)
from pm.web.routes import (
    todo as _todo,
)
from pm.web.routes import (
    usage as _usage,
)

# Sub-routers of the pages: carried by ``pages.router`` (tests build apps from it alone), so they are not
# listed in ROUTERS again. Added after the pages' own routes; none of their paths overlap.
_pages.router.include_router(_projectdocs.router)
_pages.router.include_router(_tmuxpages.router)
_pages.router.include_router(_auditpage.router)

ROUTERS: list[APIRouter] = [
    _pages.router,
    _control.router,
    _agents.router,
    _agentsauth.router,  # before agentspage: GET /agents/{name} would catch /agents/auth.json
    _agentspage.router,
    _agentsedit.router,
    _ask.router,
    _usage.router,
    _nav.router,
    _search.router,
    _newproject.router,
    _exit.router,
    _autoexit.router,
    _fork.router,
    _qchat.router,
    _next.router,
    _backlog.router,
    _todo.router,
    _issues.router,
    _plans.router,
    _chat.router,
    _chatitems.router,
    _outbox.router,
    _reloadneed.router,
    _subagents.router,
    _builder.router,
    _atfiles.router,
    _canvas.router,
    _recap.router,
    _slash_output.router,
    _events.router,
    _passkey.router,
    _term.router,
    _ntfy.router,
    _servers.router,
    _stackops.router,
    _clienterrors.router,
    _settings.router,
    _ssh.router,
    _gitremotes.router,
    _gitops.router,
    _projectsettings.router,
]
