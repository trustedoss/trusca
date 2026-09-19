# In-repo golden fixtures

One directory per baseline in `../baselines/`. `test_golden_fixtures.py` scans
each fixture through the real pipeline and asserts the normalised output equals
the committed baseline. `PROVENANCE.md` says how each fixture was made and where
its baseline differs from the original recording.

The nightly (`.github/workflows/golden-nightly.yml`) reads this directory
only. A fixture that does not resolve to the same components on every run makes
the gate flaky, so pin every dependency, transitive ones included. The first
nightly run failed on `python-pip` because only `requests` was pinned and
`certifi` and `idna` resolved to whatever was newest that day.

To change a fixture, regenerate its baseline in CI (dispatch the workflow with
`update_baselines=true`, download the artifact, review the diff). Do not
regenerate on a laptop.

Fixtures whose baseline is held out of the nightly are listed in `HELD_OUT` in
`tests/unit/test_golden_gate_contract.py`, with the issue that blocks them.

| fixture | detector | components |
|---|---|---|
| `node` | npm | 1 |
| `node-yarn` | yarn | 1 |
| `python-pip` | pip | 5 |
| `python-poetry` | poetry manifest only | 1 |
| `maven` | maven | 8 |
| `gradle`, `gradle-kts` | gradle | 7 |
| `go` | go modules | 1 |
| `rust` | cargo | 8 |
| `ruby` | bundler | 6 |
| `multi-component` | npm + maven | 69 |
| `scancode-*` | license text and headers only | 0 (`scancode-mixed-policy`: 1) |
