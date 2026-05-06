"""External tool / service adapters — Phase 2 PR #8.

This package isolates every subprocess call (cdxgen, ORT, Trivy) and every
outbound HTTP integration (Dependency-Track) behind a small adapter surface.
The Celery tasks under `tasks/` should never spawn a subprocess directly —
they go through the adapters here, which makes mocking in tests trivial
(`TRUSTEDOSS_SCAN_BACKEND=mock`) and concentrates retry / timeout policy in
one place.

Layout:

- ``cdxgen.py``  — CycloneDX SBOM generator wrapper.
- ``ort.py``     — OSS Review Toolkit license evaluator wrapper.
- ``trivy.py``   — Trivy container vulnerability scanner wrapper.
- ``dt/``        — Dependency-Track REST client + health monitor + breaker.
- ``_size_guard.py`` — JSONB row size guard (CLAUDE.md core rule + I-1).
"""
