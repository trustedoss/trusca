# Golden-fixture drift gate (Tier 1)

Scans every bd-scan fixture through the **real** pipeline (cdxgen → scancode →
DT → preserve) and diffs the normalised output against a committed baseline.
Catches the class of bug that mocked unit/integration tests miss: spurious
components (`pkg:nix`), compound-SPDX mis-classification, vanished transitive
deps, broken NOTICE/SBOM/PDF.

Why this exists: in the 2026-05-22 fixtures e2e, the entire unit+integration
suite was green while real-tool output was wrong. Asserting on **normalised
output content vs a baseline** is what surfaces that drift.

## Layout
- `run_golden.py` — stdlib-only harness (HTTP drive + zip + normalised capture +
  baseline diff/update). Runnable anywhere (host / container / CI).
- `test_golden_fixtures.py` — pytest wrapper, marker `golden` (nightly only).
- `baselines/<fixture>.json` — committed normalised snapshots.

## Run (needs a live stack with real cdxgen/scancode/DT)
```bash
# diff against baselines (CI nightly)
pytest -m golden apps/backend/tests/e2e/golden/

# or the raw harness
python apps/backend/tests/e2e/golden/run_golden.py \
  --api http://localhost:8000 \
  --fixtures ~/projects/bd-scan/tests/fixtures/projects \
  --names scancode-mixed-policy gradle maven rust node ...
```

## Regenerate baselines (deliberate — review the diff in the PR!)
```bash
python run_golden.py --api ... --fixtures ... --update --names <fixture> ...
```
Env: `GOLDEN_API`, `GOLDEN_EMAIL`, `GOLDEN_PASSWORD`, `GOLDEN_TEAM`,
`GOLDEN_FIXTURES`, `GOLDEN_POLL_TIMEOUT`.

## Notes
- **Not in the PR gate.** CI runs `pytest tests/unit tests/integration`; this
  lives under `tests/e2e/` and runs only in the nightly e2e workflow.
- `vulnerabilities_count` is **excluded** from the diff (NVD-mirror-dependent);
  vuln detection is asserted deterministically in the Tier 5 suite.
- Skips cleanly when the stack / fixtures aren't reachable.
