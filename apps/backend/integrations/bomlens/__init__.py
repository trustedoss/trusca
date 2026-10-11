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

Persisting the result through TRUSCA's existing write paths is
``tasks.bomlens_result.apply_bomlens_result``. Running the image (a sidecar or
a Kubernetes Job driven by BomLens' execution plan) is a later unit.
"""
