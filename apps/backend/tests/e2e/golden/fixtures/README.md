# In-repo golden fixtures (self-contained BUG-008 guard)

These are a **small, self-contained subset** of the external `bd-scan` fixture
corpus, committed into the repo so the golden-fixture drift gate
(`../test_golden_fixtures.py`) can guard the most important regression —
**"scan reports `succeeded` but detects 0 components"** (the BUG-008 silent-
failure class) — **without** requiring the `BD_SCAN_REPO_URL` secret / external
clone.

How it works: `test_golden_fixtures.py` resolves each baseline's fixture from
`GOLDEN_FIXTURES` (the external bd-scan corpus) first, and falls back to this
directory. So `node` and `python-pip` always run in the nightly e2e workflow
(real cdxgen, live stack); the full language matrix still runs when the
external corpus is present.

Keep each fixture **byte-identical** to its bd-scan counterpart — the committed
`../baselines/<name>.json` was generated from it, and the gate asserts full
equality. If you change a fixture, regenerate its baseline deliberately:

    python run_golden.py --api ... --fixtures ... --update --names <name>

Current self-contained fixtures:

| fixture      | detector | expected components |
|--------------|----------|---------------------|
| `node`       | npm      | 1 (`lodash`)        |
| `python-pip` | pip      | 5 (`requests` + transitive) |
