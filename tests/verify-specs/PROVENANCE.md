# Vendored verification specs — provenance

## Source

- **Repository**: `github.com/haksungjang/bug-hunter` (the external verification team's repo)
- **Snapshot commit**: **`1aa1605`** — "feat(verify): 개발팀 70건 종결 보고 독립 재검증 + 스펙 보강" (round2 reinforcement: the M-3 `GET /v1/audit` path, cancel cross-team 404, github-app revoke→re-register cycle + hard cleanup, drift-hardened vars). Do NOT re-vendor from an earlier commit — round2 changes are required.
- **Vendored files**:
  - `specs/*.json` (26 modules, ~320 checks) — **byte-faithful, never edited here**
  - `verify-runner.mjs` — 1-line edit: helpers import `../testrun-helpers.mjs` → `./testrun-helpers.mjs` (directory flattening)
  - `testrun-helpers.mjs` — 2 edits: `API` and `COMPOSE` now read `VERIFY_API_URL` / `VERIFY_COMPOSE_FILE` env vars (CI portability; defaults preserve the original local-dev values)
- **Ours, not vendored**: `run-modules.mjs` (nightly entrypoint), `excluded.json`, this file.

## Ground rules (agreed with the verification team, 2026-06-11)

1. **No spec edits.** The specs are the verification team's artifact. If a
   check doesn't fit our environment, the resolution is (in order): seed the
   assumption (`scripts/seed_demo.py` `_seed_verify_baseline`), ask the team
   to re-key the spec, or exclude WITH a reason in `excluded.json`. Never
   "fix" a spec locally — that silently forks the oracle.
2. **Exclusions are shared.** `excluded.json` is part of the seed-baseline
   agreement: every entry is visible to the verification team and treated as
   a gap to close, not a permanent carve-out. The nightly summary prints
   excluded counts so shrinking coverage is always visible (no-silent-caps).
3. **Green here does NOT waive Tier-3.** This nightly is the dev team's
   internal regression net. The verification team's independent Tier-3
   re-verification — same specs, run by them, plus the FE/LLM surfaces these
   specs don't cover — remains a separate, co-existing gate. Purpose differs:
   we catch regressions early; they keep the oracle independent.

## Re-sync procedure

Only when the verification team publishes a spec update (e.g. after a Tier-3
round):

```bash
# from the repo root, with bug-hunter checked out at the agreed commit
git -C ~/projects/bug-hunter rev-parse --short HEAD   # record below
cp ~/projects/bug-hunter/scripts/verify/specs/*.json tests/verify-specs/specs/
cp ~/projects/bug-hunter/scripts/verify/verify-runner.mjs tests/verify-specs/
cp ~/projects/bug-hunter/scripts/testrun-helpers.mjs tests/verify-specs/
# re-apply the 3 portability edits (see "Vendored files" above), then
# update the snapshot commit in this file and re-run the nightly via
# workflow_dispatch before merging.
```

## Runtime assumptions

- A live dev-compose stack (backend :8000, postgres via
  `docker-compose -f <VERIFY_COMPOSE_FILE> exec -T postgres psql`).
- `alembic upgrade head` + `scripts/seed_demo.py` already run — the seed's
  `_seed_verify_baseline` block provides the fixtures the specs resolve
  (fx-appr, webhook slots, cancelled/superseded scans, GPL copyleft
  obligation, GitHub App credential fixtures 99000201/99000202/99000206,
  audit baseline rows). The 5 spec accounts equal the 5 seed accounts.
- `RATELIMIT_DISABLED=1` on the backend (the runner caches tokens, but a full
  multi-module run can still cross 5 logins/min/IP).
