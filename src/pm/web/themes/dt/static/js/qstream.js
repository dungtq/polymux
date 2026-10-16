/* dt Quick chat stream (PRD.md §6.12): the one reader of POST /qchat/send, used by the Quick chat panel (js/qchat.js)
   and btw in the chat aside (js/btw.js). The answer is SSE read from the fetch body (EventSource cannot POST):
   frames "event: <name>\ndata: <json>\n\n" with conv {id, title}, delta {t}, tool {id, name, fam, line},
   toolres {id, text, err}, done {html, session, cost} (html = the server's rendered, escaped markdown), error {msg}.
   Globals: pm7.qstream = {
     send(form, on(event, data), {signal}) -> Promise<{ok, r, done, aborted, lost}>   POST + read; form = object or
       URLSearchParams (repeated mcp / skills keys); ok = the server took it and the stream ended; done = a done
       frame came; aborted = the signal stopped it; lost = the stream broke after the server took it (r: null)
     read(body, on) -> Promise<bool>   read one stream body, true when a done frame came
     parse(block) -> {event, data} | null   one frame (pure; tested) }. */
(function () {
  'use strict';
  const pm7 = window.pm7;

  function parse(block) {
    let ev = 'message', data = '';
    String(block || '').split('\n').forEach((l) => {
      if (l.indexOf('event:') === 0) ev = l.slice(6).trim();
      else if (l.indexOf('data:') === 0) data += l.slice(5).trim();
    });
    try { return { event: ev, data: JSON.parse(data) || {} }; } catch (e) { return null; }
  }

  async function read(body, on) {
    const rd = body.getReader(), dec = new TextDecoder();
    let buf = '', done = false;
    const take = (block) => {
      const f = parse(block);
      if (!f) return;
      if (f.event === 'done') done = true;
      on(f.event, f.data);
    };
    for (;;) {
      const x = await rd.read();
      if (x.done) break;
      buf += dec.decode(x.value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) { take(buf.slice(0, i)); buf = buf.slice(i + 2); }
    }
    if (buf.trim()) take(buf);
    return done;
  }

  async function send(form, on, opts) {
    const o = opts || {};
    let r = null;
    try { r = await pm7.fetch('/qchat/send', { form: form, signal: o.signal }); } catch (e) {
      return { ok: false, r: null, done: false, aborted: !!(e && e.name === 'AbortError') };
    }
    if (!r.ok || !r.body) return { ok: false, r: r, done: false, aborted: false };
    try { return { ok: true, r: r, done: await read(r.body, on), aborted: false }; } catch (e) {
      return { ok: false, r: null, done: false, aborted: !!(e && e.name === 'AbortError'), lost: true };
    }
  }

  pm7.qstream = { send: send, read: read, parse: parse };
})();
