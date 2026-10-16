---
name: builder-preview
description: Build web pages so they show in pm's Builder mode preview aside (/a/<agent>/builder). Use when asked to build, mock or prototype a page, site or UI in an agent session, or to change the Builder page itself.
---

# Builder mode: preview aside

`<base_url>/a/<agent>/builder` shows the chat (with composer) on the left and a live preview aside
on the right. The aside is a sandboxed iframe over the agent's project files and reloads when a file changes
(2 s mtime poll).

## When you build a page for the user

1. Put `index.html` at the root of the agent's cwd (or `public/`, `dist/`, `build/`, `out/`).
2. Static only: relative paths for css/js/images (local pages keep the strict sandbox: no external `<use href="x.svg#id">` sprites or cross-origin fonts, so inline the SVG symbols or use `<img>`/CSS masks), no bundler needed to view it, no `fetch` to other hosts
   (CSP `connect-src 'none'`; the iframe has no same-origin access to pm).
3. Keep it responsive; the user switches the aside between a screen size preset (fit, MacBook 14", Dell 27"/34", tablet 768, iPhone 17 Pro Max 440 and other phones; the page is laid out at the real size and scaled).
4. Dotfiles, symlinks out of the project and files over 5 MB are not served.
5. Tell the user to open the Builder button above the composer (or `/a/<agent>/builder`).

## Showing a page: the `Preview:` line

End your reply with a line of its own (case-insensitive, nothing else on it):

```
Preview: /mock/index.html
Preview: https://<allowed host>/<path>
```

The Builder page watches the newest assistant reply and loads that target in the aside, once per reply.
The target is either a path inside the preview folder (relative to the agent's cwd, no `..`) or an
absolute URL on the allowed list. Replies already in the chat when the page opens never move the aside.

Allowed external URLs: `https://` origins in the pm setting `builder_frame_hosts` (pm.toml; default empty = no
external URLs; bare origins only, no path, e.g. a mock server such as `tools/mocksrv.py` behind TLS). Those load directly in the same sandboxed iframe
(the frame gets `allow-same-origin` there, so the mock's own `<use href="x.svg#id">` sprites and fonts load; pm's own origin is refused); anything else is refused. The aside's source select switches between **Static files** (project pages) and **URL** (allowed https pages); the
user can also type a path or an allowed URL into the address bar, and Ctrl/Cmd + wheel zooms the preview.

## When you build an API: the API pane

The aside has a **Page | API** switch. The API pane lists the endpoints of the project's API, lets the user
edit a test request (method, path, query, headers, body) and shows the response as text. To make an API
show there, keep one of these in the preview root (agent cwd, else the project folder):

1. An OpenAPI 3 or Swagger 2 file: `openapi.json`, `openapi.yaml`/`.yml`, `swagger.json`, or the same names
   under `api/` or `docs/` (first one found wins). Give every operation a `summary` and response
   `example`s (or schemas with `example` values): the mock replies come from them.
2. And/or mock replies: `mocks.json` or `mocks/*.json`, either `{"GET /pets": {"status": 200, "body": [...]}}`
   (a value without `status`/`body` is the body itself) or a list of
   `{"method": "POST", "path": "/pets", "request": {...}, "responses": {"201": {"body": {...}}, "400": "bad"}}`.
   Mocks override the spec's examples for the same method and path.

Limits: 1 MB per file, 500 endpoints, 50 mock files; no symlinks or dotfiles. **Mock** is the default target:
replies are computed in the browser, nothing is sent anywhere. Sending to the project's own dev server
(`127.0.0.1:<port>`) works only for ports the user lists in the pm setting `builder_api_ports` (off by
default; pm's own ports are refused); Cookie/Authorization headers are dropped, redirects are not followed.

## The `[pm builder: ...]` line

A user message that ends with a line starting `[pm builder:` was sent from the Builder page (pm adds that
line; the chat view hides it). Then end your reply with a `Preview:` line whenever there is something to
show. Messages without it come from the full chat page: do not add a `Preview:` line there.

## When you change the Builder page

Files (classic): `src/pm/web/themes/classic/templates/builder.html`, `static/css/builder.css`, `static/js/builder.js` (preview, source, chat links),
`static/js/builder-screen.js` (screen size + zoom), `static/js/builder-api.js` (Page | API switch, API pane),
`projects/apimock.py` (spec / mocks parsing), `projects/apisend.py` (dev-server send),
`web/routes/builder.py` (context keys documented at its top). Follow `docs/UI-RULES.md`, verify with
`uv run tools/uishot.py` on the dev instance, and never type into a live `/a/<agent>` composer.

The dt theme has its own `src/pm/web/themes/dt/templates/builder.html` (frame: `session/_frame.html`,
`pm.web.dt.session`); it follows `docs/themes/dt/CONDUCT.md`, not UI-RULES, and is checked with
`uv run tools/uishot.py --ui dt`. Never change one theme's Builder files for the other.
