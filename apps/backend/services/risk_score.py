"""Per-axis, non-saturating project risk scoring (Wave 1 #34).

Single source of truth for the project risk score, replacing the legacy
weighted sum capped at 100 that previously lived (duplicated) in
``project_detail_service`` and ``release_snapshot_service``.

Why the rewrite
---------------
The old formula was ``min(100, critical*15 + high*5 + medium*1
+ forbidden*30 + conditional*5)``. It had two defects:

1. **Saturation** — summing then capping at 100 means 7 Critical CVEs and
   700 Critical CVEs both read "100", and a pile of low-weight items pins
   the score to the ceiling. Information is lost exactly where it matters.
2. **Concept conflation** — security and license risk were folded into one
   number, so a project with *zero* vulnerabilities but 24 ``conditional``
   licenses (24 * 5 = 120 → capped to 100) rendered as "Critical". That is
   actively misleading and erodes trust.

The model
---------
Two independent axes, each ``0..100``:

* **Security** — driven by the worst CVE severity present.
* **License**  — driven by the worst license category present.

Within an axis the *band* is fixed by the worst finding present; the
position inside the band scales with that finding's count through the
saturating curve ``n / (n + K)``, so the score rises with count but
approaches — never reaches — the band ceiling. No hard cap, no additive
weights, and the worst category can never be "outvoted" by a crowd of
lesser ones.

    Security band      by worst severity present
      critical  -> 75..100
      high      -> 50..74
      medium    -> 25..49
      low       ->  1..24
      (none)    ->   0

    License band       by worst category present
      forbidden    -> 75..100   (build-blocking == Critical)
      conditional  -> 25..49    (legal review == Medium; never Critical)
      unknown      ->  1..24    (needs identification == Low)
      (allowed)    ->   0

The bands line up with the frontend grade thresholds
(``RiskGauge.severityForScore``: >=75 Critical / >=50 High / >=25 Medium /
>0 Low / 0 none) so a single mapping colours both axes.

The legacy single ``risk_score`` is kept for back-compat as
``max(security, license)`` — the worse of the two axes — which is itself
non-saturating and meaningful for "riskiest project" sorting and release
trends.

``K`` and the band bounds are the only tunables; everything else is derived.
"""

from __future__ import annotations

from collections.abc import Mapping

# Within-band shape constant. Larger K => the score climbs toward the band
# ceiling more slowly (each additional finding matters less). K=4 gives a
# single finding ~20% into its band and ~10 findings ~70% in.
K = 4.0

# Bands ordered worst-first. The first key with a non-zero count wins.
_SECURITY_BANDS: tuple[tuple[str, float, float], ...] = (
    ("critical", 75.0, 100.0),
    ("high", 50.0, 74.0),
    ("medium", 25.0, 49.0),
    ("low", 1.0, 24.0),
)
_LICENSE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("forbidden", 75.0, 100.0),
    ("conditional", 25.0, 49.0),
    ("unknown", 1.0, 24.0),
)


def _band_score(count: int, floor: float, ceiling: float) -> float:
    """Position inside a band for ``count`` findings (count >= 1)."""
    fraction = count / (count + K)
    return round(floor + (ceiling - floor) * fraction, 1)


def _axis_score(
    distribution: Mapping[str, int],
    bands: tuple[tuple[str, float, float], ...],
) -> float:
    """Score one axis: the worst present category fixes the band; its count
    fixes the position within it. Returns ``0.0`` when nothing scores."""
    for key, floor, ceiling in bands:
        count = distribution.get(key, 0)
        if count > 0:
            return _band_score(count, floor, ceiling)
    return 0.0


def security_score(severity_distribution: Mapping[str, int]) -> float:
    """0..100 security risk from a component severity distribution.

    ``info`` / ``none`` buckets never contribute (they are not bands).
    """
    return _axis_score(severity_distribution, _SECURITY_BANDS)


def license_score(license_distribution: Mapping[str, int]) -> float:
    """0..100 license risk from a license-category distribution.

    ``allowed`` never contributes; ``conditional`` alone caps at the Medium
    band (<=49) — it can never render as "Critical" on its own.
    """
    return _axis_score(license_distribution, _LICENSE_BANDS)


def overall_risk_score(security: float, license_: float) -> float:
    """Back-compat single score: the worse of the two axes."""
    return max(security, license_)


def compute_risk_score(
    severity_distribution: Mapping[str, int],
    license_distribution: Mapping[str, int],
) -> float:
    """Legacy single-number entry point (overall = worse axis).

    Kept so callers that only need one figure (release snapshots, project
    diff, PDF/Excel report summary) swap in one line.
    """
    return overall_risk_score(
        security_score(severity_distribution),
        license_score(license_distribution),
    )
