# TRUSCA 완성도 마스터플랜 (v0.14 ~ v0.17)

> 작성: 2026-07-16 | 상태: 진행 중
> 이 문서는 릴리즈 단위 완성도 로드맵의 단일 진실이다. 각 항목 완료 시 상태 열을 갱신하고,
> 릴리즈 태그 컷 시점에 해당 절을 닫는다. 출처 문서의 개별 표기와 어긋나면 이 문서를 신뢰한다.

## 0. 배경과 원칙

v0.13.1 기준 BomLens 격차 17건은 전부 해소됐고, 남은 완성도 과제는
상용 격차 후보(C1·C3·C4), post-GA 트래커 잔여(Wave 7 문서, W8 스캐너 보강, W9 UX),
운영 레인(O1~O4, 데모 SaaS 미배포), 품질 탐색에서 발견된 폴리시 갭이다.

- 목표선: 기능·안정성·디자인·문서 전 분야 균형. 실행 단위는 릴리즈(v0.14~).
- C4(다언어 도달성)는 BomLens 협의가 선행 조건 — 합의 준비물까지만 진행하는 조건부 트랙.
- C1 라이선스 한글화는 Claude 번역을 그대로 반영한다(사용자 결정, 2026-07-16).
- 각 릴리즈는 CLAUDE.md 공통 DoD를 따르고, 보안 관련 항목은 Producer-Reviewer 패턴을 거친다.

출처 문서: `docs/commercial-parity-candidates.md`(C1·C3·C4),
`docs/post-ga-execution-tracker.md`(Wave 7·W8·W9·O 레인),
`docs/sessions/2026-07-11-phase-klm-bomlens-v170-parity.md` §4(후속 백로그).

## 1. v0.14.0 — 출하 마감 + 안정성·에러 UX

