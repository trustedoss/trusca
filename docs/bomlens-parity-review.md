# BomLens 격차 추적 (Parity Review)

자매 프로젝트 BomLens(`sktelecom/sbom-tools`, 로컬 경로 `~/projects/sbom-tools`)의 개선을 주기적으로 확인하고 TRUSCA 관점의 격차를 추적하는 살아있는 문서다. BomLens는 릴리즈 주기가 매우 짧으므로(2026-06 기준 3주에 6개 릴리즈) 1회성 분석 대신 이 문서를 갱신한다.

## 리뷰 절차

1. 새 릴리즈 태그 발견 시(또는 격주): `git -C ~/projects/sbom-tools log <기준점>..HEAD --oneline`으로 신규 변경을 확인한다.
2. 사용자 가치가 있는 신기능·개선을 아래 격차 표에 판정과 함께 추가하고, 종결된 항목의 상태를 갱신한다.
3. 아래 기준점을 새 HEAD 해시와 태그로 갱신하고, 리뷰 이력에 한 줄을 남긴다.
4. 판정 원칙: 역할 경계(BomLens = local-first 단발 스캔 도구, TRUSCA = 영속 거버넌스 포털 — `bomlens-internal/identity-and-direction.md`)에 따라 **포털에 어울리는 격차만** open으로 채택한다. 데스크톱 앱·local-first류는 `역할경계`로 표기한다.

## 마지막 리뷰 기준점

| 항목 | 값 |
|---|---|
| BomLens 커밋 | `3d1a1d3` |
| BomLens 태그 | v1.5.5 (2026-07-01) |
| 리뷰 일자 | 2026-07-02 |

## 격차 표

상태: open(격차 확인, 미착수) / in-progress / closed / 역할경계(격차 아님)

