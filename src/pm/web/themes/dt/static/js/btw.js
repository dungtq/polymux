/* dt btw (PRD.md §6.3, README A9): a side question about one agent's chat, shown in the chat aside next to its tab
   (pm7.aside.btw puts the panel there; this file builds and runs it). Nothing reaches the agent: the question goes to
   Quick chat's POST /qchat/send (claude -p, profile "none": no tools, MCP or skills) with agent=<id> on the first
   turn, so the server adds that agent's recent chat (masked) as context; later turns send conv=<id>. The answer
   streams (SSE read from the fetch body by js/qstream.js: conv {id}, delta {t}, done {html}, error {msg}); done's
   html is the server's rendered, escaped markdown. Under each answer: Use in composer (the agent's own composer on
   this page, at the cursor, not sent: classic pm.composer.insert) and Copy. The conversation is also in Quick chat's
   history.
   Globals: pm7.btw = {panel(id) -> element (one per agent, kept for the page's life), ask(id, text), focus(id),
   focused(id) -> bool}.
   Markup (built here, css/aside.css): div.x-btw[data-btw-agent] > p.x-btwh (the note) + div.x-btwlog (div.x-btwm.u /
   .a items; the empty state .x-btwe with suggestions [data-btw-q]) + form.x-btwf (textarea[data-btw-input], Ask).
   Keys: Enter asks, Shift+Enter is a new line (the textarea's own listener; no document keydown). Phones have no
   aside: there aside.js opens the btw in Quick chat instead (pm7.qchat.btw, full screen). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const SUGGEST = ['Why is it waiting?', 'What has it changed so far?', 'Summarise the last reply'];
  const panels = new Map();   // agent id -> {id, el, log, ta, send, conv, busy}

  const el = pm7.el;  // core.js: el(tag, cls, text)
  function btn(label, icon, attr) {
    const b = el('button', 'x-btn sm');
    b.type = 'button';
    const i = icon ? pm7.icon(icon) : null;
    if (i) b.appendChild(i);
    b.appendChild(d.createTextNode(label));
    if (attr) b.setAttribute(attr, '');
    return b;
  }

  /* ---------- the panel ---------- */
  function build(id) {
    const st = { id: id, conv: '', busy: false };
    st.el = el('div', 'x-btw');
    st.el.setAttribute('data-btw-agent', id);
    const note = el('p', 'x-btwh');
    note.append('Side question about ', el('b', '', id), '. Answered from its chat (masked, newest part) by ',
      el('code', '', 'claude -p'), ' without tools. ', el('b', '', 'Nothing is sent to the agent.'));
    st.log = el('div', 'x-btwlog');
    st.log.setAttribute('aria-live', 'polite');
    const empty = el('div', 'x-btwe');
    empty.append(el('b', '', 'Ask anything about this chat'));
    SUGGEST.forEach((q) => { const b = btn(q, null, 'data-btw-q'); b.setAttribute('data-btw-q', q); empty.append(b); });
    st.log.append(empty);
    const form = el('form', 'x-btwf');
    st.ta = el('textarea', 'x-btwin');
    st.ta.rows = 2;
    st.ta.maxLength = 8000;
    st.ta.placeholder = 'Ask about ' + id + ' (not sent to it)';
    st.ta.setAttribute('aria-label', 'btw question about ' + id);
    st.ta.setAttribute('data-btw-input', '');
    st.send = btn('Ask', 'up', 'data-btw-send');
    st.send.type = 'submit';
    form.append(st.ta, st.send);
    st.el.append(note, st.log, form);
    form.addEventListener('submit', (e) => { e.preventDefault(); ask(id, st.ta.value); });
    st.ta.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.isComposing) return;
      e.preventDefault();
      ask(id, st.ta.value);
    });
    st.el.addEventListener('click', (e) => click(st, e));
    return st;
  }
  function state(id) {
    if (!panels.has(id)) panels.set(id, build(id));
    return panels.get(id);
  }
  function bottom(st) { st.log.scrollTop = st.log.scrollHeight; }
  function item(st, who, text) {
    const empty = st.log.querySelector('.x-btwe');
    if (empty) empty.remove();
    const m = el('div', 'x-btwm ' + who);
    m.append(el('div', 'h', who === 'u' ? 'You' : 'btw · no tools'), el('div', 't', text));
    st.log.append(m);
    bottom(st);
    return m;
  }
  function err(st, text) { st.log.append(el('p', 'x-btwerr', text)); bottom(st); }
  function acts(m) {
    const row = el('div', 'x-btwa');
    row.append(btn('Use in composer', 'up', 'data-btw-use'), btn('Copy', 'copy', 'data-btw-copy'));
    m.append(row);
  }
  function busy(st, on) {
    st.busy = on;
    st.send.disabled = on;
    st.ta.disabled = on;
    if (!on) st.ta.focus({ preventScroll: true });
  }

  /* ---------- ask: POST /qchat/send, read the event stream ---------- */
  async function ask(id, text) {
    const st = state(id), q = String(text == null ? '' : text).trim();
    if (!q || st.busy) return false;
    st.ta.value = '';
    item(st, 'u', q);
    const a = item(st, 'a', ''), t = a.querySelector('.t');
    busy(st, true);
    const form = { text: q, model: '', profile: 'none', conv: st.conv };
    if (!st.conv) form.agent = id;
    let r = null, ok = false;
    try { r = await pm7.fetch('/qchat/send', { form: form }); } catch (e) { r = null; }
    if (r && r.ok && r.body) {
      try { ok = await stream(st, r.body, t, a); } catch (e) { err(st, 'Connection lost.'); }
    } else err(st, await pm7.why(r, 'btw failed'));
    if (!t.textContent && !t.childElementCount) a.remove(); else if (ok) acts(a);
    busy(st, false);
    return ok;
  }
  function stream(st, body, t, a) {  // the SSE reader is js/qstream.js (shared with Quick chat)
    return pm7.qstream.read(body, (ev, o) => {
      if (ev === 'conv' && o.id != null) st.conv = String(o.id);
      else if (ev === 'delta' && o.t) { t.textContent += o.t; bottom(st); }
      else if (ev === 'done') {
        if (o.html) { t.classList.add('md'); t.innerHTML = o.html; }  // server-rendered, escaped markdown
        a.setAttribute('data-text', t.innerText || t.textContent || '');
        bottom(st);
      } else if (ev === 'error') err(st, o.msg ? 'btw failed: ' + String(o.msg).slice(0, 160) : 'btw failed');
    });
  }

  /* ---------- Use in composer, Copy, suggestions ---------- */
  function composerOf(id) {  // the agent's own composer form on this page (classic composer markup, README A5)
    const want = '/a/' + id + '/message';
    const f = pm7.$$('[data-composer="live"] [data-cmp-form], [data-composer="builder"] [data-cmp-form]')
      .find((x) => x.getAttribute('action') === want);
    return f ? f.querySelector('textarea[data-cmp-input]') : null;
  }
  function useIn(st, text) {
    const ta = composerOf(st.id), cmp = window.pm && window.pm.composer;
    if (!ta) { pm7.toast('Open ' + st.id + '’s tab to use the answer (or Copy it)', '', { error: true }); return; }
    if (cmp && cmp.insert) {
      const r = cmp.insert(ta, text);  // at the cursor; grows the box, keeps the draft and the Send state
      if (r && !r.ok) { pm7.toast('The composer is full', '', { error: true }); return; }
    } else {
      ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, 'end');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
    ta.focus();
    pm7.toast('In ' + st.id + '’s composer, not sent');
  }
  function click(st, e) {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const sug = t.closest('[data-btw-q]');
    if (sug) { ask(st.id, sug.getAttribute('data-btw-q')); return; }
    const m = t.closest('.x-btwm.a'), text = m ? m.getAttribute('data-text') || '' : '';
    if (!m) return;
    if (t.closest('[data-btw-use]')) useIn(st, text);
    else if (t.closest('[data-btw-copy]')) {
      const done = () => pm7.toast('Copied'), fail = () => pm7.toast('Copy failed', '', { error: true });
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, fail); else fail();
    }
  }

  pm7.btw = {
    panel: (id) => state(id).el,
    ask: (id, text) => ask(id, text),
    focus: (id) => { const st = panels.get(id); if (st && !st.ta.disabled) st.ta.focus(); },
    focused: (id) => { const st = panels.get(id); return !!st && d.activeElement === st.ta; }
  };
})();
