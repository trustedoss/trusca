# Golden-fixture drift gate (Tier 1)

Scans every baseline-scan fixture through the **real** pipeline (cdxgen → scancode →
Trivy → preserve) and diffs the normalised output against a committed baseline.
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
- `container_images.json` - the container images of the engine comparison
  run, pinned by digest (no baselines).

## Run (needs a live stack with real cdxgen/scancode/Trivy)
```bash
# diff against baselines (CI nightly)
pytest -m golden apps/backend/tests/e2e/golden/

# or the raw harness
python apps/backend/tests/e2e/golden/run_golden.py \
  --api http://localhost:8000 \
  --fixtures ~/projects/baseline-scan/tests/fixtures/projects \
  --names scancode-mixed-policy gradle maven rust node ...
```

## Regenerate baselines (deliberate — review the diff in the PR!)
```bash
python run_golden.py --api ... --fixtures ... --update --names <fixture> ...
```
Env: `GOLDEN_API`, `GOLDEN_EMAIL`, `GOLDEN_PASSWORD`, `GOLDEN_TEAM`,
`GOLDEN_FIXTURES`, `GOLDEN_POLL_TIMEOUT`, `GOLDEN_NAMES` (space or comma
separated subset of baselines; an unknown name is an error), `GOLDEN_STRICT`
(`1`: an unreachable stack or a missing fixture fails instead of skipping).

## Nightly
`.github/workflows/golden-nightly.yml` runs one ephemeral stack per shard, with
`GOLDEN_STRICT=1`. The shard list in that file is the set of baselines the
nightly compares; `tests/unit/test_golden_gate_contract.py` keeps it in step
with `baselines/` and `fixtures/`, and names the baselines still waiting for a
fixture. Regenerate baselines only there: dispatch with `update_baselines=true`
and download the artifact.

## Shadow run (in-process vs BomLens engine)
Dispatch `golden-nightly` with `shadow=true` (`gh workflow run golden-nightly.yml
-f shadow=true --ref <branch>`). Instead of diffing baselines, each shard scans
its fixtures with the in-process engine, then recreates the backend with
`SCAN_ENGINE=bomlens`, starts the engine worker and socket proxy from
`compose.bomlens.yml` on the same stack, and scans them again. Both passes read
celery-worker's Trivy database volume, so the report also compares
vulnerability ids; the database metadata before and after is recorded per
shard. The `shadow-report` job runs `shadow_report.py` over every shard's
captures and puts the difference table in the job summary and the
`golden-shadow-report` artifact. A difference never fails the run.

The report's "Stage timings" section shows, per fixture, the seconds each
engine spent per stage and each stage's share of the scan, from the scan row's
`stage_timings`. Stages the BomLens engine reports from its own step times
(`trivy`, `postprocess`) sit inside the stage 2 window and are marked `(in
scancode)`, not added. A second table lists each engine container of the
BomLens scan (plan, stage 1, stage 2, ownership reset) from the engine
worker's log, which the run keeps in `golden-shadow/engine-log/`. The wait
before a container includes checking for its image and pulling it when it is
missing; neither the log nor the scan row says whether a pull happened.

To rebuild the table from downloaded artifacts:
```bash
python shadow_report.py --inprocess golden-shadow/inprocess \
  --bomlens golden-shadow/bomlens --trivy-db golden-shadow/trivy-db \
  --engine-log golden-shadow/engine-log
```
The first scan per language includes pulling its cdxgen image; the two
BomLens images are pulled before the pass.

### Container images in the engine comparison run
The same dispatch starts a `golden (containers)` job, which scans the images
listed in `container_images.json` (each pinned to its index digest, with the
reason it was chosen) through the container scan API (`kind: container`,
`metadata.image_ref`). The first pass dispatches with
`SCAN_CONTAINER_ENGINE=inprocess`; the job then recreates the backend with
`SCAN_CONTAINER_ENGINE=bomlens` and starts the engine worker, as the source
shards do, and scans them again. `compose.containers.yml` passes the registry
allow-list (`CONTAINER_SCAN_ALLOWED_REGISTRIES=docker.io/library`) and the
engine setting to the containers; the engine worker gets the allow-list with
the in-process worker's environment. Both passes read celery-worker's Trivy
database, and its metadata is kept as `containers-before.json` and
`containers-after.json`.

The images are not pulled into the runner's Docker before either pass,
because neither engine would read them there: celery-worker has no Docker
socket, so `trivy image` reads each image from its registry, and the BomLens
engine reads it from the registry with syft. Each scan's time therefore
includes downloading the image, on both sides. The BomLens image itself is
pulled before the second pass.

The captures land in the same folders as the source fixtures' and the report
shows them in the same summary table, with `container` in its `Input` column,
after a `Container images` section that lists each reference. The images have
no baseline: `run_golden.py --images` runs only with `--shadow-out`.

```bash
python run_golden.py --api http://localhost:8000 --images container_images.json \
  --shadow-out golden-shadow/inprocess
```

## Notes
- **Not in the PR gate.** CI runs `pytest tests/unit tests/integration`; this
  lives under `tests/e2e/` and runs only in the golden-nightly workflow.
- `vulnerabilities_count` is **excluded** from the diff (NVD-mirror-dependent);
  vuln detection is asserted deterministically in the Tier 5 suite.
- Skips cleanly when the stack / fixtures aren't reachable.