| # | 항목 | 우선순위 | 상태 | 근거 (BomLens ↔ TRUSCA) |
|---|---|---|---|---|
| 1 | CISA KEV 신호 표면화 + KEV→severity→EPSS 정렬 | P1 | **closed (#438, 2026-07-02)** | BomLens #203 미러. CISA 피드 일일 동기화(beat) + kev/kev_due_date 컬럼 + priority 기본 정렬 + KEV 뱃지. security-reviewer 2회전 APPROVE. 참고: KEV는 Trivy 출력에 없음(0.71.2 실측) — 피드 별도 동기화가 정답이었음. 후속: SLA 뱃지, admin/health 피드 상태 패널, KEV e2e |
| 2 | AI SBOM(CycloneDX 1.7 ML-BOM) 수용 + G7 최소요소 적합성 검사 | P1 | open | BomLens `g7-registry.json` 7클러스터 50요소(#290, 2026-07-02 완성). EU AI Act 2026-08-02 적용. BomLens 로드맵에 "11~12월 TRUSCA ingest 연계" 예고 ↔ TRUSCA `sbom_conformance.py`는 1.7/ML-BOM/G7 미인식 |
| 3 | NOTICE 라이선스 전문 번들 + copyright 표시 | P1 | open | BomLens `docker/lib/licenses/*.txt` 20+종 전문 번들 ↔ TRUSCA는 `reference_url` 링크만 — 자체 카탈로그의 license_text_inclusion_required 의무를 못 지킴 |
| 4 | Maven/Gradle 직접/간접 의존성 오분류 검증 | P1 | **closed (#435, 2026-07-02)** | 검증 결과 결함 실재(방향은 반대): 빈 루트 `dependsOn` 시 고아 섬 폴백의 정렬순 시딩이 **간접→직접 오분류**. BomLens #285의 근본 원인(cdxgen 플래그)은 TRUSCA에 없음. 수정: 자식 선언한 루트만 신뢰 + in-degree-0 폴백 |
| 5 | AI 특화 라이선스 플래그 (행동제한 RAIL/Llama/Gemma, 비상업 CC-BY-NC) | P2 | open | BomLens `license-flags.jq` + "License review needed" 표시 ↔ TRUSCA 카탈로그(32개)에 해당 어휘 없음. #2와 세트 |
| 6 | 스캔 간 비교(diff) 뷰 | P2 | open | BomLens #247(이전 실행 대비 변화) ↔ TRUSCA에 스캔 diff 없음. BomLens 문서가 "지속 추적은 포털 몫"으로 선 그은 영역 — 영속 DB를 가진 TRUSCA가 더 잘할 수 있음 |
| 7 | 라이선스 분류 카탈로그 확장 (32개 → SPDX 주요 라이선스) | P2 | open | BomLens `spdx-normalize.jq` 매핑 참고 ↔ TRUSCA `_LICENSE_CATEGORY_DEFAULTS` 32개, 미등재는 unknown |
| 8 | Excel 리포트 | P3 | open | CLAUDE.md가 약속했으나 미구현(PDF만). 구현 또는 약속 철회로 문서-구현 불일치 해소 필요 |
| 9 | 의존성 그래프 뷰 | P3 | open | BomLens cytoscape 그래프+트리 재설계(#243) ↔ TRUSCA Components는 테이블+드로어만 |
| 10 | 전역 검색(컴포넌트+CVE 크로스 프로젝트) | P3 | open | BomLens 상단바 전역 검색(#274) ↔ TRUSCA 없음 |
| 11 | SCANOSS vendored OSS 식별 | P3 | open | BomLens 기본 탑재(#271) ↔ TRUSCA 없음. C/C++ 고객 요구 시 착수 |
| 12 | 릴리즈 게이트 강화(published 이미지에서 first-scan 실증 후 publish) | P3 | open | BomLens #239/#241 draft→검증→publish ↔ TRUSCA release.yml에 이식 가치 |
| 13 | 바이너리/펌웨어/ROOTFS 스캔 | — | 역할경계 | BomLens 영역. "BomLens로 스캔 → TRUSCA ingest" 연계 문서화로 커버 권장 |
| 14 | 데스크톱 앱, local-first, `--byte-stable`·cosign 서명 | — | 역할경계 | BomLens 정체성 영역 |

## TRUSCA 우위 (참고)

Go 도달성 분석(govulncheck), 자동 재매칭 beat, VEX 임베드 4포맷 SBOM 수출, 팀/정책/승인 워크플로우, 감사로그, 빌드 차단 게이트(BomLens는 report-only 철학).

## BomLens 예고 기능 감시 목록

BomLens 내부 방향 문서(`bomlens-internal/identity-and-direction.md`)가 예고한 항목. 구현이 확인되면 격차 표로 승격한다.

| 예정 시기 | 기능 | TRUSCA 시사점 |
|---|---|---|
| 2026 9~10월 | 검출 모드 `DETECTION=build\|lightweight\|static` (설계: sbom-tools #300) | 빌드 없는 경량 스캔 — TRUSCA 스캔 파이프라인에도 유효한 옵션 |
| 2026 11~12월 | 취약점 변화 추적(재스캔 diff, 로컬 파일 기반) | 격차 표 #6과 같은 영역 — 포털이 먼저 완성하면 역할 경계가 명확해짐 |
| 2026 11~12월 | TRUSCA SBOM ingest 연계(업로드 편의) | TRUSCA 쪽 수용 준비 필요 — 특히 ML-BOM 1.7 수용(#2) |

## 리뷰 이력

| 일자 | 기준점 이동 | 요약 |
|---|---|---|
| 2026-07-02 | (최초) → `3d1a1d3`/v1.5.5 | 최초 전수 분석. BomLens 최근 3주(v1.5.0~v1.5.5, 402커밋): 웹 UI 전면 재설계(~20.8k라인), G7 7클러스터 완성, EPSS/KEV UI 표면화, 스캔 비교, 릴리즈 게이트, SCANOSS 기본 탑재. 격차 14건 판정(P1 4건) |
