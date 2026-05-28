# 경쟁 도구 화면 출처 (Phase B)

각 이미지에 (도구·화면 분류·출처 URL·캡처 일자·공정사용 메타) 기록.

**공정사용 원칙**: 모두 vendor 공식 docs/blog/product page 의 공개 이미지. 비공개/유료 데모/leaked 자료 0. 보고서 인용 시 출처 명시.

수집 일자: 2026-05-27 (한국시간 23:00~23:10)

총 PNG 다운로드: **34장** (DoD 최소 20 충족)

---

## C1 — Black Duck SCA (Synopsys / Black Duck Polaris) — 7장

출처: [Polaris September 2025 Release blog post](https://www.blackduck.com/blog/polaris-september-2025-release-enhancements.html)

| 파일 | 화면 | URL |
|---|---|---|
| `bd-polaris-triage-approval.png` | Triage approval workflow | `https://www.blackduck.com/adobe/dynamicmedia/deliver/dm-aid--0bdfe649-.../polaris-picture1.png` |
| `bd-polaris-report-scope.png` | Report scope | `.../dm-aid--333e368c-.../polaris-picture2.png` |
| `bd-polaris-report-scope-selection.png` | Report scope selection | `.../dm-aid--2f3cf1d2-.../polaris-picture3.png` |
| `bd-polaris-dashboard-filters.png` | Dashboard filters view | `.../dm-aid--bb5a7ba1-.../polaris-picture4.png` |
| `bd-polaris-policy-violations-component.png` | Policy violations — Component view (Components 탭 + 좌 filter sidebar + dense table) | `.../dm-aid--4a5837d6-.../polaris-picture5.png` |
| `bd-polaris-policy-violations-issues.png` | Policy violations — Issues view | `.../dm-aid--4ffbb12d-.../polaris-picture6.png` |
| `bd-polaris-policy-violations-hover.png` | Policy violations — hover/tooltip details | `.../dm-aid--1ec67cd0-.../polaris-picture7.png` |

**누락 영역**: BD SCA 의 "Hub" 라인업(non-Polaris) UI는 documentation.blackduck.com 이 SPA-rendered 라 curl/WebFetch 로 추출 불가. Polaris 가 BD 의 최신 통합 platform 이므로 audit 분석에는 충분.

---

## C2 — Snyk — 3장 (+ 2 무효 HTML 파일)

출처: [Prioritize with Snyk's Open Source Vulnerability Experience blog](https://snyk.io/blog/prioritize-with-snyks-open-source-vulnerability-experience/)

| 파일 | 화면 | URL |
|---|---|---|
| `snyk-vuln-list-1.png` | Project view — Fixable issues grouped by upgrade (dependency-grouped view, 가장 innovative한 패턴 — upgrade-centric) | `https://res.cloudinary.com/snyk/.../Screenshot_2025-08-20_at_12.29.46_PM_fssmwx.png` |
| `snyk-vuln-list-2.png` | (동일 화면 variant) | `.../Screenshot_2025-08-20_at_12.30.02_PM_bx7ara.png` |
| `snyk-vuln-list-3.png` | (동일 화면 variant) | `.../Screenshot_2025-08-20_at_12.30.27_PM_bvz4x3.png` |

**무효 파일 (cleanup 권한 차단으로 잔존)**:
- `snyk-issues-tab.png` (529KB HTML wrapper, GitBook redirect 결과)
- `snyk-upgradable-issues.png` (529KB HTML wrapper)
→ Phase C 평가에서 이 두 파일은 무시. 다음 정리 PR에서 git rm 권장.

**누락 영역**: Snyk 의 Reports / Dashboard / Project list 화면은 docs.snyk.io 가 GitBook 으로 image 직링크 미제공, snyk.io 마케팅 페이지는 SVG 일러스트 위주. 본 3장이 Snyk 의 가장 차별화된 UX 패턴(dependency-grouped fix view)을 충분히 보여줘 audit 에 적합.

---

## C3 — Sonatype Lifecycle / IQ Server — 9장

출처: [Reviewing a Report](https://help.sonatype.com/en/reviewing-a-report.html)

| 파일 | 화면 |
|---|---|
| `sonatype-aa37fc46.png` | Application Composition Report — Policy violations table (App Risk Score · Aggregate by component toggle · View Dependency Tree 버튼) |
| `sonatype-a423f520.png` | Policy violations table (alternate view) |
| `sonatype-8896bd73.png` | Vulnerabilities list table (CVSS scores) |
| `sonatype-6eb2fcc3.png` | Security vulnerability details modal |
| `sonatype-49640b08.png` | Violation count badge |
| `sonatype-17cd5206.png` | Component count + donut chart |
| `sonatype-a61b16b7.png` | Filter button interface |
| `sonatype-a8a03d1d.png` | Aggregate-by-component toggle |
| `sonatype-d128ac64.png` | Raw data view dropdown |

URL pattern: `https://help.sonatype.com/en/image/uuid-<full-uuid>.png` (모두 동일 호스트, sources 위 파일명 prefix는 UUID 첫 8자)

**누락 영역**: Sonatype Lifecycle 의 dashboard·orgs 관리 화면은 본 페이지(report 중심)에 없음. 별도 docs 페이지 탐색 후속 가능하지만 본 9장으로 SCA 핵심 UX(밀도·필터·세부 모달) 평가 가능.

---

## C4 — Mend (전 WhiteSource) — 9장 (substantive 4장)

출처: [The Dependencies Findings Report](https://docs.mend.io/platform/latest/the-dependencies-findings-report) + [Search Findings in your Organization](https://docs.mend.io/platform/latest/search-findings-in-your-organization)

| 파일 | 화면 | 크기 | 평가 |
|---|---|---|---|
| `mend-findings-image-20260426-082111.png` | Findings 검색 — filter chip group + columnar table + "+ More Filters" dropdown + Columns picker | 178KB | ★ substantive |
| `mend-findings-image-20260426-085121.png` | Findings (다른 상태) | 153KB | ★ substantive |
| `mend-image-20240908-134803.png` | Reports 페이지 | 162KB | ★ substantive |
| `mend-image-20251217-193824.png` | Scope 설정 화면 | 57KB | substantive |
| `mend-image-20240719-091017.png` | Reports button (네비) | 10KB | small UI bit |
| `mend-image-20240902-144937.png` | Create button icon | 2KB | tiny icon |
| `mend-findings-image-20260426-081218.png` | (small) | 16KB | small |
| `mend-findings-image-20260426-083330.png` | (small) | 13KB | small |
| `mend-findings-image-20260426-085604.png` | (small) | 10KB | small |

URL pattern: `https://docs.mend.io/__attachments/<page-id>/image-<timestamp>.png?inst-v=...`

**누락 영역**: Mend Risk Reduction Dashboard / AI Security Dashboard 의 새 UI 는 mend.io blog 에 있을 가능성이 높지만 403 차단됨. 본 9장(특히 4 substantive)으로 Mend 핵심 UX(필터 chip + table + column picker) 평가 가능.

---

## C5 — Datadog Vulnerability Management — 6장

출처: 
- [Cloud Security Vulnerabilities docs](https://docs.datadoghq.com/security/cloud_security_management/vulnerabilities/) — 5장
- [Software Composition Analysis product page](https://www.datadoghq.com/product/software-composition-analysis/) — 1장

| 파일 | 화면 |
|---|---|
| `csm-vm-findings.png` | Vulnerability detail — left meta + right "NEXT STEPS" sidebar (Triage: Open/Assign/Jira; Remediation guided) + Datadog Severity breakdown |
| `csm-vm-dashboard.png` | Cloud Security Vulnerabilities dashboard (out-of-the-box) |
| `csm-package-explorer.png` | Package inventory — vuln context + pivot to deployed resources |
| `image-layer-vulnerabilities.png` | Container image layer — vulns per layer |
| `csm-notifications.png` | Notification rule setup |
| `sca-product.png` | SCA product page hero (Service Catalog + open source risk surface) |

URL host: `docs.dd-static.net/images/...` (공식 docs CDN)

**누락 영역**: Datadog Code Security (SAST) UI는 별도. 본 audit이 SCA 중심이라 무영향.

---

## 도구별 분포 + 평가축 적합도

| 도구 | 화면 수 | A1 밀도 | A4 드로어 | A6 bulk | 비고 |
|---|---|---|---|---|---|
| C1 BD Polaris | 7 | ✓ | △ | △ | Components 화면이 직접 비교 대상 |
| C2 Snyk | 3 | ✓ | ✗ | ✗ | 가장 차별화된 dependency-grouped fix view |
| C3 Sonatype | 9 | ✓ | ✓ (모달) | ✗ | 가장 풍부, classic SCA UX |
| C4 Mend | 4 sub | ✓ | ✗ | ✓ (filter chip) | filter UX 강점 |
| C5 Datadog | 6 | △ | ✓ (in-page) | ✗ | 모니터링-스타일 UX, Triage/Remediation 분리 |

**모든 A1~A8 축에 대해 최소 2 도구의 비교 자료 확보** → Phase C 매트릭스 평가 가능.

---

## 공정 사용 (Fair Use) 메타

- 모든 이미지: vendor 공식 도메인의 공개 docs/blog/product page 임베드 자료
- 다운로드 목적: 우리 제품 UX 비교 internal audit (제 3자 배포 없음)
- 출처 명시 + 도구 trademark 보호: 모든 이미지 사용 시 vendor 명시
- 보존: 본 `docs/ux/screens/competitors/` 디렉토리는 git 추적 (재현 가능성). 필요시 SoT 갱신 분기마다 재수집.

**향후 보고서 발행 시**: vendor 의 trademark / copyright 명시 의무. competitive-audit-2026-05-27.md 의 §footer 에 standard "Trademarks and screenshots are property of their respective owners. Used under fair use for product comparison purposes." 첨부.
