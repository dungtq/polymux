from pm.web.agentsidebar import _leaf


def test_leaf_is_project_folder():
    assert _leaf("/home/me/dev/pm/") == "pm"
    assert _leaf("") == ""


def test_sidebar_carries_project_dir():
    from types import SimpleNamespace as NS

    from pm.web.agentsidebar import agent_sidebar_data

    snap = NS(session=lambda sid: None, agents=(), sessions=())
    agent = NS(id="claude-aaaaaa", cwd="/home/me/dev/pm", session_id="$1")
    sb = agent_sidebar_data(snap, NS(terminal_enabled=False), agent, (), 0)
    assert (sb.title, sb.proj, sb.proj_dir) == ("pm", "pm", "/home/me/dev/pm")
