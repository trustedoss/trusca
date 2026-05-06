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
| Project | 프로젝트 | A unit of source-tracked software registered in the portal; carries scans, components, and risk scores. | 포털에 등록된 소스 추적 단위. 스캔·컴포넌트·리스크 점수를 보유합니다. |
| Repository | 저장소 | The git source location (URL + branch) tied to a project. | 프로젝트와 연결된 git 소스 위치 (URL + 브랜치). |
| Risk Score | 리스크 점수 | Aggregated numeric indicator combining vulnerability severity and license risk for a project. | 프로젝트의 취약점 심각도와 라이선스 리스크를 합산한 수치 지표. |
| Cache (vulnerability) | 캐시 (취약점) | PostgreSQL-stored snapshot of DT findings, served when DT is unavailable. | DT findings의 PostgreSQL 보관 스냅샷. DT 장애 시 대체 데이터로 사용됩니다. |
| Workspace | 작업 공간 | The temporary filesystem area where source is fetched and scanned. | 소스를 가져와 스캔을 실행하는 임시 파일시스템 영역. |
| Reconnect | 재연결 | WebSocket auto-reconnect with exponential backoff during a scan stream. | 스캔 스트리밍 중 지수 백오프로 자동 재연결하는 동작. |
| Status — Queued / Running / Succeeded / Failed / Idle | 상태 — 대기 중 / 실행 중 / 성공 / 실패 / 스캔 전 | The lifecycle states of a scan (Idle = no scan has run yet for the project). | 스캔의 수명 주기 상태 (스캔 전 = 해당 프로젝트에 스캔이 아직 실행되지 않음). |
| Bootstrapping | 작업 공간 준비 | Scan pipeline step: preparing the workspace before fetching source. | 스캔 파이프라인 단계: 소스 수신 전 작업 공간을 준비. |
| Resolving Vulnerabilities | 취약점 탐지 | DT pipeline step: matching SBOM components against NVD/OSV/GitHub Advisory feeds. | DT 파이프라인 단계: SBOM 컴포넌트를 NVD/OSV/GitHub Advisory와 대조하여 취약점을 도출. |

Updated 2026-05-06 — Phase 2 PR #9 (WebSocket UI).
