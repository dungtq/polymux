"""The ``dt`` theme (keyboard-first shell), loaded as a plugin (entry point ``pm.plugins: dt``): its Python side.

Tabs store, shell view model, /dt routes (docs/themes/dt/SHELL.md) and the view models the theme's templates read
(the ``dt_*`` template globals). Templates and static files: ``pm/web/themes/dt``. No pm.web.routes imports.
pm imports are done inside the hooks so loading this module does not depend on pm's import order.
"""

from __future__ import annotations

from pathlib import Path

DT_THEME = Path(__file__).parent.parent / "themes" / "dt"


class DtTheme:
    name = "dt"

    def __init__(self) -> None:
        self.settings = None

    def configure(self, cfg: dict, settings) -> None:
        self.settings = settings

    def themes(self) -> list:
        from pm.web.themes import Theme  # noqa: PLC0415

        return [Theme(name="dt", templates=DT_THEME / "templates", static=DT_THEME / "static", label="dt (keyboard)")]

    def routers(self) -> list:
        """The dt shell's /dt routes (tabs, sessions sidebar); every POST is behind mutation_guard."""
        from pm.web.dt import routes  # noqa: PLC0415

        return [routes.router]

    def template_globals(self) -> dict:
        """``dt_shell``: the dt shell's view model, read by the dt theme's templates (docs/themes/dt/SHELL.md §2);
        ``dt_inbox``: the dt Inbox's view model (``pm.web.dt.inbox``, bound to this plugin's settings);
        ``dt_ntfy``: the Notifications list and page state (``pm.web.dt.ntfy``); ``dt_session``: the session
        tab's tabs and ⋯ menu (``pm.web.dt.session``)."""
        from pm.web.dt import agents as dtagents  # noqa: PLC0415
        from pm.web.dt import audit as dtaudit  # noqa: PLC0415
        from pm.web.dt import backlog as dtbacklog  # noqa: PLC0415
        from pm.web.dt import (  # noqa: PLC0415
            files,
            inbox,
            ntfy,
            pjops,
            session,
            shell,
            tmux,
        )
        from pm.web.dt import launch as dtlaunch  # noqa: PLC0415
        from pm.web.dt import project as dtproject  # noqa: PLC0415
        from pm.web.dt import projects as dtprojects  # noqa: PLC0415
        from pm.web.dt import servers as dtservers  # noqa: PLC0415
        from pm.web.dt import settings as dtsettings  # noqa: PLC0415

        return {"dt_shell": shell.dt_shell, "dt_inbox": inbox.bind(lambda: self.settings), "dt_ntfy": ntfy.dt_ntfy,
                "dt_session": session.dt_session,
                "dt_projects": dtprojects.bind(lambda: self.settings),  # Projects list + New project dialog
                "dt_project": dtproject.bind(lambda: self.settings),  # the project view (pm.web.dt.project)
                "dt_launch": dtlaunch.dt_launch,  # the Launch dialog (pm.web.dt.launch)
                "dt_tmux": tmux.bind(lambda: self.settings),  # the tmux page (pm.web.dt.tmux)
                "dt_pjops": pjops.dt_pjops,  # project view VMs tab rows (pm.web.dt.pjops)
                "dt_backlog": dtbacklog.dt_backlog,  # Backlog + project tabs
                "dt_audit": dtaudit.dt_audit,  # the Audit page (pm.web.dt.audit)
                "dt_agents": dtagents.dt_agents,  # the Agents page (pm.web.dt.agents, agents_library, agents_usage)
                "dt_files": files.dt_files,  # project view Files tab listing (pm.web.dt.files)
                "dt_servers": dtservers.dt_servers,  # the Servers page (pm.web.dt.servers)
                "dt_settings": dtsettings.bind(lambda: self.settings)}  # the Settings page (pm.web.dt.settings)


plugin = DtTheme()
