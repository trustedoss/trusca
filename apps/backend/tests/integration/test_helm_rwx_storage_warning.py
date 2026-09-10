# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
#429: NOTES.txt warns when workspace/trivyCache persistence would silently
hang on install.

Both PVCs default to `accessMode: ReadWriteMany` with `storageClassName: ""`
(cluster default). Most default StorageClasses (AWS gp2/gp3, GCP
pd-standard/pd-ssd, Azure managed-csi) are ReadWriteOnly, so the PVC stays
Pending and the install hangs with no indication of why unless the operator
already read the docs first.

Asserted on NOTES.txt's own Go-template source text rather than rendered
output, for the same reason `test_helm_notes_connection_budget.py` does:
NOTES.txt is an install-time artifact `helm template` never renders
(verified locally against this repo's pinned Helm version -- confirmed
absent from `helm template`'s output even with every required value set),
and `helm install --dry-run` needs a reachable cluster, which CI does not
have. Manually verified against a real `helm install --dry-run` locally
(the one environment where that command works) that the four cases below
behave as this file asserts on the template source: both PVCs left at
their defaults warns for both, fixing one `storageClassName` narrows the
warning to the other alone, setting `existingClaim` suppresses it (the
chart provisions nothing in that case), and `accessMode: ReadWriteOnce`
suppresses it too (no RWX request, no RWX risk).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
NOTES_PATH = REPO_ROOT / "charts" / "trustedoss" / "templates" / "NOTES.txt"


def _source() -> str:
    return NOTES_PATH.read_text()


def test_both_pvcs_are_checked_for_the_readwritemany_default_class_risk() -> None:
    source = _source()
    for prefix in ("workspace", "trivyCache"):
        for field in ("accessMode", "storageClassName", "existingClaim", "enabled"):
            needle = f".Values.{prefix}.persistence.{field}"
            assert needle in source, (
                f"NOTES.txt's RWX-risk check no longer references {needle} -- "
                "the #429 warning may have stopped covering this PVC"
            )


def test_the_risk_check_requires_readwritemany_specifically() -> None:
    """A ReadWriteOnce PVC with no storageClassName is not a risk -- the
    cluster default only needs to support RWO, which every StorageClass
    does. The check must compare against the literal, not just check that
    storageClassName is empty."""
    source = _source()
    assert 'eq .Values.workspace.persistence.accessMode "ReadWriteMany"' in source
    assert 'eq .Values.trivyCache.persistence.accessMode "ReadWriteMany"' in source


def test_existing_claim_is_excluded_from_the_risk() -> None:
    """`existingClaim` set means the chart provisions nothing itself, so the
    cluster's default StorageClass is never consulted -- the risk this
    warning exists for cannot occur."""
    source = _source()
    risk_lines = [
        line
        for line in source.splitlines()
        if "RWXRisk" in line and ":=" in line and "$" in line.split(":=")[0]
    ]
    assert len(risk_lines) == 2, (
        f"expected exactly one $...RWXRisk assignment per PVC, found: {risk_lines}"
    )
    for line in risk_lines:
        assert "existingClaim" in line, (
            f"a PVC's RWX-risk condition does not check existingClaim, so a "
            f"pre-created claim would still trigger the warning: {line!r}"
        )


def test_warning_names_the_actual_failure_mode_and_the_fix() -> None:
    source = _source()
    warning_start = source.index("WARNING:", source.index("RWXRisk"))
    # The warning block ends at the next Go-template {{- end }} closing the
    # `{{- if or $workspaceRWXRisk $trivyCacheRWXRisk }}` this file's other
    # tests pin the shape of.
    warning_end = source.index("{{- end }}", warning_start)
    warning_text = source[warning_start:warning_end]

    assert "ReadWriteMany" in warning_text
    assert "Pending" in warning_text, (
        "the warning should name the actual symptom (PVC stuck Pending), "
        "not just the misconfiguration, so an operator can grep for it"
    )
    assert "storageClassName" in warning_text
    assert "docs-site/docs/installation/helm.md" in warning_text, (
        "the warning should point at the doc that already has the full "
        "troubleshooting entry, not leave the operator to search for it"
    )
