# BomLens 격차 추적 (Parity Review)

자매 프로젝트 BomLens(`sktelecom/sbom-tools`, 로컬 경로 `~/projects/bomlens`)의 개선을 주기적으로 확인하고 TRUSCA 관점의 격차를 추적하는 살아있는 문서다. BomLens는 릴리즈 주기가 매우 짧으므로(2026-06 기준 3주에 6개 릴리즈) 1회성 분석 대신 이 문서를 갱신한다.

## 리뷰 절차

1. 새 릴리즈 태그 발견 시(또는 격주): `git -C ~/projects/sbom-tools log <기준점>..HEAD --oneline`으로 신규 변경을 확인한다.
2. 사용자 가치가 있는 신기능·개선을 아래 격차 표에 판정과 함께 추가하고, 종결된 항목의 상태를 갱신한다.
3. 아래 기준점을 새 HEAD 해시와 태그로 갱신하고, 리뷰 이력에 한 줄을 남긴다.
4. 판정 원칙: 역할 경계(BomLens = local-first 단발 스캔 도구, TRUSCA = 영속 거버넌스 포털 — `bomlens-internal/identity-and-direction.md`)에 따라 **포털에 어울리는 격차만** open으로 채택한다. 데스크톱 앱·local-first류는 `역할경계`로 표기한다.

## 마지막 리뷰 기준점

