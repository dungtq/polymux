# Third-party notices

pm bundles these files in `src/pm/static/vendor/`. Each keeps its own license.
Versions and checksums are in the `VERSIONS.txt` files next to them.

| Component | Version | Files | License | License text |
|---|---|---|---|---|
| htmx | 2.0.4 | `htmx.min.js` | 0BSD | `vendor/HTMX-LICENSE` |
| htmx-ext-sse | 2.2.2 | `htmx-sse.js` | 0BSD | `vendor/HTMX-LICENSE` |
| @xterm/xterm | 6.0.0 | `xterm/xterm.js`, `xterm/xterm.css` | MIT, © The xterm.js authors, SourceLair Private Company, Christopher Jeffrey | `vendor/xterm/LICENSE` |
| @xterm/addon-fit | 0.11.0 | `xterm/addon-fit.js` | MIT, © The xterm.js authors | `vendor/xterm/LICENSE` |
| Symbols Nerd Font Mono | Nerd Fonts 3.5.1 | `fonts/SymbolsNerdFontMono-Regular.woff2` | MIT (© 2014 Ryan L McIntyre) for the patcher and glyph collection; included icon sets keep their own licenses (Font Awesome: SIL OFL 1.1 / CC BY 4.0, Material Design Icons: Apache 2.0, and others) | `vendor/fonts/LICENSE`, <https://github.com/ryanoasis/nerd-fonts/blob/master/LICENSE> |

The font file is a lossless WOFF2 conversion of `SymbolsNerdFontMono-Regular.ttf`
from the official `NerdFontsSymbolsOnly.zip`, without subsetting.

Python dependencies (FastAPI, uvicorn, Jinja2, and others in `pyproject.toml`)
are installed by uv from PyPI and are not bundled in this repository.
