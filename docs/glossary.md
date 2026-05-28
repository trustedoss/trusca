# TrustedOSS Domain Glossary

This glossary is the single source of truth for TrustedOSS Portal domain terms in
English and Korean. UI strings, error messages, and documentation MUST use the
canonical Korean translation listed here. When introducing a new domain term,
add the entry to this table **before** using it in code or copy.

Contribution rules: keep entries concise (one-line definitions); use the
established Korean form (no synonyms) once a term is on the table; do not
translate proper nouns (Dependency-Track, SBOM, CVE, ORT, Trivy, cdxgen).

| English | 한국어 | Definition (EN) | 정의 (KO) |
|---------|--------|-----------------|-----------|
| Component | 컴포넌트 | A package or library used in a project. | 프로젝트에서 사용하는 패키지·라이브러리. |
| Vulnerability | 취약점 | A known security flaw in a component, typically a CVE. | 컴포넌트에서 알려진 보안 결함, 주로 CVE로 식별됨. |
| License | 라이선스 | The legal terms governing the use of an open-source component. | 오픈소스 컴포넌트 사용을 규정하는 법적 조건. |
| Scan | 스캔 | An end-to-end run that detects components, licenses, and vulnerabilities for a project. | 프로젝트의 컴포넌트·라이선스·취약점을 탐지하는 일련의 실행. |
| Severity — Critical / High / Medium / Low / Info | 심각도 — 치명 / 높음 / 중간 / 낮음 / 정보 | The risk tier assigned to a vulnerability, driving UI color tokens and the build gate. | 취약점에 부여되는 리스크 등급. UI 색상 토큰과 빌드 차단 게이트의 기준이 됩니다. |
| SBOM | SBOM | Software Bill of Materials — a machine-readable inventory of components (CycloneDX, SPDX). | 소프트웨어 자재 명세 — 컴포넌트의 기계 판독 가능한 목록 (CycloneDX, SPDX). |
| CVE | CVE | Common Vulnerabilities and Exposures — the public identifier for a known vulnerability. | 공통 취약점·노출 식별자 — 알려진 취약점의 공식 ID. |
| Allowed License | 허용 라이선스 | Licenses freely usable without legal review (MIT, Apache-2.0, BSD-2/3, ISC). | 법무 검토 없이 자유롭게 사용 가능한 라이선스 (MIT, Apache-2.0, BSD-2/3, ISC 등). |
| Conditional License | 조건부 라이선스 | Licenses requiring legal review and approval (LGPL, MPL, EPL, CDDL). | 법무 검토와 승인이 필요한 라이선스 (LGPL, MPL, EPL, CDDL 등). |
| Forbidden License | 금지 라이선스 | Licenses that block the build (AGPL, GPL, SSPL, BUSL). | 빌드를 차단하는 라이선스 (AGPL, GPL, SSPL, BUSL 등). |
| Component Approval | 컴포넌트 승인 | Workflow for vetting components: Pending → Under Review → Approved / Rejected. | 컴포넌트 검토 워크플로우: 대기 → 검토 중 → 승인 / 반려. |
| Audit Log | 감사 로그 | Append-only record of every write operation, with actor, action, and target. | 모든 쓰기 작업의 추가 전용 기록. 행위자·동작·대상을 보존합니다. |
| Build Gate | 빌드 차단 게이트 | CI step that exits with code 1 when a Critical CVE or forbidden license is found. | Critical CVE 또는 금지 라이선스 발견 시 종료 코드 1로 빌드를 중단하는 CI 단계. |
| Build gate verdict — Pass / Fail | 빌드 차단 판정 — 통과 / 차단 | The build-gate outcome for a project's latest successful scan: Pass = nothing blocks the build, Fail = a Critical CVE or forbidden license blocks it. | 프로젝트의 최근 성공 스캔에 대한 빌드 차단 게이트 결과: 통과 = 빌드를 막는 항목 없음, 차단 = Critical CVE 또는 금지 라이선스가 빌드를 막음. |
| Container scan | 컨테이너 스캔 | A scan that runs Trivy against a Docker image reference to find OS-package vulnerabilities, as opposed to a source scan. | 소스 스캔과 달리 Docker 이미지 참조에 Trivy를 실행하여 OS 패키지 취약점을 찾는 스캔. |
| Image reference | 이미지 참조 | The name and tag/digest identifying a container image to scan (e.g. `ghcr.io/org/app:1.2.3`). | 스캔할 컨테이너 이미지를 식별하는 이름과 태그/다이제스트 (예: `ghcr.io/org/app:1.2.3`). |
| Project | 프로젝트 | A unit of source-tracked software registered in the portal; carries scans, components, and risk scores. | 포털에 등록된 소스 추적 단위. 스캔·컴포넌트·리스크 점수를 보유합니다. |
| Repository | 저장소 | The git source location (URL + branch) tied to a project. | 프로젝트와 연결된 git 소스 위치 (URL + 브랜치). |
| Risk Score | 리스크 점수 | Overall project risk 0–100 = the worse of the Security and License scores (max). Non-saturating. | 프로젝트 전체 리스크 0–100 = 보안 점수와 라이선스 점수 중 더 높은 값(최댓값). 포화되지 않습니다. |
| Security Score | 보안 점수 | Security-axis risk 0–100 set by the worst CVE severity present (Critical→75–100, High→50–74, Medium→25–49, Low→1–24); count scales within the band, never saturating. | 존재하는 최악의 CVE 심각도로 결정되는 보안 축 리스크 0–100 (치명→75–100, 높음→50–74, 중간→25–49, 낮음→1–24). 밴드 안에서 건수에 따라 변하며 포화되지 않습니다. |
| License Score | 라이선스 점수 | License-axis risk 0–100 set by the worst license category (Forbidden→75–100, Conditional→25–49, Unknown→1–24); conditional licenses alone never reach Critical. | 존재하는 최악의 라이선스 분류로 결정되는 라이선스 축 리스크 0–100 (금지→75–100, 조건부→25–49, 미상→1–24). 조건부 라이선스만으로는 Critical에 도달하지 않습니다. |
| Cache (vulnerability) | 캐시 (취약점) | PostgreSQL-stored snapshot of DT findings, served when DT is unavailable. | DT findings의 PostgreSQL 보관 스냅샷. DT 장애 시 대체 데이터로 사용됩니다. |
| Workspace | 작업 공간 | The temporary filesystem area where source is fetched and scanned. | 소스를 가져와 스캔을 실행하는 임시 파일시스템 영역. |
| Reconnect | 재연결 | WebSocket auto-reconnect with exponential backoff during a scan stream. | 스캔 스트리밍 중 지수 백오프로 자동 재연결하는 동작. |
| Status — Queued / Running / Succeeded / Failed / Idle | 상태 — 대기 중 / 실행 중 / 성공 / 실패 / 스캔 전 | The lifecycle states of a scan (Idle = no scan has run yet for the project). | 스캔의 수명 주기 상태 (스캔 전 = 해당 프로젝트에 스캔이 아직 실행되지 않음). |
| Bootstrapping | 작업 공간 준비 | Scan pipeline step: preparing the workspace before fetching source. | 스캔 파이프라인 단계: 소스 수신 전 작업 공간을 준비. |
| Resolving Vulnerabilities | 취약점 탐지 | DT pipeline step: matching SBOM components against NVD/OSV/GitHub Advisory feeds. | DT 파이프라인 단계: SBOM 컴포넌트를 NVD/OSV/GitHub Advisory와 대조하여 취약점을 도출. |
| VEX State — New | VEX 상태 — 신규 | A vulnerability finding that has just been discovered and not yet triaged. | 새로 발견되어 아직 분류되지 않은 취약점 상태. |
| VEX State — Analyzing | VEX 상태 — 분석 중 | Triage in progress; an analyst is investigating the finding. | 분석가가 해당 취약점을 조사 중인 분류 진행 상태. |
| VEX State — Exploitable | VEX 상태 — 악용 가능 | The finding is confirmed exploitable in this project's context (CycloneDX VEX). | 해당 프로젝트 맥락에서 악용 가능함이 확인된 상태 (CycloneDX VEX 용어). |
| VEX State — Not affected | VEX 상태 — 해당 없음 | The component is present but the vulnerable code path is not reachable. | 컴포넌트가 포함되어 있으나 취약 코드 경로가 실행되지 않는 상태. |
| VEX State — False positive | VEX 상태 — 오탐 | The detection is incorrect; the project is not actually vulnerable. | 잘못된 탐지로, 실제로는 취약하지 않은 상태. |
| VEX State — Suppressed | VEX 상태 — 억제됨 | An operator chose to silence/dismiss this finding (CycloneDX VEX `not_affected` with explicit suppression). | 운영자가 해당 취약점을 의도적으로 억제·무시한 상태 (CycloneDX VEX 용어). |
| VEX State — Fixed | VEX 상태 — 수정됨 | The finding has been resolved (component upgraded or patch applied). | 컴포넌트 업그레이드 또는 패치 적용으로 해결된 상태. |
| Declared license | 선언된 라이선스 | License declared in the package's own metadata (e.g. `package.json`, `pom.xml`). | 패키지 자체 메타데이터(`package.json`, `pom.xml` 등)에 선언된 라이선스. |
| Concluded license | 확정된 라이선스 | License finally chosen by ORT after reconciling declared and detected sources. | ORT가 선언/검출 결과를 종합해 최종 확정한 라이선스. |
| Detected license | 검출된 라이선스 | License automatically detected from source files by the scanner. | 스캐너가 소스 파일에서 자동 검출한 라이선스. |
| OSI Approved | OSI 승인 | License approved by the Open Source Initiative as conforming to the Open Source Definition. | Open Source Initiative가 오픈소스 정의에 부합한다고 승인한 라이선스. |
| FSF Free/Libre | FSF 자유 소프트웨어 | License classified by the Free Software Foundation as a free software license. | Free Software Foundation이 자유 소프트웨어 라이선스로 분류한 라이선스. |
| ORT Match | ORT 매치 | The raw rule-evaluation record ORT emits per finding (rule name, severity, message). | ORT가 발견 항목마다 출력하는 원시 규칙 평가 기록(규칙명·심각도·메시지). |
| Obligation | 의무사항 | A duty arising from a license (attribution, source disclosure, copyleft, etc.) that must be honored when redistributing the component. | 라이선스가 부여하는 의무(저작자 표시·소스 공개·카피레프트 등). 컴포넌트 재배포 시 반드시 이행해야 합니다. |
| Obligation kind — Attribution | 의무 종류 — 저작자 표시 | Original copyright notice must be preserved in source and/or user-facing materials. | 원 저작권 고지를 소스 또는 최종 사용자 자료에 보존해야 하는 의무. |
| Obligation kind — NOTICE preservation | 의무 종류 — NOTICE 보존 | The upstream NOTICE file (Apache-2.0 §4(d) and similar) must be carried with the distribution. | 상위 패키지의 NOTICE 파일(Apache-2.0 §4(d) 등)을 배포물에 함께 포함해야 하는 의무. |
| Obligation kind — Source disclosure | 의무 종류 — 소스 공개 | Recipients must be granted access to the corresponding source code on demand. | 수령자가 요구할 경우 해당 소스 코드에 접근할 수 있게 해야 하는 의무. |
| Obligation kind — Copyleft | 의무 종류 — 카피레프트 | Derivative works must be released under the same (or compatible) license terms. | 파생물은 동일(또는 호환) 라이선스로 공개해야 하는 의무. |
| Obligation kind — Modifications | 의무 종류 — 변경 표시 | Modified files must carry prominent notices stating the changes made. | 변경된 파일에는 변경 사실을 명시한 표시를 두드러지게 남겨야 하는 의무. |
| Obligation kind — Dynamic linking | 의무 종류 — 동적 링킹 | LGPL-style requirement: end-users must be able to relink against a modified library. | LGPL류의 의무: 최종 사용자가 수정된 라이브러리로 재링크할 수 있어야 함. |
| Obligation kind — No endorsement | 의무 종류 — 보증 금지 | Project name or contributors may not be used to endorse derivative products without permission. | 허락 없이 프로젝트 이름이나 기여자를 파생 제품 보증에 사용할 수 없음. |
| NOTICE file | NOTICE 파일 | Generated attribution document listing third-party licenses, components, and obligations from a project's latest scan. | 프로젝트 최근 스캔에서 추출한 제3자 라이선스·컴포넌트·의무사항을 정리한 자동 생성 attribution 문서. |
| EPSS | EPSS | Exploit Prediction Scoring System — the probability (0–1) that a CVE is exploited in the wild within the next 30 days. Complements CVSS. | 악용 예측 점수 체계 — 해당 CVE가 향후 30일 내 실제로 악용될 확률(0~1). CVSS를 보완합니다. |
| EPSS percentile | EPSS 백분위 | The rank of an EPSS score among all scored CVEs, read as "this CVE is more likely to be exploited than N% of CVEs". | EPSS 점수가 전체 채점 CVE 중 차지하는 순위. "이 CVE가 전체 CVE의 N%보다 악용 가능성이 높음"으로 해석. |
| CVSS | CVSS | Common Vulnerability Scoring System — the standardized severity score (0–10) of a vulnerability. | 공통 취약점 점수 체계 — 취약점의 표준 심각도 점수(0~10). |

Updated 2026-05-23 — v2.1 (EPSS UI first-class: EPSS / EPSS percentile / CVSS terms).
