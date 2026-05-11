# Dogfooding Results — &lt;YYYY-MM-DD&gt; (real environment)

> **Fill in as you go.** Copy this file to
> `docs/sessions/<YYYY-MM-DD>-dogfooding-results-real.md` before you
> start, then replace every `<...>` placeholder with actual values.
> Keep the section headers stable so future readers can diff this
> against the simulated dry-run (`2026-05-11-dogfooding-results.md`).

---

## Environment

- **Type**: &lt;DigitalOcean droplet | local VM | leftover Linux box | other&gt;
- **OS / kernel**: &lt;Ubuntu 22.04 LTS / 5.15.x&gt;
- **CPU / RAM**: &lt;2 vCPU / 4 GB | ...&gt;
- **Public URL**: &lt;https://oss.example.com | http://192.168.1.50:8000 | ...&gt;
- **DNS**: &lt;real A record | /etc/hosts | nip.io | ...&gt;
- **TLS**: &lt;Let's Encrypt | self-signed | none (HTTP)&gt;
- **Browser**: &lt;Chrome 120 | Safari 17 | ...&gt;
- **Operator persona for Task α**: &lt;e.g. "Linux operator, never seen this portal"&gt;
- **Developer persona for Task β**: &lt;e.g. "new hire who knows React but not SCA"&gt;
- **CI engineer persona for Task γ**: &lt;e.g. "uses GitHub Actions weekly"&gt;

## Method

- Wall-clock recorded via `bash scripts/dogfood-timer.sh`
  (`start` / `mark` / `friction` / `end`).
- External search **forbidden** — docs / `git log` / `grep` only.
- Stuck > 60 min on one milestone: switch to the next task and log
  it as `C — abandoned`. Do not loop on a single block.
- Persona stays in character — do not jump into the FastAPI source
  unless the docs themselves point you there.

---

## Task α — First Admin (budget: 30 min)

### Setup

- Started at: &lt;YYYY-MM-DD HH:MM:SS local&gt;
- Persona: &lt;...&gt;

### Milestone log

Paste the output of `bash scripts/dogfood-timer.sh report a` here:

```text
&lt;paste&gt;
```

### Friction by category

| # | +mm:ss | Cat | Where (file:line or UI path) | Note | Workaround | Recommended fix |
|---|--------|-----|------------------------------|------|------------|-----------------|
|   |        |     |                              |      |            |                 |

### Result

- **Total wall-clock**: &lt;mm:ss&gt;
- **Milestones reached**: &lt;N/6&gt;
- **Friction-induced loss**: &lt;mm&gt;m
- **Net flow time**: &lt;mm&gt;m (&lt;pct&gt;% of 30-min budget)

---

## Task β — First Developer (budget: 30 min)

### Setup

- Started at: &lt;YYYY-MM-DD HH:MM:SS local&gt;
- Persona: &lt;...&gt;
- Test repo: &lt;e.g. github.com/expressjs/express&gt;

### Milestone log

Paste the output of `bash scripts/dogfood-timer.sh report b` here:

```text
&lt;paste&gt;
```

### Friction by category

| # | +mm:ss | Cat | Where | Note | Workaround | Recommended fix |
|---|--------|-----|-------|------|------------|-----------------|
|   |        |     |       |      |            |                 |

### Result

- **Total wall-clock**: &lt;mm:ss&gt;
- **Milestones reached**: &lt;N/7&gt;
- **Friction-induced loss**: &lt;mm&gt;m
- **Net flow time**: &lt;mm&gt;m (&lt;pct&gt;% of 30-min budget)

---

## Task γ — First CI integration (budget: 30 min)

### Setup

- Started at: &lt;YYYY-MM-DD HH:MM:SS local&gt;
- Persona: &lt;...&gt;
- Test repo (for the SCA workflow): &lt;e.g. your own scratch repo&gt;

### Milestone log

Paste the output of `bash scripts/dogfood-timer.sh report g` here:

```text
&lt;paste&gt;
```

### Friction by category

| # | +mm:ss | Cat | Where | Note | Workaround | Recommended fix |
|---|--------|-----|-------|------|------------|-----------------|
|   |        |     |       |      |            |                 |

### Result

- **Total wall-clock**: &lt;mm:ss&gt;
- **Milestones reached**: &lt;N/5&gt;
- **Friction-induced loss**: &lt;mm&gt;m
- **Net flow time**: &lt;mm&gt;m (&lt;pct&gt;% of 30-min budget)

---

## Priority backlog (from THIS pass)

| Priority | Task | Cat | Location | Estimated friction prevented |
|----------|------|-----|----------|------------------------------|
|          |      |     |          |                              |

## Cross-pass comparison

Compare against `2026-05-11-dogfooding-results.md` (simulated dry-run):

| Dim | Simulated (2026-05-11) | Real | Note |
|-----|------------------------|------|------|
| Total friction items found | 7 (P0×3 + P1×1 + P2×2 + P3×1) | &lt;N&gt; | |
| P0 items | 3 | &lt;N&gt; | &lt;which simulated P0s still showed up?&gt; |
| New items not in simulated | n/a | &lt;N&gt; | &lt;D/S vs P/U/C breakdown&gt; |
| Wall-clock budget hit (3/3) | n/a | &lt;hit/miss per task&gt; | |

## Hypothesis check

The simulated pass argued that fixing the 3 P0 docs items would
shave several minutes per task and replace hard 404s with smooth
flows. Did the real pass observe this?

- Task α P0 (α-1, DT OPEN/CLOSED contradiction): &lt;observed / not observed / new variant&gt;
- Task β P0 (β-1, fake `/v1/scans/source` endpoint): &lt;observed / not observed / new variant&gt;
- Task γ P0 (γ-1, fake `allowed_actions` taxonomy): &lt;observed / not observed / new variant&gt;

## Handoff

- All friction items mapped to PR fixes? &lt;Y/N&gt;
- Next session: &lt;e.g. "PR-2 follow-up after triage of P/U/C items"&gt;
- Linked PRs: &lt;#NN, #NN, ...&gt;
