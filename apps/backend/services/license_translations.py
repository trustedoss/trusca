"""
License content translations (C1a) — Korean summaries + obligation text.

What this module is
-------------------
The Korean half of the license content the portal shows: a one-or-two sentence
**summary** per catalog license (English + Korean — the English summary is new
here too; the catalog never had one) and a **Korean rendering of every
obligation paragraph** in ``services.obligation_catalog``.

Until now only the UI *chrome* was bilingual (react-i18next): license
categories and obligation kinds rendered as translated labels, but the
obligation prose and the license name came back from the API as English
pass-through. This module closes that gap for the finite (52-license) catalog.

Authoritative text is English
-----------------------------
The Korean text is **advisory** — an aid to reading, not a legal instrument.
The canonical license text stays English (``services/license_texts/*.txt``,
bundled verbatim from SPDX) and the NOTICE artifact keeps quoting it in
English. The UI presents the Korean rendering with the English original
available, and the API returns both so a caller can always fall back.

Why keyed by English text (not a parallel list)
-----------------------------------------------
``obligation_catalog`` builds its rows through shared helpers (``_permissive``,
``_weak_copyleft``, ``_lgpl``, ``_gpl``, …), so the same paragraph is reused
across many licenses — 52 licenses share just 48 distinct paragraphs. Keying
the translations by the English paragraph means each one is translated once and
every license that reuses it inherits the translation automatically, with no
per-license list to keep in order.

The drift risk this trades for (editing an English paragraph silently orphans
its translation) is closed by a contract test: ``test_catalog_contracts.py``
asserts every ``(kind, text)`` in the catalog has an entry here and every entry
here is still reachable from the catalog. Edit an English paragraph and the
suite fails until its Korean text is updated — which is the intended workflow.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LicenseSummary:
    """One-or-two sentence plain-language summary of a license.

    ``en`` is the authoritative wording; ``ko`` is the advisory translation.
    """

    en: str
    ko: str


# ---------------------------------------------------------------------------
# Obligation paragraph translations — keyed by the English paragraph exactly as
# it appears in ``services.obligation_catalog``.
# ---------------------------------------------------------------------------

_OBLIGATION_TEXT_KO: dict[str, str] = {
    # ----- Permissive attribution / notice --------------------------------
    "Reproduce the above copyright notice and the permission notice in all "
    "copies or substantial portions of the software.": (
        "소프트웨어의 모든 사본 또는 중요한 부분에 위 저작권 표시와 허가 표시를 "
        "그대로 포함해야 합니다."
    ),
    "Include a copy of the license text with any redistribution so recipients "
    "receive the same grant and disclaimers.": (
        "재배포할 때 라이선스 전문 사본을 함께 제공해, 수령자가 동일한 권리 허여와 "
        "면책 조항을 받도록 해야 합니다."
    ),
    "Retain the copyright notice, the list of conditions, and the disclaimer "
    "in source redistributions, and reproduce them in the documentation and/or "
    "other materials provided with binary redistributions.": (
        "소스를 재배포할 때는 저작권 표시, 조건 목록, 면책 조항을 유지해야 하고, "
        "바이너리를 재배포할 때는 문서나 함께 제공하는 자료에 이를 포함해야 합니다."
    ),
    "Include a copy of the license text with redistributions.": (
        "재배포할 때 라이선스 전문 사본을 함께 제공해야 합니다."
    ),
    # ----- Apache -----------------------------------------------------------
    "Retain all copyright, patent, trademark, and attribution notices from the "
    "source (Apache-2.0 §4(c)).": (
        "소스에 있는 저작권·특허·상표·저작자 표시를 모두 유지해야 합니다"
        "(Apache-2.0 §4(c))."
    ),
    "If the work ships a NOTICE file, include its attribution notices in your "
    "redistribution's NOTICE, documentation, or display (Apache-2.0 §4(d)).": (
        "저작물에 NOTICE 파일이 포함돼 있다면, 그 저작자 표시를 재배포물의 NOTICE "
        "파일이나 문서 또는 화면 표시에 포함해야 합니다(Apache-2.0 §4(d))."
    ),
    "Carry prominent notices stating that you changed any files you modified "
    "(Apache-2.0 §4(b)).": (
        "수정한 파일에는 변경했다는 사실을 알리는 표시를 눈에 띄게 남겨야 합니다"
        "(Apache-2.0 §4(b))."
    ),
    "Apache-2.0 grants an express patent license (§3); the grant terminates for "
    "a party that initiates patent litigation alleging the work infringes a "
    "patent.": (
        "Apache-2.0은 명시적 특허 라이선스를 허여합니다(§3). 해당 저작물이 특허를 "
        "침해했다고 주장하는 특허 소송을 제기하면 그 당사자의 특허 허여는 "
        "종료됩니다."
    ),
    "Retain the copyright notice, the conditions list, and the disclaimer; "
    "include the acknowledgement attributing the Apache Software Foundation in "
    "redistributions where it appears.": (
        "저작권 표시, 조건 목록, 면책 조항을 유지해야 합니다. Apache Software "
        "Foundation을 명시하는 감사 문구가 있다면 재배포물에도 포함해야 합니다."
    ),
    # ----- BSD / X11 / naming ----------------------------------------------
    "Do not use the names of the copyright holder or contributors to endorse or "
    "promote derived products without prior written permission (BSD-3-Clause "
    "third clause).": (
        "사전 서면 허가 없이 저작권자나 기여자의 이름을 파생 제품의 보증이나 홍보에 "
        "사용해서는 안 됩니다(BSD-3-Clause 세 번째 조항)."
    ),
    "Do not use the X Consortium name in advertising or promotion relating to "
    "the software without prior written authorization.": (
        "사전 서면 승인 없이 이 소프트웨어와 관련한 광고나 홍보에 X Consortium "
        "이름을 사용해서는 안 됩니다."
    ),
    "Advertising materials mentioning features of the software must display the "
    "acknowledgement crediting the OpenSSL Project, and products may not be "
    'called "OpenSSL" without permission.': (
        "이 소프트웨어의 기능을 언급하는 광고 자료에는 OpenSSL Project를 명시하는 "
        '감사 문구를 표시해야 하며, 허가 없이 제품 이름에 "OpenSSL"을 사용할 수 '
        "없습니다."
    ),
    "The advertising clause requires all advertising materials mentioning "
    "features or use of the software to display the acknowledgement crediting "
    "the copyright holder, and forbids using their name to endorse derived "
    "products without permission.": (
        "광고 조항에 따라, 이 소프트웨어의 기능이나 사용을 언급하는 모든 광고 "
        "자료에는 저작권자를 명시하는 감사 문구를 표시해야 하며, 허가 없이 그 "
        "이름을 파생 제품의 보증에 사용할 수 없습니다."
    ),
    'Products derived from this software may not be called "PHP", nor may "PHP" '
    "appear in their name, without prior written permission from the PHP Group "
    "(PHP-3.01 clauses 3–4).": (
        "PHP Group의 사전 서면 허가 없이는 이 소프트웨어에서 파생된 제품을 "
        '"PHP"라고 부르거나 제품 이름에 "PHP"를 포함할 수 없습니다'
        "(PHP-3.01 조항 3~4)."
    ),
    # ----- Zlib / libpng / origin -------------------------------------------
    "Do not misrepresent the origin of the software; keep the license notice in "
    "the source distribution.": (
        "소프트웨어의 출처를 사실과 다르게 표시해서는 안 되며, 소스 배포물에 "
        "라이선스 표시를 유지해야 합니다."
    ),
    "Mark altered source versions plainly as changed; do not claim you wrote the "
    "original.": (
        "변경한 소스 버전에는 변경됐다는 사실을 분명히 표시해야 하며, 원본을 "
        "본인이 작성했다고 주장해서는 안 됩니다."
    ),
    "Do not misrepresent the origin of the source; keep the license and "
    "authorship notices in the source distribution.": (
        "소스의 출처를 사실과 다르게 표시해서는 안 되며, 소스 배포물에 라이선스와 "
        "저작자 표시를 유지해야 합니다."
    ),
    "Plainly mark altered source versions as changed, and do not misrepresent "
    "them as the original software.": (
        "변경한 소스 버전에는 변경됐다는 사실을 분명히 표시해야 하며, 이를 원본 "
        "소프트웨어인 것처럼 표시해서는 안 됩니다."
    ),
    # ----- Python / Artistic -------------------------------------------------
    "Retain the PSF copyright notice and the license text in copies or "
    "substantial portions of the software.": (
        "소프트웨어의 사본이나 중요한 부분에 PSF 저작권 표시와 라이선스 전문을 "
        "유지해야 합니다."
    ),
    "If you make derivative works, include a brief summary of the changes you "
    "made to the original.": (
        "2차적 저작물을 만드는 경우, 원본에 가한 변경 사항의 간단한 요약을 포함해야 "
        "합니다."
    ),
    "Retain the copyright notice and this license with the package, and "
    "duplicate all of the original copyright notices and associated "
    "disclaimers.": (
        "패키지에 저작권 표시와 이 라이선스를 유지해야 하며, 원본의 모든 저작권 "
        "표시와 관련 면책 조항을 그대로 포함해야 합니다."
    ),
    "If you modify the package, document your changes and how they differ from "
    "the Standard Version (Artistic-2.0 §§4–6).": (
        "패키지를 수정하는 경우, 변경 사항과 그것이 표준 버전(Standard Version)과 "
        "어떻게 다른지 문서로 남겨야 합니다(Artistic-2.0 §§4~6)."
    ),
    # ----- Creative Commons ---------------------------------------------------
    "Give appropriate credit (author, copyright notice, license notice, "
    "disclaimer) and a link to the license, and to the material if supplied.": (
        "적절한 출처 표시(저작자, 저작권 표시, 라이선스 표시, 면책 조항)와 라이선스 "
        "링크를 제공해야 하며, 자료 링크가 제공된 경우 그 링크도 함께 표시해야 "
        "합니다."
    ),
    "Give appropriate credit (author, copyright notice, license notice, "
    "disclaimer) and a link to the license.": (
        "적절한 출처 표시(저작자, 저작권 표시, 라이선스 표시, 면책 조항)와 라이선스 "
        "링크를 제공해야 합니다."
    ),
    "Indicate whether you modified the material and retain an indication of any "
    "previous modifications.": (
        "자료를 수정했는지 여부를 표시해야 하며, 이전 수정 이력의 표시도 유지해야 "
        "합니다."
    ),
    "You may not apply legal terms or technological measures that legally "
    "restrict others from doing anything the license permits.": (
        "라이선스가 허용하는 행위를 다른 사람이 하지 못하도록 법적으로 제한하는 "
        "약관이나 기술적 조치를 적용해서는 안 됩니다."
    ),
    "If you remix or build upon the material, distribute your adaptations under "
    "CC-BY-SA-4.0 or a BY-SA-compatible license.": (
        "자료를 변형하거나 이를 기반으로 새 저작물을 만드는 경우, 그 2차적 저작물을 "
        "CC-BY-SA-4.0 또는 BY-SA 호환 라이선스로 배포해야 합니다."
    ),
    # ----- Patent grants -------------------------------------------------------
    "The license includes an express patent grant from contributors; asserting a "
    "covered patent against the work can terminate your patent rights.": (
        "이 라이선스는 기여자의 명시적 특허 허여를 포함합니다. 대상 특허를 이 "
        "저작물에 대해 주장하면 본인의 특허 권리가 종료될 수 있습니다."
    ),
    "Contributors grant an express patent license; do not impose further patent "
    "restrictions on downstream recipients.": (
        "기여자는 명시적 특허 라이선스를 허여합니다. 하위 수령자에게 추가적인 특허 "
        "제한을 부과해서는 안 됩니다."
    ),
    # ----- Weak copyleft (MPL / EPL / CDDL / MS-RL) ----------------------------
    "Retain all copyright, patent, trademark, and attribution notices from the "
    "source you received.": (
        "전달받은 소스에 있는 저작권·특허·상표·저작자 표시를 모두 유지해야 합니다."
    ),
    "Make the source of the covered files (and your modifications to them) "
    "available to recipients under this same license; the rest of a larger work "
    "that merely uses the component may stay under other terms.": (
        "적용 대상 파일과 그에 대한 수정 사항의 소스를 동일한 라이선스로 수령자에게 "
        "제공해야 합니다. 해당 컴포넌트를 사용하기만 하는 더 큰 저작물의 나머지 "
        "부분은 다른 조건을 유지할 수 있습니다."
    ),
    "Carry modified covered files under this same license and identify the files "
    "you changed.": (
        "수정한 적용 대상 파일은 동일한 라이선스로 배포해야 하며, 변경한 파일을 "
        "식별할 수 있게 표시해야 합니다."
    ),
    # ----- LGPL ----------------------------------------------------------------
    "Retain the copyright notices and the LGPL license text with the library.": (
        "라이브러리와 함께 저작권 표시와 LGPL 라이선스 전문을 유지해야 합니다."
    ),
    "Provide the complete source of the LGPL library (or a written offer for it) "
    "and allow the end user to relink against a modified version of the "
    "library.": (
        "LGPL 라이브러리의 완전한 소스를 제공하거나 이를 제공하겠다는 서면 약속을 "
        "함께 전달해야 하며, 최종 사용자가 수정된 버전의 라이브러리로 다시 링크할 "
        "수 있게 해야 합니다."
    ),
    "Distribute the library so the end user can replace it and relink — prefer "
    "dynamic linking, or supply object files / a written offer enabling a static "
    "relink against a modified library.": (
        "최종 사용자가 라이브러리를 교체하고 다시 링크할 수 있는 형태로 배포해야 "
        "합니다. 동적 링크를 우선 고려하고, 그렇지 않다면 수정된 라이브러리로 정적 "
        "재링크가 가능하도록 오브젝트 파일이나 서면 약속을 제공해야 합니다."
    ),
    "License any modifications you make to the library itself under the LGPL and "
    "note the changes.": (
        "라이브러리 자체에 가한 수정 사항은 LGPL로 배포해야 하며, 변경 내용을 "
        "표시해야 합니다."
    ),
    # ----- OFL ------------------------------------------------------------------
    "Keep the copyright and license notice with the font, in the font files or "
    "accompanying documentation.": (
        "폰트 파일 자체나 함께 제공하는 문서에 저작권 표시와 라이선스 표시를 "
        "유지해야 합니다."
    ),
    "Modified versions of the font must be distributed entirely under the OFL; "
    "the font itself (bundled or standalone) may not be sold on its own.": (
        "수정한 폰트는 전체를 OFL로 배포해야 합니다. 폰트 자체는 번들이든 단독이든 "
        "그 자체만으로 판매할 수 없습니다."
    ),
    "Do not use any Reserved Font Name declared by the author for your modified "
    "version without written permission.": (
        "서면 허가 없이는 저작자가 지정한 예약 폰트 이름(Reserved Font Name)을 "
        "수정한 버전에 사용해서는 안 됩니다."
    ),
    # ----- GPL -------------------------------------------------------------------
    "Convey the complete corresponding source under the GPL to every recipient "
    "of the binary (or accompany it with a written offer).": (
        "바이너리를 받는 모든 수령자에게 대응하는 완전한 소스를 GPL로 전달하거나, "
        "이를 제공하겠다는 서면 약속을 함께 전달해야 합니다."
    ),
    "License the entire conveyed work under the GPL; keep the license text, "
    "copyright notices, and warranty disclaimers intact.": (
        "전달하는 저작물 전체를 GPL로 배포해야 하며, 라이선스 전문·저작권 표시·보증 "
        "면책 조항을 그대로 유지해야 합니다."
    ),
    "Mark modified files with prominent change notices and dates.": (
        "수정한 파일에는 변경 사실과 날짜를 눈에 띄게 표시해야 합니다."
    ),
    # ----- AGPL -------------------------------------------------------------------
    "If users interact with a modified version over a network, offer them the "
    "complete corresponding source of your version under the AGPL (the §13 "
    "'remote network interaction' obligation).": (
        "사용자가 수정된 버전을 네트워크를 통해 사용하는 경우, 해당 버전의 대응하는 "
        "완전한 소스를 AGPL로 제공해야 합니다(§13 '원격 네트워크 상호작용' 의무)."
    ),
    "License the entire work under the AGPL; the network-use trigger means "
    "hosting it as a service does not avoid the source obligation.": (
        "저작물 전체를 AGPL로 배포해야 합니다. 네트워크 사용이 의무를 발생시키므로, "
        "서비스 형태로 호스팅한다고 해서 소스 공개 의무를 피할 수 없습니다."
    ),
    # ----- SSPL --------------------------------------------------------------------
    "If you offer the program as a service, release the complete source of the "
    "service-making software stack (management, APIs, monitoring, orchestration) "
    "under the SSPL (the §13 obligation).": (
        "프로그램을 서비스로 제공하는 경우, 그 서비스를 구성하는 소프트웨어 "
        "스택(관리·API·모니터링·오케스트레이션)의 완전한 소스를 SSPL로 공개해야 "
        "합니다(§13 의무)."
    ),
    "Convey modified versions under the SSPL and keep all license and copyright "
    "notices intact.": (
        "수정한 버전은 SSPL로 전달해야 하며, 모든 라이선스 표시와 저작권 표시를 "
        "그대로 유지해야 합니다."
    ),
    # ----- BUSL ---------------------------------------------------------------------
    "Retain the BUSL license grant, the Additional Use Grant, and the copyright "
    "notices on every copy.": (
        "모든 사본에 BUSL 권리 허여, 추가 사용 허여(Additional Use Grant), 저작권 "
        "표시를 유지해야 합니다."
    ),
    "Production use outside the Additional Use Grant is prohibited until the "
    "Change Date, after which the work converts to the Change License (commonly "
    "Apache-2.0 or GPL). Review the license parameters before deploying.": (
        "변경일(Change Date) 전까지는 추가 사용 허여 범위를 벗어난 프로덕션 사용이 "
        "금지됩니다. 변경일이 지나면 저작물은 변경 라이선스(Change License, 보통 "
        "Apache-2.0 또는 GPL)로 전환됩니다. 배포 전에 라이선스에 지정된 값을 "
        "확인하십시오."
    ),
}


# ---------------------------------------------------------------------------
# License summaries — the plain-language "what does this license ask of me"
# line shown above the obligation rows. English is authoritative.
# ---------------------------------------------------------------------------

_LICENSE_SUMMARY: dict[str, LicenseSummary] = {
    # ----- Permissive / allowed ---------------------------------------------
    "MIT": LicenseSummary(
        en=(
            "A short permissive license: use, modify, and redistribute freely, "
            "including in proprietary products, as long as you keep the "
            "copyright and permission notice."
        ),
        ko=(
            "짧고 자유로운 허용형 라이선스입니다. 저작권 표시와 허가 표시만 "
            "유지하면 독점 제품을 포함해 자유롭게 사용·수정·재배포할 수 있습니다."
        ),
    ),
    "ISC": LicenseSummary(
        en=(
            "A permissive license functionally equivalent to MIT with simpler "
            "wording: keep the copyright and permission notice, and you may use "
            "the code anywhere."
        ),
        ko=(
            "MIT와 사실상 동일하되 문구가 더 간결한 허용형 라이선스입니다. 저작권 "
            "표시와 허가 표시를 유지하면 어디에서든 코드를 사용할 수 있습니다."
        ),
    ),
    "Apache-2.0": LicenseSummary(
        en=(
            "A permissive license with an express patent grant. Keep the "
            "notices, propagate any NOTICE file, and flag files you changed; "
            "proprietary use is allowed."
        ),
        ko=(
            "명시적 특허 허여가 포함된 허용형 라이선스입니다. 각종 표시를 "
            "유지하고, NOTICE 파일이 있으면 함께 전달하며, 수정한 파일을 "
            "표시해야 합니다. 독점 제품에도 사용할 수 있습니다."
        ),
    ),
    "Apache-1.1": LicenseSummary(
        en=(
            "The pre-2.0 Apache license: permissive attribution with an "
            "acknowledgement requirement, but no patent grant. Superseded by "
            "Apache-2.0 for new work."
        ),
        ko=(
            "2.0 이전의 Apache 라이선스입니다. 감사 문구 표시가 필요한 허용형 "
            "저작자 표시 라이선스이며 특허 허여는 없습니다. 새 저작물에는 "
            "Apache-2.0이 대신 쓰입니다."
        ),
    ),
    "BSD-2-Clause": LicenseSummary(
        en=(
            "A permissive license: redistribute in source or binary form as long "
            "as you retain the copyright notice, conditions, and disclaimer."
        ),
        ko=(
            "허용형 라이선스입니다. 저작권 표시·조건·면책 조항을 유지하면 소스나 "
            "바이너리 형태로 재배포할 수 있습니다."
        ),
    ),
    "BSD-3-Clause": LicenseSummary(
        en=(
            "BSD-2-Clause plus a no-endorsement clause: you may not use the "
            "copyright holder's name to promote derived products without written "
            "permission."
        ),
        ko=(
            "BSD-2-Clause에 보증 금지 조항이 추가된 것입니다. 서면 허가 없이 "
            "저작권자의 이름을 파생 제품 홍보에 사용할 수 없습니다."
        ),
    ),
    "0BSD": LicenseSummary(
        en=(
            "BSD with the attribution requirement removed: use the code for any "
            "purpose with no conditions at all."
        ),
        ko=(
            "저작자 표시 의무를 없앤 BSD입니다. 아무 조건 없이 어떤 목적으로든 "
            "코드를 사용할 수 있습니다."
        ),
    ),
    "Zlib": LicenseSummary(
        en=(
            "A permissive license focused on honest provenance: do not claim you "
            "wrote the original, and mark altered versions as changed."
        ),
        ko=(
            "출처를 정확히 밝히는 데 초점을 둔 허용형 라이선스입니다. 원본을 "
            "본인이 작성했다고 주장해서는 안 되고, 변경한 버전은 변경됐다고 "
            "표시해야 합니다."
        ),
    ),
    "WTFPL": LicenseSummary(
        en=(
            "A parody license that grants unrestricted use with no conditions. "
            "It is not OSI-approved; some organizations disallow it on legal-"
            "clarity grounds."
        ),
        ko=(
            "아무 조건 없이 제약 없는 사용을 허용하는 풍자적 라이선스입니다. "
            "OSI 승인 라이선스가 아니며, 법적 명확성을 이유로 이를 금지하는 조직도 "
            "있습니다."
        ),
    ),
    "Unlicense": LicenseSummary(
        en=(
            "A public-domain dedication: the author waives all copyright, so you "
            "may use the work for any purpose without conditions."
        ),
        ko=(
            "퍼블릭 도메인 헌정입니다. 저작자가 모든 저작권을 포기하므로 아무 조건 "
            "없이 어떤 목적으로든 사용할 수 있습니다."
        ),
    ),
    "CC0-1.0": LicenseSummary(
        en=(
            "Creative Commons' public-domain dedication: the author waives "
            "copyright worldwide as far as the law allows, with a permissive "
            "fallback license where waiver is not possible."
        ),
        ko=(
            "Creative Commons의 퍼블릭 도메인 헌정입니다. 저작자가 법이 허용하는 "
            "범위에서 전 세계 저작권을 포기하며, 포기가 불가능한 경우를 대비해 "
            "허용형 대체 라이선스를 둡니다."
        ),
    ),
    "Python-2.0": LicenseSummary(
        en=(
            "The Python Software Foundation license: permissive, requires the "
            "PSF copyright notice and a summary of any changes you make."
        ),
        ko=(
            "Python Software Foundation 라이선스입니다. 허용형이며 PSF 저작권 "
            "표시와 변경 사항 요약이 필요합니다."
        ),
    ),
    "BSL-1.0": LicenseSummary(
        en=(
            "The Boost license: permissive, and notably requires no attribution "
            "in binary distributions — only the source must carry the notice."
        ),
        ko=(
            "Boost 라이선스입니다. 허용형이며, 특히 바이너리 배포에는 저작자 "
            "표시가 필요 없고 소스에만 표시를 유지하면 됩니다."
        ),
    ),
    "PostgreSQL": LicenseSummary(
        en=(
            "The PostgreSQL project's license: permissive and BSD/MIT-equivalent "
            "— keep the copyright notice and the permission paragraph."
        ),
        ko=(
            "PostgreSQL 프로젝트의 라이선스입니다. BSD·MIT와 동등한 허용형이며 "
            "저작권 표시와 허가 문구를 유지하면 됩니다."
        ),
    ),
    "NTP": LicenseSummary(
        en=(
            "A permissive license: keep the copyright notice, and do not use the "
            "author's name in promotion without permission."
        ),
        ko=(
            "허용형 라이선스입니다. 저작권 표시를 유지해야 하고, 허가 없이 저작자의 "
            "이름을 홍보에 사용해서는 안 됩니다."
        ),
    ),
    "curl": LicenseSummary(
        en=(
            "The curl license: permissive and MIT-equivalent — keep the "
            "copyright and permission notice in redistributions."
        ),
        ko=(
            "curl 라이선스입니다. MIT와 동등한 허용형이며 재배포 시 저작권 표시와 "
            "허가 표시를 유지하면 됩니다."
        ),
    ),
    "Ruby": LicenseSummary(
        en=(
            "The Ruby license: permissive, and offered as a dual license with "
            "BSD-2-Clause so recipients may choose either set of terms."
        ),
        ko=(
            "Ruby 라이선스입니다. 허용형이며 BSD-2-Clause와의 이중 라이선스로 "
            "제공돼 수령자가 둘 중 하나를 선택할 수 있습니다."
        ),
    ),
    "X11": LicenseSummary(
        en=(
            "The X11/MIT variant: permissive attribution plus a restriction on "
            "using the X Consortium name in advertising."
        ),
        ko=(
            "X11·MIT 계열 라이선스입니다. 허용형 저작자 표시에 더해 광고에 "
            "X Consortium 이름을 사용하는 것을 제한합니다."
        ),
    ),
    "Artistic-2.0": LicenseSummary(
        en=(
            "The Perl/CPAN license: permissive, but modified versions must "
            "document how they differ from the Standard Version."
        ),
        ko=(
            "Perl·CPAN 생태계의 라이선스입니다. 허용형이지만 수정한 버전은 표준 "
            "버전과 어떻게 다른지 문서로 남겨야 합니다."
        ),
    ),
    "PHP-3.01": LicenseSummary(
        en=(
            "The PHP license: permissive attribution with naming restrictions — "
            'derived products may not carry "PHP" in their name without '
            "permission."
        ),
        ko=(
            "PHP 라이선스입니다. 허용형 저작자 표시에 이름 제한이 붙어, 허가 없이 "
            '파생 제품 이름에 "PHP"를 쓸 수 없습니다.'
        ),
    ),
    "Libpng": LicenseSummary(
        en=(
            "The libpng license: permissive, focused on provenance — do not "
            "misrepresent the origin and mark altered versions."
        ),
        ko=(
            "libpng 라이선스입니다. 출처 표기에 초점을 둔 허용형으로, 출처를 사실과 "
            "다르게 표시해서는 안 되고 변경한 버전은 표시해야 합니다."
        ),
    ),
    "OpenSSL": LicenseSummary(
        en=(
            "The legacy OpenSSL license: permissive with an advertising clause "
            "crediting the OpenSSL Project. OpenSSL 3.0 and later moved to "
            "Apache-2.0."
        ),
        ko=(
            "구 OpenSSL 라이선스입니다. OpenSSL Project를 명시하는 광고 조항이 "
            "붙은 허용형이며, OpenSSL 3.0부터는 Apache-2.0으로 바뀌었습니다."
        ),
    ),
    "BSD-4-Clause": LicenseSummary(
        en=(
            "The original BSD license: BSD-3-Clause plus the advertising clause, "
            "which is widely considered impractical because every advertisement "
            "must credit each copyright holder."
        ),
        ko=(
            "최초의 BSD 라이선스입니다. BSD-3-Clause에 광고 조항이 더해진 형태로, "
            "모든 광고에 저작권자마다 감사 문구를 표시해야 해 실무상 부담이 크다고 "
            "평가됩니다."
        ),
    ),
    "CC-BY-4.0": LicenseSummary(
        en=(
            "Creative Commons Attribution: use and adapt the material for any "
            "purpose, including commercially, as long as you credit the author "
            "and indicate changes. Intended for content, not code."
        ),
        ko=(
            "Creative Commons 저작자 표시 라이선스입니다. 저작자를 표시하고 변경 "
            "사실을 밝히면 상업적 목적을 포함해 자유롭게 사용·변형할 수 있습니다. "
            "코드가 아니라 콘텐츠를 위한 라이선스입니다."
        ),
    ),
    "UPL-1.0": LicenseSummary(
        en=(
            "The Universal Permissive License: MIT-style terms with an unusually "
            "broad express patent grant covering larger works the code is "
            "included in."
        ),
        ko=(
            "Universal Permissive License입니다. MIT와 유사한 조건에 더해, 코드가 "
            "포함된 더 큰 저작물까지 포괄하는 넓은 명시적 특허 허여를 제공합니다."
        ),
    ),
    "AFL-3.0": LicenseSummary(
        en=(
            "The Academic Free License: permissive with an express patent grant "
            "and a patent-retaliation clause; not copyleft."
        ),
        ko=(
            "Academic Free License입니다. 명시적 특허 허여와 특허 보복 조항이 있는 "
            "허용형이며 카피레프트가 아닙니다."
        ),
    ),
    "MS-PL": LicenseSummary(
        en=(
            "The Microsoft Public License: permissive with an express patent "
            "grant; binary distribution must carry the license notice."
        ),
        ko=(
            "Microsoft Public License입니다. 명시적 특허 허여가 있는 허용형이며 "
            "바이너리 배포 시 라이선스 표시를 포함해야 합니다."
        ),
    ),
    "BlueOak-1.0.0": LicenseSummary(
        en=(
            "A modern plain-language permissive license with an express patent "
            "grant, designed to be readable without a lawyer."
        ),
        ko=(
            "명시적 특허 허여를 포함한 현대적 허용형 라이선스입니다. 법률 전문가 "
            "없이도 읽을 수 있도록 평이한 표현으로 작성됐습니다."
        ),
    ),
    "MIT-0": LicenseSummary(
        en=(
            "MIT with the attribution requirement removed: use the code for any "
            "purpose without keeping the notice."
        ),
        ko=(
            "저작자 표시 의무를 없앤 MIT입니다. 표시를 유지하지 않고도 어떤 "
            "목적으로든 코드를 사용할 수 있습니다."
        ),
    ),
    # ----- Weak copyleft / conditional ------------------------------------------
    "MPL-2.0": LicenseSummary(
        en=(
            "File-level copyleft with a patent grant: source for the MPL files "
            "you ship (and your changes to them) must stay MPL, but the rest of "
            "your application may stay proprietary."
        ),
        ko=(
            "특허 허여가 포함된 파일 단위 카피레프트입니다. 배포하는 MPL 파일과 그 "
            "수정본의 소스는 MPL로 유지해야 하지만, 애플리케이션의 나머지 부분은 "
            "독점으로 둘 수 있습니다."
        ),
    ),
    "MPL-1.1": LicenseSummary(
        en=(
            "The predecessor of MPL-2.0: file-level copyleft with a patent "
            "grant. MPL-2.0 is the current version and is easier to combine with "
            "GPL work."
        ),
        ko=(
            "MPL-2.0의 이전 버전으로, 특허 허여가 포함된 파일 단위 "
            "카피레프트입니다. 현재 버전은 MPL-2.0이며 GPL 저작물과 결합하기가 더 "
            "수월합니다."
        ),
    ),
    "EPL-1.0": LicenseSummary(
        en=(
            "The Eclipse Public License: module-scoped copyleft with a patent "
            "grant. Source for the EPL modules must be available; the larger "
            "work may stay proprietary."
        ),
        ko=(
            "Eclipse Public License입니다. 특허 허여가 포함된 모듈 단위 "
            "카피레프트로, EPL 모듈의 소스는 제공해야 하지만 더 큰 저작물은 독점으로 "
            "둘 수 있습니다."
        ),
    ),
    "EPL-2.0": LicenseSummary(
        en=(
            "The current Eclipse Public License: module-scoped copyleft with a "
            "patent grant, plus an optional secondary-license clause easing GPL "
            "compatibility."
        ),
        ko=(
            "현재 버전의 Eclipse Public License입니다. 특허 허여가 포함된 모듈 단위 "
            "카피레프트이며, GPL 호환을 돕는 선택적 보조 라이선스 조항이 "
            "추가됐습니다."
        ),
    ),
    "CDDL-1.0": LicenseSummary(
        en=(
            "Sun's file-level copyleft license with a patent grant: modified "
            "CDDL files stay CDDL, but they may be combined with proprietary "
            "code in a larger work."
        ),
        ko=(
            "특허 허여가 포함된 Sun의 파일 단위 카피레프트 라이선스입니다. 수정한 "
            "CDDL 파일은 CDDL로 유지해야 하지만, 더 큰 저작물에서 독점 코드와 결합할 "
            "수 있습니다."
        ),
    ),
    "CDDL-1.1": LicenseSummary(
        en=(
            "A maintenance revision of CDDL-1.0 with the same file-level "
            "copyleft and patent grant."
        ),
        ko=(
            "CDDL-1.0의 유지보수 개정판으로, 파일 단위 카피레프트와 특허 허여는 "
            "동일합니다."
        ),
    ),
    "LGPL-2.0-only": LicenseSummary(
        en=(
            "Library-scoped copyleft: the LGPL library itself stays LGPL and "
            "users must be able to relink it, but an application that merely "
            "links to it may stay proprietary."
        ),
        ko=(
            "라이브러리 단위 카피레프트입니다. LGPL 라이브러리 자체는 LGPL로 "
            "유지되고 사용자가 재링크할 수 있어야 하지만, 이를 링크하기만 하는 "
            "애플리케이션은 독점으로 둘 수 있습니다."
        ),
    ),
    "LGPL-2.0-or-later": LicenseSummary(
        en=(
            "LGPL-2.0 with the option to use any later LGPL version: "
            "library-scoped copyleft with a relink requirement; linking "
            "applications may stay proprietary."
        ),
        ko=(
            "이후 버전의 LGPL을 선택할 수 있는 LGPL-2.0입니다. 재링크 보장이 필요한 "
            "라이브러리 단위 카피레프트이며, 링크하는 애플리케이션은 독점으로 둘 수 "
            "있습니다."
        ),
    ),
    "LGPL-2.1-only": LicenseSummary(
        en=(
            "The most common LGPL version: library-scoped copyleft — keep the "
            "library's source available and let users relink it; your "
            "application may stay proprietary."
        ),
        ko=(
            "가장 널리 쓰이는 LGPL 버전입니다. 라이브러리 단위 카피레프트로, "
            "라이브러리 소스를 제공하고 사용자가 재링크할 수 있게 하면 "
            "애플리케이션은 독점으로 둘 수 있습니다."
        ),
    ),
    "LGPL-2.1-or-later": LicenseSummary(
        en=(
            "LGPL-2.1 with the option to use any later LGPL version: "
            "library-scoped copyleft with a relink requirement."
        ),
        ko=(
            "이후 버전의 LGPL을 선택할 수 있는 LGPL-2.1입니다. 재링크 보장이 필요한 "
            "라이브러리 단위 카피레프트입니다."
        ),
    ),
    "LGPL-3.0-only": LicenseSummary(
        en=(
            "LGPL built on GPLv3: library-scoped copyleft with a relink "
            "requirement, plus GPLv3's patent grant and anti-tivoization terms."
        ),
        ko=(
            "GPLv3를 기반으로 한 LGPL입니다. 재링크 보장이 필요한 라이브러리 단위 "
            "카피레프트에 GPLv3의 특허 허여와 설치 정보 제공 조항이 더해집니다."
        ),
    ),
    "LGPL-3.0-or-later": LicenseSummary(
        en=(
            "LGPL-3.0 with the option to use any later LGPL version: "
            "library-scoped copyleft with a relink requirement and a patent "
            "grant."
        ),
        ko=(
            "이후 버전의 LGPL을 선택할 수 있는 LGPL-3.0입니다. 재링크 보장이 필요한 "
            "라이브러리 단위 카피레프트이며 특허 허여를 포함합니다."
        ),
    ),
    "MS-RL": LicenseSummary(
        en=(
            "The Microsoft Reciprocal License: file-level copyleft with a patent "
            "grant — source files you take stay MS-RL, other files in the same "
            "work may use other terms."
        ),
        ko=(
            "Microsoft Reciprocal License입니다. 특허 허여가 포함된 파일 단위 "
            "카피레프트로, 가져다 쓴 소스 파일은 MS-RL로 유지되고 같은 저작물의 다른 "
            "파일은 다른 조건을 쓸 수 있습니다."
        ),
    ),
    "OFL-1.1": LicenseSummary(
        en=(
            "The SIL Open Font License: fonts may be used and embedded freely, "
            "but modified fonts stay OFL and the font may not be sold on its "
            "own."
        ),
        ko=(
            "SIL Open Font License입니다. 폰트를 자유롭게 사용하고 포함할 수 "
            "있지만, 수정한 폰트는 OFL로 유지해야 하고 폰트 자체만 판매할 수는 "
            "없습니다."
        ),
    ),
    "CC-BY-SA-4.0": LicenseSummary(
        en=(
            "Creative Commons Attribution-ShareAlike: credit the author, and "
            "license your adaptations under the same terms. Intended for "
            "content; its ShareAlike reach makes it a review item for products."
        ),
        ko=(
            "Creative Commons 저작자 표시-동일조건변경허락 라이선스입니다. 저작자를 "
            "표시하고, 2차적 저작물도 같은 조건으로 배포해야 합니다. 콘텐츠용 "
            "라이선스이며, 동일조건 조항의 적용 범위 때문에 제품에서는 검토 "
            "대상입니다."
        ),
    ),
    # ----- Strong copyleft / forbidden --------------------------------------------
    "GPL-2.0-only": LicenseSummary(
        en=(
            "Strong whole-program copyleft: if you distribute a binary, the "
            "entire work must be GPL-2.0 and its complete source must reach "
            "every recipient."
        ),
        ko=(
            "프로그램 전체에 적용되는 강한 카피레프트입니다. 바이너리를 배포하면 "
            "저작물 전체를 GPL-2.0으로 배포해야 하고, 모든 수령자에게 완전한 소스를 "
            "제공해야 합니다."
        ),
    ),
    "GPL-2.0-or-later": LicenseSummary(
        en=(
            "GPL-2.0 with the option to use any later GPL version: strong "
            "whole-program copyleft triggered by distributing a binary."
        ),
        ko=(
            "이후 버전의 GPL을 선택할 수 있는 GPL-2.0입니다. 바이너리 배포가 의무를 "
            "발생시키는, 프로그램 전체에 적용되는 강한 카피레프트입니다."
        ),
    ),
    "GPL-3.0-only": LicenseSummary(
        en=(
            "Strong whole-program copyleft with an express patent grant: "
            "distributing a binary obliges you to release the entire work's "
            "source under GPL-3.0."
        ),
        ko=(
            "명시적 특허 허여를 포함하며 프로그램 전체에 적용되는 강한 "
            "카피레프트입니다. 바이너리를 배포하면 저작물 전체의 소스를 GPL-3.0으로 "
            "공개해야 합니다."
        ),
    ),
    "GPL-3.0-or-later": LicenseSummary(
        en=(
            "GPL-3.0 with the option to use any later GPL version: strong "
            "whole-program copyleft with a patent grant."
        ),
        ko=(
            "이후 버전의 GPL을 선택할 수 있는 GPL-3.0입니다. 특허 허여를 포함하며 "
            "프로그램 전체에 적용되는 강한 카피레프트입니다."
        ),
    ),
    "AGPL-3.0-only": LicenseSummary(
        en=(
            "GPL-3.0 extended to network use: if users reach a modified version "
            "over a network, you must offer them its complete source. Hosting it "
            "as a service does not avoid the obligation."
        ),
        ko=(
            "네트워크 사용까지 적용 범위를 넓힌 GPL-3.0입니다. 사용자가 수정된 "
            "버전을 네트워크로 사용하면 그 완전한 소스를 제공해야 하며, 서비스로 "
            "호스팅한다고 해서 의무를 피할 수 없습니다."
        ),
    ),
    "AGPL-3.0-or-later": LicenseSummary(
        en=(
            "AGPL-3.0 with the option to use any later AGPL version: GPL "
            "copyleft whose source obligation is also triggered by network use."
        ),
        ko=(
            "이후 버전의 AGPL을 선택할 수 있는 AGPL-3.0입니다. 네트워크 사용으로도 "
            "소스 공개 의무가 발생하는 GPL 계열 카피레프트입니다."
        ),
    ),
    "SSPL-1.0": LicenseSummary(
        en=(
            "MongoDB's service-stack copyleft: offering the program as a service "
            "obliges you to release the source of the whole surrounding stack. "
            "It is not OSI-approved."
        ),
        ko=(
            "MongoDB가 만든 서비스 스택 카피레프트입니다. 프로그램을 서비스로 "
            "제공하면 이를 둘러싼 스택 전체의 소스를 공개해야 합니다. OSI 승인 "
            "라이선스가 아닙니다."
        ),
    ),
    "BUSL-1.1": LicenseSummary(
        en=(
            "A source-available license, not open source: production use is "
            "restricted until the Change Date, after which the work converts to "
            "an open-source Change License."
        ),
        ko=(
            "오픈소스가 아니라 소스 공개형(source-available) 라이선스입니다. 변경일 "
            "전까지 프로덕션 사용이 제한되며, 변경일이 지나면 오픈소스 변경 "
            "라이선스로 전환됩니다."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def obligation_text_ko(text: str) -> str | None:
    """Korean rendering of an English obligation paragraph, or ``None``.

    ``None`` means the paragraph has no translation — callers fall back to the
    English text rather than showing a blank row. The contract test keeps this
    from happening for catalog paragraphs, so ``None`` in practice only reaches
    obligations that did not come from the catalog.
    """
    return _OBLIGATION_TEXT_KO.get(text)


def license_summary(spdx_id: str | None) -> LicenseSummary | None:
    """Summary for a single SPDX id, or ``None`` for ids outside the catalog.

    Exact-match lookup, mirroring ``obligation_catalog.get_license_obligations``
    — compound expressions and ``LicenseRef-*`` ids have no summary.
    """
    if not spdx_id:
        return None
    return _LICENSE_SUMMARY.get(spdx_id)


def translated_obligation_texts() -> frozenset[str]:
    """Every English paragraph this module translates (for the contract test)."""
    return frozenset(_OBLIGATION_TEXT_KO)


def summarized_spdx_ids() -> frozenset[str]:
    """Every SPDX id this module summarizes (for the contract test)."""
    return frozenset(_LICENSE_SUMMARY)


__all__ = [
    "LicenseSummary",
    "license_summary",
    "obligation_text_ko",
    "summarized_spdx_ids",
    "translated_obligation_texts",
]
