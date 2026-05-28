"""
Structured obligation catalog — v2.2 Track C (c4 "license text / obligation
catalog enrichment").

What this module is
-------------------
A *static, in-code* catalog of the concrete obligations a downstream consumer
must satisfy for each of the ~30 well-known SPDX licenses the portal classifies
in ``tasks/scan_source.py`` (``_LICENSE_CATEGORY_DEFAULTS``). Each entry encodes
the obligation *facts* of the license as structured booleans / enums, plus the
human-readable obligation paragraphs the Obligations tab and the NOTICE-file
generator render.

Why a code catalog (not new columns / not a new table)
------------------------------------------------------
The obligation facts here are static properties of a *license text* (e.g. "MIT
requires you to reproduce the copyright + permission notice"); they do not vary
per team, per scan, or over time. The portal *already* has a DB home for them:
the ``obligations`` table (``models.scan.Obligation``), keyed by
``(license_id, kind)``, which is *already* the read surface for the Obligations
tab and the NOTICE generator (``services.obligation_service``). Before this
change, however, ``obligations`` was populated **only** by the seed scripts — a
real production scan created ``License`` rows (``_get_or_create_license``) but
**zero** ``Obligation`` rows, so the Obligations tab and the generated NOTICE
came back empty for real projects.

c4 closes that gap by:
  1. encoding the structured obligations of the catalog licenses HERE
     (single source of truth, derived from the license texts — see citations),
  2. exposing :func:`obligations_for` which turns a license's structured facts
     into concrete ``(kind, text, link)`` obligation rows, and
  3. having ``scan_source._get_or_create_license`` upsert those rows into the
     existing ``obligations`` table idempotently (CLAUDE.md §6 — data
     population is idempotent, not baked into a schema migration).

No schema change / no migration is required: the table and the read path already
exist; we only start *populating* it during real scans.

Sourcing
--------
Every structured value below is derived from the canonical license text (linked
per entry). The obligation paragraphs paraphrase the relevant clauses — they are
NOT legal advice and are deliberately concise (full license texts are large and
are intentionally NOT embedded; consumers follow ``reference_url`` for the
verbatim text). Citations point at the SPDX-hosted canonical text.

Structured obligation fields (per license)
------------------------------------------
- ``attribution_required``           bool — must reproduce copyright / author notices.
- ``license_text_inclusion_required`` bool — must include the full license text.
- ``copyright_notice_required``      bool — must preserve copyright notices specifically.
- ``state_changes_required``         bool — must document / flag modifications.
- ``source_disclosure``              NONE / LIBRARY / NETWORK (see :class:`SourceDisclosure`).
- ``patent_grant``                   bool — license carries an express patent grant.
- ``same_license_required``          bool — conveyed/derivative work must stay under
                                     the same license (copyleft).
- ``notice_file_required``           bool — must propagate a NOTICE/attribution file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Obligation "kind" vocabulary
# ---------------------------------------------------------------------------
#
# These string constants are the ``obligations.kind`` values written to the DB.
# They line up with ``schemas.obligation_detail.KNOWN_OBLIGATION_KINDS`` so the
# Obligations-tab distribution chart renders them in a stable, ranked order.
# (``kind`` is a free-form column, so adding a value here never needs a
# migration.)
KIND_ATTRIBUTION = "attribution"
KIND_NOTICE = "notice"
KIND_SOURCE_DISCLOSURE = "source-disclosure"
KIND_COPYLEFT = "copyleft"
KIND_MODIFICATIONS = "modifications"
KIND_PATENT = "patent"


class SourceDisclosure(str, Enum):
    """Scope of any source-code disclosure obligation a license imposes.

    - ``NONE``    — permissive license; no obligation to disclose source.
    - ``LIBRARY`` — weak copyleft; source must be available for the LICENSED
      component / library itself (e.g. LGPL, MPL-2.0, EPL, CDDL), not for the
      whole application that merely links to it.
    - ``NETWORK`` — strong copyleft that reaches network use; source must be
      offered to users who interact with the software OVER A NETWORK, not only
      to those who receive a binary (e.g. AGPL-3.0, SSPL-1.0).

    ``GPL`` is strong copyleft but program-scoped (not network-scoped): it is
    modelled with ``same_license_required=True`` + ``source_disclosure=LIBRARY``
    where LIBRARY here means "the conveyed work's complete corresponding source",
    because GPL's reach is triggered by CONVEYING a binary, like the weak-copyleft
    licenses — the distinguishing GPL property (whole-program copyleft) is carried
    by ``same_license_required``. AGPL extends that trigger to NETWORK use, which
    is what ``NETWORK`` captures.
    """

    NONE = "none"
    LIBRARY = "library"
    NETWORK = "network"


@dataclass(frozen=True)
class LicenseObligations:
    """Structured obligation facts for a single license, plus rendered text.

    ``rows`` holds the concrete ``(kind, text)`` obligation paragraphs that get
    upserted into the ``obligations`` table; the boolean / enum fields above it
    are the machine-readable summary callers (build gate, future policy UI) can
    reason over without parsing prose.
    """

    spdx_id: str
    attribution_required: bool = False
    license_text_inclusion_required: bool = False
    copyright_notice_required: bool = False
    state_changes_required: bool = False
    source_disclosure: SourceDisclosure = SourceDisclosure.NONE
    patent_grant: bool = False
    same_license_required: bool = False
    notice_file_required: bool = False
    # (kind, text) paragraphs — link defaults to the license reference at render
    # time (see ``obligations_for``). Encoded as a tuple so the dataclass stays
    # frozen/hashable.
    rows: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The catalog — keyed by SPDX id, covering the ~30 licenses in
# tasks/scan_source.py ``_LICENSE_CATEGORY_DEFAULTS``.
#
# Citations point at the canonical SPDX-hosted license text. The obligation
# paragraphs paraphrase the cited clauses; they are concise by design.
# ---------------------------------------------------------------------------

# Permissive attribution paragraph reused across MIT-style licenses.
_ATTR_TEXT = (
    "Reproduce the above copyright notice and the permission notice in all "
    "copies or substantial portions of the software."
)
_BSD_ATTR_TEXT = (
    "Retain the copyright notice, the list of conditions, and the disclaimer "
    "in source redistributions, and reproduce them in the documentation and/or "
    "other materials provided with binary redistributions."
)


def _permissive(
    spdx_id: str,
    *,
    text: str = _ATTR_TEXT,
) -> LicenseObligations:
    """Build a permissive (attribution + license-text) obligation entry.

    Used for MIT / ISC / BSD-2-Clause. The one permissive license that ALSO
    carries a patent grant (Apache-2.0) is defined explicitly below rather than
    via this helper, so this builder intentionally has no patent branch.
    """
    rows: tuple[tuple[str, str], ...] = (
        (KIND_ATTRIBUTION, text),
        (
            KIND_NOTICE,
            "Include a copy of the license text with any redistribution so "
            "recipients receive the same grant and disclaimers.",
        ),
    )
    return LicenseObligations(
        spdx_id=spdx_id,
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        rows=rows,
    )


def _public_domain(spdx_id: str) -> LicenseObligations:
    """Public-domain / no-attribution dedication — no obligations to satisfy."""
    return LicenseObligations(spdx_id=spdx_id)


# Weak-copyleft (file/library-scoped) paragraph reused across MPL/EPL/CDDL/LGPL.
def _weak_copyleft(
    spdx_id: str,
    *,
    patent_grant: bool = False,
    extra_rows: tuple[tuple[str, str], ...] = (),
) -> LicenseObligations:
    rows: list[tuple[str, str]] = [
        (
            KIND_ATTRIBUTION,
            "Retain all copyright, patent, trademark, and attribution notices "
            "from the source you received.",
        ),
        (
            KIND_SOURCE_DISCLOSURE,
            "Make the source of the covered files (and your modifications to "
            "them) available to recipients under this same license; the rest of "
            "a larger work that merely uses the component may stay under other "
            "terms.",
        ),
        (
            KIND_MODIFICATIONS,
            "Carry modified covered files under this same license and identify "
            "the files you changed.",
        ),
    ]
    if patent_grant:
        rows.append(
            (
                KIND_PATENT,
                "The license includes an express patent grant from contributors; "
                "asserting a covered patent against the work can terminate your "
                "patent rights.",
            )
        )
    rows.extend(extra_rows)
    return LicenseObligations(
        spdx_id=spdx_id,
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        source_disclosure=SourceDisclosure.LIBRARY,
        patent_grant=patent_grant,
        same_license_required=False,  # file/library scoped, not whole-work
        rows=tuple(rows),
    )


def _lgpl(spdx_id: str) -> LicenseObligations:
    """LGPL — library-scoped copyleft + relink/replace right for the library."""
    return LicenseObligations(
        spdx_id=spdx_id,
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        source_disclosure=SourceDisclosure.LIBRARY,
        patent_grant=False,
        same_license_required=False,  # only the LIBRARY itself is copyleft
        rows=(
            (
                KIND_ATTRIBUTION,
                "Retain the copyright notices and the LGPL license text with the "
                "library.",
            ),
            (
                KIND_SOURCE_DISCLOSURE,
                "Provide the complete source of the LGPL library (or a written "
                "offer for it) and allow the end user to relink against a "
                "modified version of the library.",
            ),
            (
                KIND_MODIFICATIONS,
                "License any modifications you make to the library itself under "
                "the LGPL and note the changes.",
            ),
        ),
    )


def _gpl(spdx_id: str) -> LicenseObligations:
    """GPL — strong, whole-program copyleft (conveying-triggered)."""
    return LicenseObligations(
        spdx_id=spdx_id,
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        # Program-scoped: source obligation is triggered by CONVEYING a binary,
        # like the weak-copyleft licenses (LIBRARY); the whole-program reach is
        # carried by ``same_license_required``. AGPL extends this to NETWORK.
        source_disclosure=SourceDisclosure.LIBRARY,
        patent_grant=True,  # GPLv3 §11 patent grant
        same_license_required=True,
        rows=(
            (
                KIND_SOURCE_DISCLOSURE,
                "Convey the complete corresponding source under the GPL to every "
                "recipient of the binary (or accompany it with a written offer).",
            ),
            (
                KIND_COPYLEFT,
                "License the entire conveyed work under the GPL; keep the license "
                "text, copyright notices, and warranty disclaimers intact.",
            ),
            (
                KIND_MODIFICATIONS,
                "Mark modified files with prominent change notices and dates.",
            ),
            (
                KIND_PATENT,
                "Contributors grant an express patent license; do not impose "
                "further patent restrictions on downstream recipients.",
            ),
        ),
    )


def _agpl(spdx_id: str) -> LicenseObligations:
    """AGPL — GPL copyleft extended to NETWORK interaction (§13)."""
    base = _gpl(spdx_id)
    network_rows = (
        (
            KIND_SOURCE_DISCLOSURE,
            "If users interact with a modified version over a network, offer "
            "them the complete corresponding source of your version under the "
            "AGPL (the §13 'remote network interaction' obligation).",
        ),
        (
            KIND_COPYLEFT,
            "License the entire work under the AGPL; the network-use trigger "
            "means hosting it as a service does not avoid the source obligation.",
        ),
        (
            KIND_MODIFICATIONS,
            "Mark modified files with prominent change notices and dates.",
        ),
        (
            KIND_PATENT,
            "Contributors grant an express patent license; do not impose "
            "further patent restrictions on downstream recipients.",
        ),
    )
    return LicenseObligations(
        spdx_id=base.spdx_id,
        attribution_required=base.attribution_required,
        license_text_inclusion_required=base.license_text_inclusion_required,
        copyright_notice_required=base.copyright_notice_required,
        state_changes_required=base.state_changes_required,
        source_disclosure=SourceDisclosure.NETWORK,
        patent_grant=base.patent_grant,
        same_license_required=base.same_license_required,
        rows=network_rows,
    )


def _sspl(spdx_id: str) -> LicenseObligations:
    """SSPL — strong copyleft reaching the entire service stack (§13)."""
    return LicenseObligations(
        spdx_id=spdx_id,
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        source_disclosure=SourceDisclosure.NETWORK,
        patent_grant=False,
        same_license_required=True,
        rows=(
            (
                KIND_SOURCE_DISCLOSURE,
                "If you offer the program as a service, release the complete "
                "source of the service-making software stack (management, APIs, "
                "monitoring, orchestration) under the SSPL (the §13 obligation).",
            ),
            (
                KIND_COPYLEFT,
                "Convey modified versions under the SSPL and keep all license "
                "and copyright notices intact.",
            ),
            (
                KIND_MODIFICATIONS,
                "Mark modified files with prominent change notices and dates.",
            ),
        ),
    )


def _busl(spdx_id: str) -> LicenseObligations:
    """BUSL-1.1 — source-available, use-restricted until the Change Date."""
    return LicenseObligations(
        spdx_id=spdx_id,
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=False,
        source_disclosure=SourceDisclosure.NONE,
        patent_grant=False,
        same_license_required=False,
        rows=(
            (
                KIND_ATTRIBUTION,
                "Retain the BUSL license grant, the Additional Use Grant, and "
                "the copyright notices on every copy.",
            ),
            (
                KIND_NOTICE,
                "Production use outside the Additional Use Grant is prohibited "
                "until the Change Date, after which the work converts to the "
                "Change License (commonly Apache-2.0 or GPL). Review the license "
                "parameters before deploying.",
            ),
        ),
    )


_CATALOG: dict[str, LicenseObligations] = {
    # ----- Permissive / allowed ------------------------------------------
    # MIT — https://spdx.org/licenses/MIT.html
    "MIT": _permissive("MIT"),
    # ISC — https://spdx.org/licenses/ISC.html (MIT-equivalent attribution)
    "ISC": _permissive("ISC"),
    # Apache-2.0 — https://spdx.org/licenses/Apache-2.0.html
    # §4 attribution + retain NOTICE; §3 patent grant; §4(b) state changes.
    "Apache-2.0": LicenseObligations(
        spdx_id="Apache-2.0",
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        patent_grant=True,
        notice_file_required=True,
        rows=(
            (
                KIND_ATTRIBUTION,
                "Retain all copyright, patent, trademark, and attribution notices "
                "from the source (Apache-2.0 §4(c)).",
            ),
            (
                KIND_NOTICE,
                "If the work ships a NOTICE file, include its attribution notices "
                "in your redistribution's NOTICE, documentation, or display "
                "(Apache-2.0 §4(d)).",
            ),
            (
                KIND_MODIFICATIONS,
                "Carry prominent notices stating that you changed any files you "
                "modified (Apache-2.0 §4(b)).",
            ),
            (
                KIND_PATENT,
                "Apache-2.0 grants an express patent license (§3); the grant "
                "terminates for a party that initiates patent litigation alleging "
                "the work infringes a patent.",
            ),
        ),
    ),
    # Apache-1.1 — https://spdx.org/licenses/Apache-1.1.html
    # Attribution + advertising-clause / NOTICE, but NO patent grant.
    "Apache-1.1": LicenseObligations(
        spdx_id="Apache-1.1",
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        notice_file_required=True,
        rows=(
            (
                KIND_ATTRIBUTION,
                "Retain the copyright notice, the conditions list, and the "
                "disclaimer; include the acknowledgement attributing the Apache "
                "Software Foundation in redistributions where it appears.",
            ),
            (
                KIND_NOTICE,
                "Include a copy of the license text with redistributions.",
            ),
        ),
    ),
    # BSD-2-Clause — https://spdx.org/licenses/BSD-2-Clause.html
    "BSD-2-Clause": _permissive("BSD-2-Clause", text=_BSD_ATTR_TEXT),
    # BSD-3-Clause — https://spdx.org/licenses/BSD-3-Clause.html (adds no-endorsement)
    "BSD-3-Clause": LicenseObligations(
        spdx_id="BSD-3-Clause",
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        rows=(
            (KIND_ATTRIBUTION, _BSD_ATTR_TEXT),
            (
                KIND_NOTICE,
                "Do not use the names of the copyright holder or contributors to "
                "endorse or promote derived products without prior written "
                "permission (BSD-3-Clause third clause).",
            ),
        ),
    ),
    # 0BSD — https://spdx.org/licenses/0BSD.html (no attribution required)
    "0BSD": _public_domain("0BSD"),
    # Zlib — https://spdx.org/licenses/Zlib.html
    "Zlib": LicenseObligations(
        spdx_id="Zlib",
        attribution_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        rows=(
            (
                KIND_ATTRIBUTION,
                "Do not misrepresent the origin of the software; keep the license "
                "notice in the source distribution.",
            ),
            (
                KIND_MODIFICATIONS,
                "Mark altered source versions plainly as changed; do not claim "
                "you wrote the original.",
            ),
        ),
    ),
    # WTFPL — https://spdx.org/licenses/WTFPL.html (no obligations)
    "WTFPL": _public_domain("WTFPL"),
    # Unlicense — https://spdx.org/licenses/Unlicense.html (public domain)
    "Unlicense": _public_domain("Unlicense"),
    # CC0-1.0 — https://spdx.org/licenses/CC0-1.0.html (public domain dedication)
    "CC0-1.0": _public_domain("CC0-1.0"),
    # Python-2.0 — https://spdx.org/licenses/Python-2.0.html
    "Python-2.0": LicenseObligations(
        spdx_id="Python-2.0",
        attribution_required=True,
        license_text_inclusion_required=True,
        copyright_notice_required=True,
        state_changes_required=True,
        rows=(
            (
                KIND_ATTRIBUTION,
                "Retain the PSF copyright notice and the license text in copies "
                "or substantial portions of the software.",
            ),
            (
                KIND_MODIFICATIONS,
                "If you make derivative works, include a brief summary of the "
                "changes you made to the original.",
            ),
        ),
    ),
    # ----- Weak copyleft / conditional -----------------------------------
    # MPL-2.0 — https://spdx.org/licenses/MPL-2.0.html (file-level copyleft + patent)
    "MPL-2.0": _weak_copyleft("MPL-2.0", patent_grant=True),
    # MPL-1.1 — https://spdx.org/licenses/MPL-1.1.html (file-level copyleft + patent)
    "MPL-1.1": _weak_copyleft("MPL-1.1", patent_grant=True),
    # EPL-1.0 — https://spdx.org/licenses/EPL-1.0.html (module copyleft + patent)
    "EPL-1.0": _weak_copyleft("EPL-1.0", patent_grant=True),
    # EPL-2.0 — https://spdx.org/licenses/EPL-2.0.html (module copyleft + patent)
    "EPL-2.0": _weak_copyleft("EPL-2.0", patent_grant=True),
    # CDDL-1.0 — https://spdx.org/licenses/CDDL-1.0.html (file copyleft + patent)
    "CDDL-1.0": _weak_copyleft("CDDL-1.0", patent_grant=True),
    # CDDL-1.1 — https://spdx.org/licenses/CDDL-1.1.html (file copyleft + patent)
    "CDDL-1.1": _weak_copyleft("CDDL-1.1", patent_grant=True),
    # LGPL family — https://spdx.org/licenses/LGPL-2.1-only.html (+ variants)
    "LGPL-2.0-only": _lgpl("LGPL-2.0-only"),
    "LGPL-2.0-or-later": _lgpl("LGPL-2.0-or-later"),
    "LGPL-2.1-only": _lgpl("LGPL-2.1-only"),
    "LGPL-2.1-or-later": _lgpl("LGPL-2.1-or-later"),
    "LGPL-3.0-only": _lgpl("LGPL-3.0-only"),
    "LGPL-3.0-or-later": _lgpl("LGPL-3.0-or-later"),
    # ----- Strong copyleft / forbidden -----------------------------------
    # GPL family — https://spdx.org/licenses/GPL-3.0-only.html (+ variants)
    "GPL-2.0-only": _gpl("GPL-2.0-only"),
    "GPL-2.0-or-later": _gpl("GPL-2.0-or-later"),
    "GPL-3.0-only": _gpl("GPL-3.0-only"),
    "GPL-3.0-or-later": _gpl("GPL-3.0-or-later"),
    # AGPL family — https://spdx.org/licenses/AGPL-3.0-only.html (network copyleft)
    "AGPL-3.0-only": _agpl("AGPL-3.0-only"),
    "AGPL-3.0-or-later": _agpl("AGPL-3.0-or-later"),
    # SSPL-1.0 — https://spdx.org/licenses/SSPL-1.0.html (service-stack copyleft)
    "SSPL-1.0": _sspl("SSPL-1.0"),
    # BUSL-1.1 — https://spdx.org/licenses/BUSL-1.1.html (source-available)
    "BUSL-1.1": _busl("BUSL-1.1"),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_license_obligations(spdx_id: str | None) -> LicenseObligations | None:
    """Return the structured obligation facts for a single SPDX id.

    Returns ``None`` for an unknown / custom id (``LicenseRef-*``), an empty id,
    or a compound expression (the caller resolves those via
    :func:`obligations_for`). The lookup is an exact match against the catalog
    keys — it does NOT attempt fuzzy / case-insensitive matching, mirroring
    ``_LICENSE_CATEGORY_DEFAULTS`` so the two catalogs stay in lockstep.
    """
    if not spdx_id:
        return None
    return _CATALOG.get(spdx_id)


def _split_compound(expression: str) -> list[str]:
    """Split a compound SPDX expression into its operand ids.

    Mirrors ``tasks.scan_source._classify_license_category`` splitting on the
    boolean / exception operators and parentheses so the two stay consistent.
    """
    import re

    return [t.strip() for t in re.split(r"\s+(?:AND|OR|WITH)\s+|[()]", expression)]


def obligations_for(
    spdx_id: str | None,
    *,
    reference_url: str | None = None,
) -> list[tuple[str, str, str | None]]:
    """Resolve an SPDX id (single OR compound) to concrete obligation rows.

    Returns a list of ``(kind, text, link)`` tuples ready to upsert into the
    ``obligations`` table. The list is de-duplicated on ``(kind, text)`` so a
    compound expression contributing the same obligation from two operands does
    not create duplicate rows. ``link`` is the license's own ``reference_url``
    when set, else the canonical ``https://spdx.org/licenses/<id>.html`` page for
    that operand, so the Obligations drawer / NOTICE can always deep-link to the
    canonical text — even for scan-created licenses with a NULL ``reference_url``.

    Behaviour:
      - Unknown / custom / empty id        → ``[]`` (no obligations, no crash).
      - Known single id                    → that license's rows.
      - Compound expression (``A OR B``)   → the UNION of every recognised
        operand's rows (de-duplicated). Operand order is preserved; unknown
        operands are skipped. This is intentionally permissive: surfacing the
        union of obligations is the safe default for compliance (you must
        satisfy whatever any constituent license demands), and it mirrors the
        "most restrictive wins" posture of the category classifier.
    """
    if not spdx_id:
        return []

    direct = _CATALOG.get(spdx_id)
    if direct is not None:
        entries = [direct]
    else:
        entries = []
        seen_ids: set[str] = set()
        for tok in _split_compound(spdx_id):
            if not tok or tok in seen_ids:
                continue
            seen_ids.add(tok)
            hit = _CATALOG.get(tok)
            if hit is not None:
                entries.append(hit)
        if not entries:
            return []

    out: list[tuple[str, str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        # Deep-link target: the license's own ``reference_url`` when the row has
        # one, else the canonical SPDX page for that operand. Scan-created
        # ``License`` rows frequently have a NULL ``reference_url`` (the very
        # sparseness this catalog fixes), so falling back to the deterministic
        # SPDX URL keeps the Obligations drawer / NOTICE link working instead of
        # emitting a null link.
        link = reference_url or f"https://spdx.org/licenses/{entry.spdx_id}.html"
        for kind, text in entry.rows:
            key = (kind, text)
            if key in seen:
                continue
            seen.add(key)
            out.append((kind, text, link))
    return out


def catalog_spdx_ids() -> frozenset[str]:
    """All SPDX ids the obligation catalog covers (for tests / introspection)."""
    return frozenset(_CATALOG)


__all__ = [
    "KIND_ATTRIBUTION",
    "KIND_COPYLEFT",
    "KIND_MODIFICATIONS",
    "KIND_NOTICE",
    "KIND_PATENT",
    "KIND_SOURCE_DISCLOSURE",
    "LicenseObligations",
    "SourceDisclosure",
    "catalog_spdx_ids",
    "get_license_obligations",
    "obligations_for",
]
