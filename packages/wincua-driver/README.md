# wincua-driver

Observe / act substrate for wincua. Public types live under `wincua_driver`;
callers should prefer the `wincua` façade.

Backends are pluggable. Default for CI / non-Windows: `null` or `mock`.
Optional Cua Driver wrap is an extras path — **not required to import**.
