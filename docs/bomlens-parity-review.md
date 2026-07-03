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
| BomLens 커밋 | `2591230` |
| BomLens 태그 | v1.6.0 (2026-07-03) |
| 리뷰 일자 | 2026-07-03 (5차 — Phase E 착수 전) |

## 격차 표

상태: open(격차 확인, 미착수) / in-progress / closed / 역할경계(격차 아님)

| # | 항목 | 우선순위 | 상태 | 근거 (BomLens ↔ TRUSCA) |
|---|---|---|---|---|
| 1 | CISA KEV 신호 표면화 + KEV→severity→EPSS 정렬 | P1 | **closed (#438, 2026-07-02)** | BomLens #203 미러. CISA 피드 일일 동기화(beat) + kev/kev_due_date 컬럼 + priority 기본 정렬 + KEV 뱃지. security-reviewer 2회전 APPROVE. 참고: KEV는 Trivy 출력에 없음(0.71.2 실측) — 피드 별도 동기화가 정답이었음. 후속 완료(#445, 2026-07-03 Phase C): SLA 뱃지 3상태 + admin/health KEV 피드 패널(kev_sync_state) + e2e 5시나리오. 잔여: C0 첫 자연 beat 운영 확인(01:45 UTC 후) |
| 2 | AI SBOM(CycloneDX 1.7 ML-BOM) 수용 + G7 최소요소 적합성 검사 | P1 | **closed (#440·#441, 2026-07-02)** | BomLens 레지스트리(51요소/7클러스터) 벤더링 + Python 술어 포팅(정합 계약 3건), ingest 1.7 수용(Trivy 파싱 실측), 클러스터 패널(EN/KO)·e2e 4건·가이드 신설. security-reviewer 2회전 APPROVE. EU AI Act(8/2) 한 달 전 출시. 후속: G7 스크린샷 2장, persist NUL 세척 |
| 3 | NOTICE 라이선스 전문 번들 + copyright 표시 | P1 | **closed (#443, 2026-07-03)** | 전문 32종 전수(BomLens 21 + SPDX 공식 11, 카탈로그 정합 계약 테스트) + 3포맷 License Texts 섹션 + copyright(SQL 클램프·레지스트리 URL 폴백). security-reviewer CHANGES REQUESTED(Medium 1 외 3) → 전건 반영 → PASS. license_text_inclusion_required 의무를 제품 스스로 충족 |
| 4 | Maven/Gradle 직접/간접 의존성 오분류 검증 | P1 | **closed (#435, 2026-07-02)** | 검증 결과 결함 실재(방향은 반대): 빈 루트 `dependsOn` 시 고아 섬 폴백의 정렬순 시딩이 **간접→직접 오분류**. BomLens #285의 근본 원인(cdxgen 플래그)은 TRUSCA에 없음. 수정: 자식 선언한 루트만 신뢰 + in-degree-0 폴백 |
| 5 | AI 특화 라이선스 플래그 (행동제한 RAIL/Llama/Gemma, 비상업 CC-BY-NC) | P2 | **closed (#449, 2026-07-03)** | BomLens `license-flags.jq` 포팅(정합 계약) + License.review_flag 컬럼·백필 + Licenses 탭 뱃지·필터·드로어 + NOTICE "License review needed" 절. security-reviewer APPROVE. G7 v2 missing 칩 후속 동봉 |
| 6 | 스캔 간 비교(diff) 뷰 | P2 | **closed (#452, 2026-07-03 Phase F)** | diff는 #28로 이미 구현(`project_diff_service.py` + `ComparePage.tsx` + Releases 진입). Phase F: e2e 갭 마감 — 시드 `--scan-count 2`(첫 프로젝트에 델타 있는 2번째 succeeded 스캔, 실 Postgres로 1/1/1·1/1 검증) + `compare.spec.ts`(Compare 게이트·added/removed/changed·introduced/resolved·swap) + 사용자 가이드 Compare 절 보강(EN/KO). 스캔 직후 요약 배너(BomLens #247 대비)는 옵션(F3)으로 보류 |
| 7 | 라이선스 분류 카탈로그 확장 (32개 → SPDX 주요 라이선스) | P2 | **closed (#451, 2026-07-03 Phase E)** | 카탈로그 32→52종(허용 17+조건부 3, 전문 SPDX 공식 vendored) + `services/license_normalize.py`(BomLens `spdx-normalize.jq` 포팅, `_extract_spdx_ids` name-only 회복 — 미인식은 unknown 유지) + 3자 정합 계약(분류↔카탈로그↔전문). 판정: OFL-1.1/CC-BY-SA-4.0/MS-RL=조건부, OpenSSL/BSD-4-Clause=허용(advertising→notice 의무). 로컬 991 passed |
| 8 | Excel 리포트 | P3 | in-progress (Phase G) | CLAUDE.md가 약속했으나 미구현(PDF만). Phase G: openpyxl 기반 `build_report_xlsx`(Overview/Components/Vulnerabilities 3시트, formula-injection 방어) + `GET /projects/{id}/vulnerability-report.xlsx` + report_type enum `vuln_xlsx`(0037) + Reports 탭 Excel 버튼 |
| 9 | 의존성 그래프 뷰 | P3 | open | BomLens cytoscape 그래프+트리 재설계(#243) ↔ TRUSCA Components는 테이블+드로어만 |
| 10 | 전역 검색(컴포넌트+CVE 크로스 프로젝트) | P3 | in-progress (Phase H-2) | BomLens 상단바 전역 검색(#274) ↔ TRUSCA 없음. H-2: `GET /v1/search`(컴포넌트/CVE 크로스 프로젝트, `team_scope_filter` 단일 초크포인트 팀 격리) + ⌘K 팔레트 Components/CVEs 카테고리·딥링크. security-reviewer Producer-Reviewer |
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
| 2026-07-02 (2차) | `3d1a1d3` → `d66f202` | Phase A 착수 전 리뷰. 신규 6커밋(#305~#310) 전부 데스크톱(역할경계) — 새 격차 없음. TRUSCA 재탐색으로 #6(스캔 비교)이 이미 구현돼 있음을 확인해 정정. #1·#4 closed, #2 in-progress |
| 2026-07-03 (3차) | `d66f202` → `522735a` | Phase B 착수 전 리뷰. **#306: G7 레지스트리 v2**(missingPath 모델별 커버리지 — 다중 모델 SBOM에서 any-model 시맨틱 결함 수정, openness 산문 선언 수용) — 우리 #440 포팅이 한 세대 뒤가 됨, 동기화 후속 등록. #311(OS 패키지 CVE 매칭 복구)·#312(문서)는 BomLens 내부. #3 in-progress |
| 2026-07-03 (4차) | `522735a` → `17d6e59` | Phase D 착수 전 리뷰. 신규 13커밋 전부 CI·테스트·데스크톱·문서 인프라 — 새 격차 없음. 참고: #325 syft를 1.6 핀(Trivy 0.70 한계) — TRUSCA는 0.72라 무관. #5 in-progress |
| 2026-07-03 (5차) | `17d6e59` → `2591230` | Phase E 착수 전 리뷰. v1.6.0 태그 신설이나 기준점 이후 실질 변경은 릴리즈 chore 1건뿐(데스크톱·G7·스캐너 수정 묶음, 전부 기존 격차/역할경계 범위) — 새 격차 없음. #7 in-progress |
