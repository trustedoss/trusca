# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""BomLens as TRUSCA's scan engine (preview, ``SCAN_ENGINE=bomlens``).

BomLens runs as a published image and writes an output folder: a CycloneDX
SBOM, Trivy's own JSON report, and a result manifest (``bomlens-result.json``)
that says how the run ended and which file plays which role. This package
reads that folder:

* :mod:`integrations.bomlens.contract` parses and validates the manifest and
  resolves output files by role, checked against the sizes and digests the
  manifest recorded.
* :mod:`integrations.bomlens.stages` maps BomLens steps onto TRUSCA's stage
  vocabulary (``services.scan_outcome``).
* :mod:`integrations.bomlens.plan` reads the execution plan ``MODE=PLAN``
  writes, as untrusted input.
* :mod:`integrations.bomlens.runner` runs the plan's containers through the
  Docker API (the compose path; a Kubernetes Job is a later unit).

Persisting the result through TRUSCA's existing write paths is
``tasks.bomlens_result.apply_bomlens_result``; the source pipeline's BomLens
branch is ``tasks.scan_source_bomlens``.
"""
