// TrustedOSS Portal Policy Rules
// 금지(ERROR): 상업적 배포 시 소스 공개 의무 또는 특허 위험이 있는 라이선스
// 조건부(WARNING): 법무 검토가 필요한 카피레프트 라이선스

val FORBIDDEN_LICENSES = setOf(
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "SSPL-1.0",
    "BUSL-1.1"
)

val CONDITIONAL_LICENSES = setOf(
    "LGPL-2.0-only",
    "LGPL-2.0-or-later",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MPL-2.0",
    "EPL-1.0",
    "EPL-2.0",
    "CDDL-1.0"
)

ruleSet(ortResult = ortResult, licenseInfoResolver = licenseInfoResolver) {

    // 금지 라이선스 규칙
    packageRule("FORBIDDEN_LICENSE") {
        require {
            -isExcluded()
        }
        licenseRule("FORBIDDEN_LICENSE", LicenseView.CONCLUDED_OR_DECLARED_AND_DETECTED) {
            require {
                +isSpdxLicense()
                -isExcluded()
            }
            if (license.toString() in FORBIDDEN_LICENSES) {
                error(
                    "패키지 '${pkg.metadata.id}' 가 금지된 라이선스 '$license' 를 사용합니다. " +
                    "이 라이선스는 소스 공개 의무가 있어 상업적 배포가 제한됩니다. " +
                    "법무팀 승인 없이 사용 불가합니다.",
                    "금지된 라이선스 의존성을 제거하거나 법무팀에 검토를 요청하세요."
                )
            }
        }
    }

    // 조건부 라이선스 규칙
    packageRule("CONDITIONAL_LICENSE") {
        require {
            -isExcluded()
        }
        licenseRule("CONDITIONAL_LICENSE", LicenseView.CONCLUDED_OR_DECLARED_AND_DETECTED) {
            require {
                +isSpdxLicense()
                -isExcluded()
            }
            if (license.toString() in CONDITIONAL_LICENSES) {
                warning(
                    "패키지 '${pkg.metadata.id}' 가 조건부 라이선스 '$license' 를 사용합니다. " +
                    "이 라이선스는 특정 조건(예: 동적 링크, 수정 사항 공개)에서만 허용됩니다.",
                    "법무/IPR팀에 사용 방식을 검토 받으세요."
                )
            }
        }
    }
}
