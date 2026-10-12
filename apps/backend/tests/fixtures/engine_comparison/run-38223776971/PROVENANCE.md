# Provenance of the engine comparison run captures

From `golden-nightly.yml` run 38223776971 (dispatched with `shadow=true`,
2026-10-12, BomLens 1.13.2 images), the engine comparison run whose small
fixtures took 1.3 to 13 times longer on the BomLens engine.

## Captures

`inprocess/` and `bomlens/` hold the `run_golden.py --shadow-out` captures of
two fixtures, unedited, from the artifacts `golden-shadow-node` and
`golden-shadow-scancode`:

```
gh run download 38223776971 -R trustedoss/trusca -n golden-shadow-node
gh run download 38223776971 -R trustedoss/trusca -n golden-shadow-scancode
```

The run predates the scan id and `stage_timings` in the capture. The tests add
both to a copy: the scan id from the engine log below, the stage windows
through the backend's own writers (`services.scan_outcome`).

| Fixture | Side | Scan id (engine log) |
| --- | --- | --- |
| node | BomLens | `77e851f2-c602-4efd-ad9d-96c66a9eea77` |
| scancode-license-files | BomLens | `987b3e90-cc45-4d6e-83bf-aaaf92516168` |

Each was matched by its capture's `started_at` against the first container
start of the scan in the log.

## Engine log

`engine-log/<shard>.log` holds the `bomlens_container_start` and
`bomlens_container_exit` lines the engine worker logged in that run, as the
`Shadow: engine worker log` step now writes them. They were taken from the
job logs of `golden (node)` (job 114726168369) and `golden (scancode)` (job
114726168494):

```
gh api repos/trustedoss/trusca/actions/jobs/114726168369/logs \
  | grep -E '"event": "bomlens_container_(start|exit)"' | cut -d' ' -f2-
```

`cut` removes the timestamp GitHub puts before each job log line, which leaves
the line as `docker logs` prints it. Nothing else was changed. The repository
ignores `*.log`, so the two files were added with `git add -f`.
