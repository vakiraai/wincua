# hitl-ui

The HITL page now ships inside the `wincua-hitl` package so it works after
`pip install`: see `packages/wincua-hitl/src/wincua_hitl/static/`
(`index.html`, `app.js`, `app.css`), served by `wincua_hitl.LocalUI` with the
stdlib HTTP server. No build step.
