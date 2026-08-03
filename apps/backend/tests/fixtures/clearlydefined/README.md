# ClearlyDefined fixtures

Captured from `https://api.clearlydefined.io/definitions/<coordinates>` on
2026-07-31, verbatim except for one omission.

The `files[]` array is stripped. It is the per-file harvest — thousands of
entries on a large package, 94% of the lodash response — and the parser never
reads it. Everything the parser *does* read (`licensed.declared`,
`licensed.facets.core.attribution.parties`, `coordinates`, `described`) is
untouched, so the fixtures keep the real density of the fields under test:
lodash carries a compound `CC0-1.0 AND MIT` declaration and three distinct
copyright holders, which is the shape a hand-written fixture would have
flattened.

| File | Why it is here |
|---|---|
| `real-npm-npmjs---lodash-4.17.21.json` | npm — the ecosystem no registry adapter covers. Compound `AND` declaration, so the licence normalises to nothing while the attributions still land. |
| `real-pypi-pypi---requests-2.31.0.json` | A clean single-id declaration (`Apache-2.0`) with four attributions. |
