from pm.security.ticket import MAX_OUTSTANDING, TicketStore


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def test_issue_consume_once():
    st = TicketStore()
    tok = st.issue("sess", "$3")
    t = st.consume(tok, "sess")
    assert t and t.tmux_id == "$3" and t.session_id == "sess"
    assert st.consume(tok, "sess") is None  # single use


def test_expiry():
    c = Clock()
    st = TicketStore(ttl_s=30, clock=c)
    tok = st.issue("s", "$1")
    c.t += 30.5
    assert st.consume(tok, "s") is None
    assert len(st) == 0


def test_wrong_session_burns_ticket():
    st = TicketStore()
    tok = st.issue("a", "$1")
    assert st.consume(tok, "b") is None
    assert st.consume(tok, "a") is None


def test_garbage_and_bounds():
    st = TicketStore()
    assert st.consume(None, "s") is None
    assert st.consume("", "s") is None
    assert st.consume("x" * 1000, "s") is None
    for _ in range(MAX_OUTSTANDING + 20):
        st.issue("s", "$1")
    assert len(st) == MAX_OUTSTANDING


def test_tokens_unique_and_not_stored_plain():
    st = TicketStore()
    a, b = st.issue("s", "$1"), st.issue("s", "$1")
    assert a != b and len(a) >= 40
    assert all(a.encode() != k for k in st._items)


def test_ticket_carries_window_id_and_is_still_single_use():
    st = TicketStore()
    tok = st.issue("sess", "$3", "@7")
    t = st.consume(tok, "sess")
    assert t is not None and t.window_id == "@7" and t.tmux_id == "$3"
    assert st.consume(tok, "sess") is None
    assert st.consume(st.issue("sess", "$3"), "sess").window_id is None