| # | 항목 | 출처 | 상태 |
|---|------|------|------|
| 1 | EOL 기능 릴리즈화 (CHANGELOG 정리, `refresh_eol_snapshot.py` 릴리즈 절차 편입) | CHANGELOG Unreleased | ✅ 릴리즈 준비 PR |
| 2 | 릴리즈 노트에 스코프 필터 수치 감소 + `SCAN_SCOPE_FILTER_ENABLED=false` 복원 안내 | 07-11 세션 블로커 | ✅ v0.14.0 노트 |
| 3 | 릴리즈 게이트(#457) 실동작 최종 확인 — v0.14.0 태그가 첫 실검증 | parity #12 | ⏳ 태그 시 |
| 4 | quickstart-gate main 연속 실패 해결 — 원인: seed 검증 픽스처로 프로젝트 8개, `--demo-only` 플래그 도입 | 내부 결함 #8 | ✅ PR #483 (게이트 green 확인) |
| 5 | 프론트 전역 mutation 에러 토스트 — 39개 mutation 전수 조사, opt-out 메타 35곳 | 품질 탐색 | ✅ PR #484 |
| 6 | ErrorBoundary 마감 — 문구 i18n + `role="alert"` (크래시 리포터는 실사용자 생길 때까지 보류) | 품질 탐색 | ✅ PR #484 |
| 7 | W8-#47 zip-bomb 가드 UX — 비율 가드에 10 MiB 크기 하한 (실물 Juice Shop 17.0.0 추출 검증) | post-GA W8 | ✅ PR #487 |
| 8 | 감사로그 사각 보강 — 실사각은 PR 코멘트·아카이브 업로드 2곳 (backup은 기커버, preservation은 파생 아티팩트, upgrade_recommendation은 순수 모듈로 오탐). security-reviewer 2회전 APPROVE | 품질 탐색 | ✅ PR #488 |
| 9 | 릴리스 노트 KO 번역 5건 + SBOM 가이드 Excel 표류·깨진 앵커 수정 | 품질 탐색 | ✅ PR #486 |
| 10 | (편승) 워커 Go 1.25.12 범프 — CVE-2026-39822, image-scan 게이트 복구 | CI 장애 대응 | ✅ PR #485 머지 |

## 2. 운영 트랙 O — v0.14.0 태그 직후 병행

데모 SaaS는 미배포 상태(2026-07-16 확인). 워크플로는 이미 있으므로 가동·검증이 중심.

| # | 항목 | 상태 |
|---|------|------|
| O1 | 컨테이너 이미지 게시 확인 (release.yml, v0.14.0 태그) | ⏳ |
| O2 | 데모 SaaS 배포 (Hetzner CAX31, deploy-hetzner.yml) + demo-health-canary 가동 | ⏳ |
| O2a | KEV C0 운영 확인 — 라이브에서 첫 자연 beat(01:45 UTC) 검증 (O2 선행) | ⏳ |
| O2b | install-uat-l1 재배선(install.sh 구동) + `INSTALL_ENABLE_L1` (구체안은 PR #470 본문) | ⏳ |
| O3 | Helm OCI 게시 + ArtifactHub 등록 | ⏳ |
| O4 | 문서 스크린샷 갱신 (W11 디자인 반영본) | ⏳ |

## 3. v0.15.0 — C1a 라이선스 한글화 + C3 맞춤 SBOM 프로파일

| # | 항목 | 요지 | 상태 |
|---|------|------|------|
| C1a | 라이선스 요약·의무사항 KO | 카탈로그 52종 번역(전문 정본은 영어 유지). 마이그레이션 0건 — 코드 카탈로그(services/license_translations.py) + 응답 시 부착. EN/KO 드리프트 계약 테스트 | ✅ #491 머지 |
| C3 | 정책 반영 맞춤 SBOM 프로파일 | 수출 시 `?profile=policy-annotated\|policy-filtered`. 게이트 위반맵 헬퍼(`compute_component_policy_categories`, 공유 CATEGORY_RANK) 재사용, 기본 수출 byte-stable 불변, 서명은 기본 수출만(프로파일 미서명 명시). security-reviewer 2회전 APPROVE | ✅ #492 머지 |

## 4. v0.16.0 — 스캐너 정밀도 + 문서 parity (Wave 7)

| # | 항목 | 상태 |
|---|------|------|
| W8-#48 | Python 라이선스 메타 보강 — 조사 결과 PyPI enrichment(`integrations/license_fetcher/`)는 이미 구현·배선됨(골든 python-pip 베이스라인이 bare requirements.txt→PyPI 해석 검증). 진짜 결함은 fetcher가 air-gap 게이트 없이 무조건 egress한 것 → `LICENSE_FETCH_ENABLED`(기본 ON) 추가로 마감 | ✅ 머지 |
| W8-#49 | Ruby(Gemfile)·dotnet(.nuspec) 라이선스 보강 (100% unknown) — `license_fetcher`에 RubyGems(`pkg:gem/`, v2 API `licenses` 배열)·NuGet(`pkg:nuget/`, registration API `licenseExpression`) fetcher 추가. XML 대신 JSON API로 nuspec entity-expansion DoS 회피 | ✅ 머지 |
| K-f1 | 컨테이너 스캔 Trivy `eosl`(이미지 OS 단위 EOL) 표면화 — Trivy 이미지 리포트 최상위 `Metadata.OS.EOSL`을 파싱해 `scan_metadata['os']`(family/name/eosl)에 저장(마이그레이션 0건), 스캔 상세에 EOL 패널 노출. 신규 egress 없음(번들 DB), DB 신선도 caveat 명시. 실물 alpine-3.19 픽스처로 EOSL=true 채록 | ✅ 머지 |
| K-f2 | `detected_env` 정상화의 local_docker/k8s 실행기 라우팅 영향 확인 — 조사 결과 실제 결함: 사이드카가 detected_env는 project_root(내부 clone 루트)로 판정하면서 android compileSdk 읽기·스캔 대상은 outer source_dir를 써 git 스캔에서 오탐. `SbomGenRequest.project_root`+`effective_root`로 수정. security-reviewer APPROVE. (k8s 실행기는 미구현이라 무관) | ✅ 머지 |
| W7-A~F | 문서 parity — Triage 통합 가이드(user-guide/triage.md), Analysis Types(reference/analysis-types.md), Best Practices 4페이지(best-practices/), FAQ(reference/faq.md). EN/KO 동시, docs-uat 단언 동행, Docusaurus 빌드·docs-uat lint·ko-style 전부 green. sidebars에 Best practices 카테고리 신설 + 누락됐던 v0.13.1/v0.14.0 릴리즈 노트 배선 정정. **W7-E(소급 릴리즈 노트 v2.1/2.2/2.3)는 stale-skip** — 실제 스킴은 0.x이고 v0.10~v0.14 노트가 모두 이미 존재, "v2.x"는 트래커 구표기(마스터플랜이 오버라이드). 소급 대상 없음. **W7-F(DefectDojo/ThreadFix)는 defer** — 구현·의도 부재, 트래커 자체가 미등재 유지 지시 | ✅ 머지 |

## 5. v0.17.0 — Vulnerabilities UX 확장 (C1b 드롭)

| # | 항목 | 상태 |
|---|------|------|
| C1b | ~~CVE 상세 KO 기계번역~~ **드롭(2026-07-18 사용자 결정)** — CVE 설명문은 제품명·버전·함수명이 섞인 기술 문장이라 기계번역 오역이 보안 판단을 흐릴 위험이 크고, 실무상 CVE 원문은 영어가 표준. 제품 첫 외부 egress 경로 + 캐시 테이블 + Celery + 키·비용 대비 값 낮음. C1a(유한·법률성·손번역 품질통제)와 성격 반대. 유한한 UI·구조화 정보(심각도·VEX 상태·워크플로우 문구)는 이미 i18n 현지화됨 → CVE 원문은 영어(정본) 유지가 일관 | ❌ 드롭 |
| W9-#53 | Vulnerabilities "Group by upgrade" 토글 (Snyk 패턴) — 서버 계산 upgrade-clusters 엔드포인트(기존 추천 엔진·게이트 open-status 재사용), Flat⇄업그레이드별 세그먼트 토글, 접이식 클러스터 카드+기존 드로어 재사용. EN/KO. | ✅ #500 머지 |
| W9-#55 | ~~Time-series 차트~~ **미채택(2026-07-18 검토 결정)** — 그릴 지표가 영속되지 않음: `risk_score`는 요청 시 계산·미저장(`models/*.py` grep 0건), 보존 정책이 supersede 스캔을 findings째 7일 후 하드 삭제(`scan-retention.md:30`, `scan_retention.py:289-335`)해 이력이 구조적으로 축적 안 됨. 차트 이전에 지표 스냅샷 테이블 신설/보존정책 변경이라는 선행 인프라가 필요하고, 차팅 라이브러리도 부재. 실사용자·이력 없는 현 단계 값 대비 비용 큼. 향후 실운영 이력이 쌓이고 지표 영속을 도입하면 재검토 여지. | ❌ 미채택 |
| W9-#56 | ~~Aggregate-by-component 토글~~ **미채택(2026-07-18 검토 결정)** — 컴포넌트 단위 취약점 그룹핑이 이미 2곳에 존재: W9-#53 "Group by upgrade"(findings를 component_version별 그룹핑)와 Components 탭의 컴포넌트별 severity·CVE 개수 컬럼(+Vulnerabilities 탭 딥링크, `ComponentsTab.tsx:739-759`). 세 번째 집계 뷰는 대부분 중복이며 제안서 자체가 "이미 부분 대체 → 우선순위 낮음"으로 평가. | ❌ 미채택 |

**v0.17.0 종결**: C1b 드롭 + W9-#53 출하 + W9-#55/#56 미채택으로 v0.17.0 UX 확장 트랙 마감. 남은 로드맵은 조건부 트랙 C4(BomLens 협의 선행)와 §7 상시 부채뿐.

## 6. 조건부 트랙 C4 — 다언어 도달성 분석 (전제 폐기 → 재정의·보류)

**2026-07-18 재평가**: C4의 원 전제("BomLens가 call-graph 산출 → TRUSCA가 ingest")는 폐기한다. BomLens 조사(파리티 8차) 결과 BomLens는 도달성 분석을 정책 게이트·triage·VEX와 함께 **"포털(TRUSCA)의 영역"으로 명시 분류**하고(`bomlens-internal/improvement-roadmap.md:75`) 무상태 로컬 도구 정체성을 불변 선언 → 산출 주체가 될 의사가 없다. 따라서 C4-1(인터페이스 설계)·C4-2(협의 제안)는 상류 전제가 사라져 무의미.

**재정의**: C4 = "TRUSCA 자체 다언어 도달성". 이미 **Go 도달성은 탑재 완료**(`tasks/scan_reachability.py`, govulncheck, v2.3 r1 — 소스 스캔 후 best-effort, 기본 ON, 게이트 신호로도 사용). 남은 격차는 Java·JS·Python 등으로의 확장.

**진짜 장벽은 데이터 (ADR-0004, 2026-07-18 실증)**: 이전에 "OSS 콜그래프 도구 부재"라 적었으나 이는 부정확 — 콜그래프 도구는 있다(JS/TS [Jelly] 현역·OSS, Java WALA/Soot/Eclipse Steady, Python PyCG). 진짜 해자는 **비-Go 생태계의 오픈 함수 단위 취약-심볼 데이터 부재**다. 함수 단위 어드바이저리는 전 생태계 ~1%, Go만 ~31%(Go DB가 심볼 큐레이션) — 이래서 govulncheck는 turnkey고, Black Duck조차 도달성이 **Java 전용 + 사람 큐레이션**이다. 콜그래프를 뽑아도 "무엇에 도달하는지" 타겟 데이터가 없다.

**결정**: 다언어 확장 **보류**(사유 = 도구가 아니라 데이터 해자). Go 도달성은 유지(Black Duck 대비 자동·오프라인 우위). C4-1/C4-2 폐기, C4-3 유지. 재개 트리거·어댑터 재사용 설계는 `docs/decisions/0004-multilanguage-reachability-deferred.md` 참조.

[Jelly]: https://github.com/cs-au-dk/jelly

## 7. 상시 트랙 — 부채 청산·유지 (우선순위 낮음, 관련 코드 작업에 편승)

- stage rename `dt_*` → `sbom_upload`/`vuln_match` (W6-#43f 장기 부채)
- security-reviewer 후속 M-2/M-4/L-1/L-2 (W6-chore-#42-followup)
- RFC 7807 도메인별 `type` URI 도입 (`core/errors.py` — 현재 사실상 전부 `about:blank`)
- aiosmtplib 3→5 major 업그레이드 (별도 PR)
- Android release-classpath 스코프 필터 — 워커 Android SDK 탑재 선행, 수요 확인 전 보류
- BomLens 예고 기능 감시 — `docs/bomlens-parity-review.md` 감시 목록을 BomLens 릴리즈 태그마다 재점검
- **버전 currency/staleness (파리티 #18)** — ✅ 오프라인 MVP 구현 완료. EOL 스냅샷 cycle `latest` 재사용, `component_versions.currency_*` 컬럼(0040)+같은 매처·스탬프·beat 편승(egress 0), CurrencyBadge·컬럼·드로어·Overview 칩·`?outdated=true` 필터. deps.dev 절대최신·N releases-behind(egress)는 게이트 opt-in 후속(미구현)
- license_fetcher 하드닝 (W8-#49 리뷰 후속, 6개 fetcher 공통): ① `quote(name, safe="")` 백필(현재 crates/pypi/maven/pkggo는 `safe='/'` 상속 — in-registry 경로 traversal 방어, gem/nuget은 W8-#49에서 이미 적용), ② `base.request_with_retry`에 응답 본문 크기 상한(디컴프레션 밤 방어, 공유 코드라 전 fetcher 일괄)

## 8. 검증 기준 (릴리즈별)

- v0.14.0: 릴리즈 게이트가 draft→검증→publish를 실제 차단/통과시키는지 태그에서 확인. 전역 토스트는 mutation 실패 Playwright 케이스, zip-bomb은 실물 아카이브 재현 테스트.
- 운영 트랙: 데모 배포 후 demo-health-canary green + KEV beat 01:45 UTC 자연 실행 로그.
- v0.15.0: EN/KO 카탈로그 집합 동등성 테스트 + 언어 토글 e2e. C3는 프로파일별 수출 SBOM golden fixture(실물 채록, 보강 5규칙 #3).
- 문서: docs-uat 워크플로 + `node tools/ko-style/lint.mjs --changed --fail-on S2`.
