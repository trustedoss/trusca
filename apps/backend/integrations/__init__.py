"""External tool / service adapters — Phase 2 PR #8.

This package isolates every subprocess call (cdxgen, scancode, Trivy, cosign)
behind a small adapter surface. The Celery tasks under ``tasks/`` should
never spawn a subprocess directly — they go through the adapters here, which
makes mocking in tests trivial (``TRUSTEDOSS_SCAN_BACKEND=mock``) and
concentrates retry / timeout policy in one place.

Layout:

- ``cdxgen.py``   — CycloneDX SBOM generator wrapper (third-party components +
  declared licenses).
- ``scancode.py`` — scancode-toolkit first-party detected-license scanner
  (PR-A2 — replaced the broken ORT ``evaluate`` adapter).
- ``trivy.py``    — Trivy wrapper: ``run_trivy_image`` for container scans +
  ``run_trivy_sbom`` for CVE matching against cdxgen SBOMs (W6-#40 / #41).
- ``cosign.py``   — sigstore/cosign wrapper for SBOM attestation.
- ``_size_guard.py`` — JSONB row size guard (CLAUDE.md core rule + I-1).
- ``_subprocess_env.py`` — env scrub allowlist shared across subprocesses.

W6-#43a (ADR-0001): the ``dt/`` sub-package (Dependency-Track REST client +
breaker + health monitor) was removed. CVE matching is now Trivy-only.
"""
