---
name: check-web-page
description: Look at the bdnex web page for real (demo library, serve, headless Chromium screenshots) before saying a front-end change works. Use when touching bdnex/api/static/ or the endpoints the page uses.
---

1. Build a demo library in the scratchpad: `uv run python tools/demo_library.py $SCRATCH/web` (albums with covers, a series
   with a missing volume, a review flag, an import source).
2. Serve it (background): `BDNEX_CONFIG=$SCRATCH/web/bdnex.yaml uv run bdnex serve --port 8791 --allow-changes`. Static
   files are read from disk on every request: no restart for CSS/JS changes, a restart for Python changes.
3. Screenshot each view you touched:
   `chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars --window-size=1280,900 --virtual-time-budget=15000 --screenshot=$SCRATCH/shot.png "http://localhost:8791/#/review"`
   then Read the PNG. Views take deep links (`#/series?name=nains`, `#/albums?q=...`). A short budget leaves lazy pictures
   unloaded: that is the headless clock, not a bug (a cover request takes ~15 ms).
4. `node --check bdnex/api/static/app.js` after every JS edit (a syntax error blanks the page).
5. Things that went wrong before: `replaceChildren` does not flatten arrays (use `render(...)`), `null` children print as
   "null", an `<img>` cannot send the token header (the page fetches pictures itself when a token is set).
6. Stop the server when done (do not `pkill -f` a pattern that is in your own command line).
