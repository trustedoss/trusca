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
| C1a | 라이선스 요약·의무사항 KO | 카탈로그 52종 번역(전문 정본은 영어 유지), KO 필드 추가(Alembic), 언어 토글 연동, EN/KO 집합 동등성 정합 테스트 | ⏳ |
| C3 | 정책 반영 맞춤 SBOM 프로파일 | 수출 시 프로파일 선택 → 위반 컴포넌트를 CycloneDX properties/SPDX annotation 주석 또는 필터. 기존 수출 4포맷·정책엔진·VEX 재사용. 수출 UI + CI 게이트 문서 동행 | ⏳ |

## 4. v0.16.0 — 스캐너 정밀도 + 문서 parity (Wave 7)

| # | 항목 | 상태 |
|---|------|------|
| W8-#48 | Python 라이선스 메타 보강 (requirements.txt 셀프스캔 90% unknown → PyPI 메타) | ⏳ |
| W8-#49 | Ruby(Gemfile)·dotnet(.nuspec) 라이선스 보강 (100% unknown) | ⏳ |
| K-f1 | 컨테이너 스캔 Trivy `eosl`(이미지 OS 단위 EOL) 표면화 | ⏳ |
| K-f2 | `detected_env` 정상화의 local_docker/k8s 실행기 라우팅 영향 확인 | ⏳ |
| W7-A~F | 문서 parity 5건 — Triage 통합 가이드, Analysis Types, Best Practices 4페이지, FAQ, DefectDojo/ThreadFix 조사(구현 여부는 조사 후 판단). EN/KO 동시, docs-uat 단언 동행 | ⏳ |

## 5. v0.17.0 — C1b CVE 한글화 + UX 확장

| # | 항목 | 상태 |
|---|------|------|
| C1b | CVE 상세 KO — KEV·Critical 우선 기계/지연 번역. 번역 캐시 테이블 + Celery 지연 태스크 + 원문 병기 UI | ⏳ |
| W9-#53 | Vulnerabilities "Group by upgrade" 토글 (Snyk 패턴) | ⏳ |
| W9-#55 | Time-series 차트 (P4) — 검토 후 채택 여부 결정, 미채택이면 사유 기록 후 종결 | ⏳ 검토 |
| W9-#56 | Aggregate-by-component 토글 (P4) — 위와 동일 | ⏳ 검토 |

## 6. 조건부 트랙 C4 — 다언어 도달성 분석 (BomLens 협업)

상용 대비 최대 격차이나 BomLens 스코프 합의가 선행 조건. 합의 준비물까지만 이 플랜에서 진행한다.

| # | 항목 | 상태 |
|---|------|------|
| C4-1 | 인터페이스 설계 문서 — BomLens call-graph 산출 포맷 + TRUSCA ingest 스키마 초안 (ADR) | ⏳ |
| C4-2 | 협의 제안서 — 대상 언어 우선순위(Java·JS), 산출물 경계, 검증 방법 | ⏳ |
| C4-3 | 합의 확정 시 별도 릴리즈 트랙으로 분리해 이 문서 갱신. 미확정 상태로 구현 착수 금지 | — |

## 7. 상시 트랙 — 부채 청산·유지 (우선순위 낮음, 관련 코드 작업에 편승)

- stage rename `dt_*` → `sbom_upload`/`vuln_match` (W6-#43f 장기 부채)
- security-reviewer 후속 M-2/M-4/L-1/L-2 (W6-chore-#42-followup)
- RFC 7807 도메인별 `type` URI 도입 (`core/errors.py` — 현재 사실상 전부 `about:blank`)
- aiosmtplib 3→5 major 업그레이드 (별도 PR)
- Android release-classpath 스코프 필터 — 워커 Android SDK 탑재 선행, 수요 확인 전 보류
- BomLens 예고 기능 감시 — `docs/bomlens-parity-review.md` 감시 목록을 BomLens 릴리즈 태그마다 재점검

## 8. 검증 기준 (릴리즈별)

- v0.14.0: 릴리즈 게이트가 draft→검증→publish를 실제 차단/통과시키는지 태그에서 확인. 전역 토스트는 mutation 실패 Playwright 케이스, zip-bomb은 실물 아카이브 재현 테스트.
- 운영 트랙: 데모 배포 후 demo-health-canary green + KEV beat 01:45 UTC 자연 실행 로그.
- v0.15.0: EN/KO 카탈로그 집합 동등성 테스트 + 언어 토글 e2e. C3는 프로파일별 수출 SBOM golden fixture(실물 채록, 보강 5규칙 #3).
- 문서: docs-uat 워크플로 + `node tools/ko-style/lint.mjs --changed --fail-on S2`.