| 항목 | 값 |
|---|---|
| BomLens 커밋 | `6b2ca03` |
| BomLens 태그 | v1.8.2 + 25커밋 (2026-07-18, #429) |
| 리뷰 일자 | 2026-07-18 (8차 — C4 트랙 재평가) |

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
| 8 | Excel 리포트 | P3 | **closed (#454, 2026-07-03 Phase G)** | CLAUDE.md "Excel/PDF 보고서" 약속 이행. openpyxl 기반 `build_report_xlsx`(Overview/Components/Vulnerabilities 3시트, formula-injection 방어) + `GET /projects/{id}/vulnerability-report.xlsx` + report_type enum `vuln_xlsx`(0037) + Reports 탭 Excel 버튼. 단위(injection·결정성)·통합(존재은닉·이력)·e2e·EN/KO·docs 완비 |
| 9 | 의존성 그래프 뷰 | P3 | **closed (#455, 2026-07-03 Phase H-1)** | `GET /projects/{id}/dependency-graph`(edge 테이블 직렬화, 노드 상한 5000, 존재은닉 404) + cytoscape+dagre FE 포팅(BomLens 참조) + Components 탭 테이블↔그래프 토글(`?view=graph`) + 트리/배너 폴백. 시드에 이진트리 엣지 추가, 백엔드 22 테스트, e2e 토글 스모크 |
| 10 | 전역 검색(컴포넌트+CVE 크로스 프로젝트) | P3 | **closed (#456, 2026-07-03 Phase H-2)** | BomLens 상단바 전역 검색(#274) 미러. `GET /v1/search`(컴포넌트/CVE 크로스 프로젝트) + `core.authz.team_scope_filter` 단일 초크포인트(is_superuser-only 게이트) 팀 격리 + ⌘K 팔레트 Components/CVEs 딥링크 + 전용 `SEARCH_RATE_LIMIT`. security-reviewer PASS(테넌트 격리 end-to-end 확인, Low 2건 반영). 교차누출-0 격리 테스트 |
| 11 | SCANOSS vendored OSS 식별 | P3 | **closed (#459, 2026-07-04 Phase J)** | BomLens 기본 탑재(#271) 미러. `integrations/scanoss.py`(scancode 어댑터, full-file 매치만) + 파이프라인 스테이지(cdxgen→scancode→**scanoss**→trivy) + `raw_data.source="scanoss"`+detected 라이선스 + scanoss==1.53.1 워커 이미지 + FE step_scanoss. **판정: BomLens는 기본 ON이나 TRUSCA는 온프레미스라 `SCANOSS_ENABLED` opt-in·기본 OFF**(핑거프린트 외부 전송 프라이버시). security-reviewer PASS(기본OFF egress 차단·시크릿 비유출 확인, Low 2건 반영: 길이캡·키 리댁션). 부수: 워커 이미지에서 linux-libc-dev 커널헤더 제거(76 CVE 영구 해소) |
| 12 | 릴리즈 게이트 강화(published 이미지에서 first-scan 실증 후 publish) | P3 | **closed (#457, 2026-07-03 Phase I)** — v0.13.0 실검증 대기 | BomLens #239/#241 draft→검증→publish 미러. release.yml에 `release`(draft GitHub Release) + `release-gate`(발행 이미지 pull·compose 부팅·quickstart 스모크→`gh release edit --draft=false`) 잡, `docker-compose.smoke.yml` 오버레이, 기여자 가이드 releasing.md(EN/KO). semgrep run-shell-injection 회피(env 간접). 코드 머지 완료 — 게이트 실동작은 다음 실릴리즈 v0.13.0 태그에서 최종 확인 |
| 13 | 바이너리/펌웨어/ROOTFS 스캔 | — | 역할경계 | BomLens 영역. "BomLens로 스캔 → TRUSCA ingest" 연계 문서화로 커버 권장 |
| 14 | 데스크톱 앱, local-first, `--byte-stable`·cosign 서명 | — | 역할경계 | BomLens 정체성 영역 |
| 15 | 런타임 스코프 필터(test/dev 의존성 SBOM 제외) | P1 | **closed (Phase K, 2026-07-11)** | BomLens #331/#335/#337/#341 미러. cdxgen 이 test/provided/dev 의존성까지 담아 CVE·의무 과대 집계 — TRUSCA 동일 결함 확인. `integrations/sbom_scope_filter.py`: Maven 은 cdxgen scope 태그(hasScopes 가드), Node 는 **BomLens 와 달리 npm 재해석 서브프로세스 없이** 기존 npm_lockfile 분류로(dev 만 제거, keep-if-unknown). persist·서명·Trivy 전에 copy-then-commit 으로 적용, 인제스트 경로는 의도적 무필터(업로드 SBOM = 공급자 선언 진실). 기본 켬(`SCAN_SCOPE_FILTER_*`, 정확한 falsy 토큰만 끔). FE 요약 밴드 "N건 제외" 표시. **Android(release runtime classpath)는 워커에 Android SDK 부재로 후속 백로그** — gradle 유래 컴포넌트는 scope 미탑재라 hasScopes 가드로 무회귀 |
| 16 | EOL(endoflife.date) 컴포넌트 플래그 | P2 | **closed (Phase M, 2026-07-11)** | BomLens #368(enrich-eol.sh) 미러. purl 맵 **원본 그대로 벤더링**(바이트 동일 계약 테스트, %40 정규화는 매처에서 흡수) + 스냅숏은 빌드타임 베이크 대신 **저장소 벤더링**(scripts/refresh_eol_snapshot.py, 릴리즈마다) — Docker 빌드 무네트워크·air-gap 기본 동작. 판정은 component_versions 카탈로그 컬럼(0038, KEV 동형)에 저장, persist 훅에서 스탬프(소스+인제스트 공통). FE: EolBadge(KevBadge 미러)·컬럼·`?eol=true` 필터·드로어 행·Overview 칩. 운영(0039): 주간 재스탬프 beat(항상 로컬 실행) + opt-in 실시간 수집(`EOL_REFRESH_ENABLED` 기본 꺼짐, 위생 플로어) + admin/health 스냅숏 패널(180일 stale 경고) |
| 18 | 버전 currency/staleness (최신 패치 대비 낙후·releases-behind) | P2 | **closed — 오프라인 MVP (2026-07-18)** | BomLens v1.8.0 미러. **오프라인 half 구현**: 벤더링된 EOL 스냅샷의 cycle `latest`를 재사용해 설치버전과 비교 → `component_versions.currency_state`('current'/'outdated'/'unknown')+`currency_latest` 카탈로그 컬럼(0040), 같은 EOL 매처·스탬프 훅·주간 beat에 편승(egress 0). CurrencyBadge(EolBadge 미러, medium 톤)·Components 컬럼·드로어 행·Overview "N outdated" 칩·`?outdated=true` 필터·FE↔BE `CURRENCY_STATES` 정합 계약. deps.dev "절대 최신·N releases-behind"(egress)는 게이트 opt-in 후속으로 분리(미구현) |
| 19 | 규제 크로스워크 (G7→EU AI Act Annex IV·한국 AI기본법) | P4 | **미채택 (8차, 2026-07-18)** | BomLens v1.8.1(`regulation-crosswalk.json`). G7 적합성은 이미 수집(#2). "결측 요소→규제 문서 의무" 매핑은 정보성 레이어, 실사용자 없어 값 낮음 → 보류 |
| 20 | copyleft 강도 분류 (network/strong/weak/permissive) | P4 | **미채택 (8차, 2026-07-18)** | BomLens Unreleased(#420, `bomlens:licenseClass`). 허용/조건부/금지 티어와 직교하는 세분화. 한계효용 작음 → 보류 |
| 17 | iOS CocoaPods/SPM lockfile 스캔 | P2 | **closed (Phase L, 2026-07-11)** | BomLens #347 미러. Podfile 존재 + pod CLI 부재 시 cdxgen cocoapods 수집기가 TypeError 로 스캔 전체를 죽이던 크래시 → `--exclude-type cocoapods`(어댑터+사이드카, depth-3 제한 글롭) + `integrations/cocoapods_lockfile.py` 가 Podfile.lock PODS: 블록에서 컴포넌트·그래프 오프라인 복원(**syft 불요** — BomLens 와 분기, npm_lockfile 규율). Package.resolved 만 커밋한 저장소도 swift 감지, 사이드카는 커밋된 lockfile 존재 시 `swift package resolve` 스킵. cdxgen 12.3.3 의 Package.resolved 오프라인 파싱 실측 검증(채록 픽스처가 그 증거) |

## TRUSCA 우위 (참고)

Go 도달성 분석(govulncheck), 자동 재매칭 beat, VEX 임베드 4포맷 SBOM 수출, 팀/정책/승인 워크플로우, 감사로그, 빌드 차단 게이트(BomLens는 report-only 철학).

## BomLens 예고 기능 감시 목록

BomLens 내부 방향 문서(`bomlens-internal/identity-and-direction.md`)가 예고한 항목. 구현이 확인되면 격차 표로 승격한다.

| 예정 시기 | 기능 | TRUSCA 시사점 |
|---|---|---|
| 2026 9~10월 | 검출 모드 `DETECTION=build\|lightweight\|static` (설계: sbom-tools #300) | 빌드 없는 경량 스캔 — TRUSCA 스캔 파이프라인에도 유효한 옵션 |
| 2026 11~12월 | 취약점 변화 추적(재스캔 diff, 로컬 파일 기반) | 격차 표 #6과 같은 영역 — 포털이 먼저 완성하면 역할 경계가 명확해짐 |
| 2026 11~12월 | TRUSCA SBOM ingest 연계(업로드 편의) | TRUSCA 쪽 수용 준비 필요 — 특히 ML-BOM 1.7 수용(#2) |
| ~~협의 대기~~ **전제 폐기(8차, 2026-07-18)** | 다언어 도달성 분석(Java/JS 등 call-graph 산출) | C4 원 전제("BomLens 산출→TRUSCA 소비")는 **성립 안 함**. BomLens 내부 로드맵(`improvement-roadmap.md:75`)이 도달성 분석을 정책 게이트·triage·VEX와 함께 **"포털(TRUSCA)의 영역"으로 명시 분류**하고 무상태 로컬 도구 정체성은 불변 선언 → 산출 주체가 될 의사 없음. 한편 **TRUSCA는 Go 도달성(govulncheck)을 이미 탑재**(`tasks/scan_reachability.py`, v2.3 r1). 따라서 C4는 "TRUSCA 자체 다언어 확장"으로 재정의되며, 오프라인 OSS 콜그래프 도구 부재(상용 해자)로 보류. 상세: `commercial-parity-candidates.md` C4 |

## 리뷰 이력

| 일자 | 기준점 이동 | 요약 |
|---|---|---|
| 2026-07-02 | (최초) → `3d1a1d3`/v1.5.5 | 최초 전수 분석. BomLens 최근 3주(v1.5.0~v1.5.5, 402커밋): 웹 UI 전면 재설계(~20.8k라인), G7 7클러스터 완성, EPSS/KEV UI 표면화, 스캔 비교, 릴리즈 게이트, SCANOSS 기본 탑재. 격차 14건 판정(P1 4건) |
| 2026-07-02 (2차) | `3d1a1d3` → `d66f202` | Phase A 착수 전 리뷰. 신규 6커밋(#305~#310) 전부 데스크톱(역할경계) — 새 격차 없음. TRUSCA 재탐색으로 #6(스캔 비교)이 이미 구현돼 있음을 확인해 정정. #1·#4 closed, #2 in-progress |
| 2026-07-03 (3차) | `d66f202` → `522735a` | Phase B 착수 전 리뷰. **#306: G7 레지스트리 v2**(missingPath 모델별 커버리지 — 다중 모델 SBOM에서 any-model 시맨틱 결함 수정, openness 산문 선언 수용) — 우리 #440 포팅이 한 세대 뒤가 됨, 동기화 후속 등록. #311(OS 패키지 CVE 매칭 복구)·#312(문서)는 BomLens 내부. #3 in-progress |
| 2026-07-03 (4차) | `522735a` → `17d6e59` | Phase D 착수 전 리뷰. 신규 13커밋 전부 CI·테스트·데스크톱·문서 인프라 — 새 격차 없음. 참고: #325 syft를 1.6 핀(Trivy 0.70 한계) — TRUSCA는 0.72라 무관. #5 in-progress |
| 2026-07-03 (5차) | `17d6e59` → `2591230` | Phase E 착수 전 리뷰. v1.6.0 태그 신설이나 기준점 이후 실질 변경은 릴리즈 chore 1건뿐(데스크톱·G7·스캐너 수정 묶음, 전부 기존 격차/역할경계 범위) — 새 격차 없음. #7 in-progress |
| 2026-07-03 (완료 스윕) | (기준점 유지 `2591230`) | Phase E~I 연속 완료: #7 카탈로그 확장(#451)·#6 diff e2e(#452)·#8 Excel(#454)·#9 의존성 그래프(#455)·#10 전역 검색(#456)·#12 릴리즈 게이트(#457). 부수: jsonata CVE-2026-52746 .trivyignore 억제(#453, 저장소 전역 image-scan 언블록). **open 격차는 #11(SCANOSS, 조건부 Phase J)만 남음.** #12는 코드 머지 완료, v0.13.0 태그에서 게이트 실동작 최종 확인 예정 |
| 2026-07-04 (6차) | `2591230` → `9cc37e0` | Phase J 종료 후 리뷰. 기준점 이후 신규는 #329(firmware CVE DB 게이팅)뿐 — 펌웨어는 역할경계(#13), 새 격차 없음. **#11 SCANOSS closed(#459) — 마지막 격차 마감. 격차 표 P1~P3 실질 전부 closed, open 없음(역할경계 2건 제외).** Phase J 부수로 워커 이미지 linux-libc-dev 커널헤더 제거(Trivy DB 갱신發 76 CVE 영구 해소, #453과 같은 클래스지만 억제 대신 durable 제거) |
| 2026-07-18 (8차) | `5a62094` → `6b2ca03` | v1.8.0~v1.8.2 + 25커밋 리뷰(C4 트랙 재평가 겸). **C4 전제 폐기**: BomLens는 도달성 분석을 "포털의 영역"으로 명시 분류하고 무상태 정체성 불변 선언 → "BomLens 산출→TRUSCA 소비" 구도 불가. TRUSCA는 Go 도달성(govulncheck)을 이미 탑재하므로 C4는 자체 다언어 확장으로 재정의·보류(OSS 콜그래프 도구 부재). **부수 발견·수정**: Wave 7 문서(analysis-types)와 기존 comparison·data-sources가 도달성을 "planned·미탑재"로 오기 → Go govulncheck 실탑재에 맞게 정정. **신규 격차**: 버전 currency/staleness(#18 채택→구현), 규제 크로스워크(#19 보류)·copyleft 강도(#20 보류). 역할경계: SPDX 2.3 생성, 공급사 SBOM 검증 규칙, 펌웨어 인덱스, 대용량 테이블 가상스크롤 |
| 2026-07-11 (7차) | `9cc37e0` → `5a62094` | v1.7.0 릴리즈 + 후속 27커밋, 총 80커밋 리뷰. 스캔 정밀도 연작이 핵심: 런타임 스코프 필터(#331/#335/#337/#341)·EOL 플래그(#368)·iOS lockfile(#347) — 신규 격차 3건(#15~#17) 판정 즉시 Phase K·L·M 으로 전부 구현·closed. 격차 아님: 펌웨어 CPE 인덱스(#330, 역할경계 #13), Trivy 0.72 범프(TRUSCA 기보유), Windows/문서 인프라. 부수 확인: BomLens 웹 UI 에 **TRUSCA 업로드 기능**(#336) 탑재 — 격차가 아니라 연계 실동작 시작. 후속 백로그: Android release-classpath 스코프 필터(워커 Android SDK 탑재 시), 컨테이너 스캔의 Trivy `eosl`(이미지 OS 단위) 표면화 |
