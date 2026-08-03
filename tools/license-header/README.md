# license-header

SPDX header gate for first-party source files.

```bash
node tools/license-header/lint.mjs --all           # check (what CI runs)
node tools/license-header/lint.mjs --all --fix     # insert what's missing
node tools/license-header/lint.mjs --changed       # check vs. merge-base (local)
node tools/license-header/lint.mjs path/to/file.py # check named files
node tools/license-header/selftest.mjs             # assert the tool's own logic
```

The header is two lines:

```python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
```

`#` for `.py`, `//` for `.ts` / `.tsx`. It goes at the top of the file, below a
shebang if there is one. Python module docstrings remain the first *statement* —
comments are not statements — so `from __future__ import` and docstring
semantics are unaffected.

## Why headers at all

Apache-2.0 does not require them; its appendix recommends them. Two reasons
specific to this repository:

1. **TRUSCA detects licenses per file.** Scanning our own repository with our own
   product and getting `NOASSERTION` on every source file is a claim about it,
   not just about the repo.
2. **Vendored and first-party code share directories.** `services/g7_registry.json`
   (SK Telecom) sits beside `services/g7_conformance.py` (ours). A per-file
   marker is what tells them apart without opening `THIRD_PARTY_NOTICES.md`.

## Scope

Gated extensions: `.py`, `.ts`, `.tsx`, `.sh`, `.yml`, `.yaml`, `.tpl`.

In scope:

| Path | Why |
|---|---|
| `apps/backend/`, `apps/frontend/src/` | the application source |
| `scripts/` | install / backup / restore / upgrade wrappers an operator runs on a host |
| `actions/` | the GitHub Action a user's workflow references |
| `charts/` | the Helm chart, values file included |
| `docker-compose*.yml` (4 files) | the on-premise deployment unit |

The operator-facing four matter *more* than application code, not less: someone
can receive exactly one of those files and nothing else, which is precisely the
case a per-file header answers.

Out of scope, by design:

- **Tests.** Not distributed in any artifact. The reason for per-file headers —
  a recipient of one file can tell its license — does not apply to code that
  never leaves the repository. Revisit if we ever publish a fixture package.
- **`tools/`, `deploy/`, `docs-site/`, `.github/`.** Developer tooling, our own
  demo-host provisioning, docs, and CI config — none is a product artifact.
- **Empty files.** A copyright claim over zero bytes is noise, and an
  `__init__.py` marker will never be filled in.
- **Anything in `excluded.json`** — see below.

### Helm templates get a Helm comment

Files under `charts/*/templates/` take the `{{- /* … */ -}}` form rather than a
`#` YAML comment. A `#` comment survives rendering and would land in the
manifest the operator applies; the Helm form is stripped by the template engine.
`helm template` output is byte-identical before and after the headers, which is
verified rather than assumed. Nine templates already opened with a Helm comment,
so this matches the chart's own convention.

`templates/NOTES.txt` is untouched — it is text printed to the operator after
`helm install`, and `.txt` is not a gated extension anyway. The chart's copies of
`LICENSE` / `NOTICE` / `THIRD_PARTY_NOTICES.md` are likewise never stamped;
doing so would break the byte-equality contract in
`test_license_distribution.py`.

## excluded.json

The load-bearing file. Every group carries a `$reason`.

The category that matters is third-party material. Stamping
`Copyright TRUSCA contributors` onto a file that belongs to someone else is the
same compliance failure this tooling exists to fix, pointed the other way. **If
you vendor a file, add it here in the same change.**

One distinction worth keeping straight:

- **jq / shell → Python ports** (`g7_conformance.py`, `license_flags.py`,
  `eol_catalog.py`, …) get the normal TRUSCA header. A change of language means
  the *expression* is ours even though the semantics are upstream's; the credit
  for the semantics lives in `THIRD_PARTY_NOTICES.md` §1.
- **TypeScript vendored from TypeScript** (`g7Conformance.ts`, `g7Guidance.ts`)
  carries a hand-written dual-copyright header naming SK Telecom *and* TRUSCA,
  because upstream expression survives in the file. These are excluded so the
  auto-inserter cannot overwrite that with our name alone.

## Enforcement

Two layers, mirroring `tools/ko-style`:

| Layer | Where | Shared? | Behaviour |
|---|---|---|---|
| CI gate | `ci.yml`, `lint (frontend)` job | yes | `selftest.mjs` then `lint.mjs --all`; a missing header fails the build |
| Edit-time hook | `hook.mjs`, wired via PostToolUse | script yes, wiring no | inserts the header and exits 2 so the agent is told |

**The CI gate is the enforcement.** `.claude/` is gitignored, so the hook's
wiring is per-developer local configuration — `hook.mjs` ships in the repo but
nothing wires it up for you. `tools/ko-style/hook.mjs` has the same arrangement.
To opt in, add it to your `.claude/settings.json` beside the ko-style hook:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "node tools/ko-style/hook.mjs" },
          { "type": "command", "command": "node tools/license-header/hook.mjs" }
        ]
      }
    ]
  }
}
```

The hook fixes rather than complains: the header is mechanical, so there is no
judgement to hand back, and a hook that only nags produces a file that fails CI
later for a reason one write could have settled.

Both layers call the same `isTargeted()`, so the hook cannot stamp something the
gate rejects, or skip something the gate demands.

The CI gate is a **step in the existing `lint` job**, not a job of its own — a new
job means a new required check, and this repository already has docs-only PRs
stalling on checks that skip.

## selftest

`selftest.mjs` asserts the glob translator, the scope rules, header detection,
and insertion. It exists because the glob translator shipped broken on its first
draft, and because an exclusion rule that silently matches nothing is the one
failure that cannot be seen from outside: it stamps our copyright onto a
third-party file and reports success. CI runs it before the gate.

## Related

- `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` — the repository-level notices.
- `apps/backend/tests/unit/test_license_distribution.py` — asserts those three
  files reach every artifact (images, Helm chart) and that vendored code is
  listed in the notices.
- `tools/ko-style/` — the linter this one is modelled on.
