---
session_prompt: 스캔 파이프라인 갭(G1~G4) 테스트 계획 + 순차 실행
date_authored: 2026-05-22
authoring_session: scan-pipeline-gaps test plan
status: complete
relates_to:
  - docs/sessions/2026-05-22-scan-pipeline-gaps-handoff.md
  - PRs #96 #97 #99 #101 #102 #103 #104 #105
---

# 테스트 계획 — 스캔 파이프라인 갭 (G1~G4)

> 핸드오프(`2026-05-22-scan-pipeline-gaps-handoff.md`)의 §2 갭이 PR #96~#105로
> 머지 완료된 뒤, 실환경 검증·E2E·게이트를 정리한 계획. `-complete.md`는 없으므로
> 핸드오프(계획) + 실제 머지 코드 기준으로 작성한다.

## 0. 현재 상태 (조사 결과)

| 갭 | 구현 | 자동 테스트(현재) | 실환경 검증 |
|----|------|------------------|------------|
| **G1** NOTICE html | `obligation_service.generate_notice` (text/md/html) | unit + integration(text/md/html/download) ✅ | UI 다운로드 미검증 |
| **G2** 취약점 PDF | `report_service` weasyprint, `GET /v1/projects/{id}/vulnerability-report.pdf` | unit(HTML빌더/XSS) ✅ / integration PDF는 **skip 중** ⚠️ | **미검증** (weasyprint 미설치 이미지) |
| **G3** 소스 트리 | preserve + `source_tree` API + `SourceTab/Tree/Viewer` | unit + integration(실 tarball 읽기) ✅ | 실 스캔 보존·정리·UI 미검증 |
| **G4** cdxgen 생태계 | `_prepare_yarn/_poetry/...` + JDK worker | unit + cdxgen mock ✅ | **mock만** — fixtures 실스캔 미검증 |

- 갭 영역 단위 테스트 **229개 green** (2026-05-22 확인).
- dev 스택 가동 중. 핸드오프 §0.5 병행 제약은 배치 세션 종료(PR 머지)로 **해제**.
- **CI 비활성화 job** (전부 `if: false`, "before deploy" 재활성화):
  `e2e`(Playwright), `image-scan`(Trivy worker), `frontend-bundle-audit`,
  SAST(bandit/semgrep), visual-regression, sca-self nightly.
- **누락 산출물**: `2026-05-22-fixtures-scan-results.md` baseline 문서 부재 →
  G4는 "0 → ≥1 추출" 회귀 게이트로 검증.

## 1. P0 — 실환경 통합 검증

### 1.1 backend/worker 이미지 재빌드 → PDF 실렌더
- 실행 중 backend 이미지에 weasyprint 미설치(`ModuleNotFoundError`) → PDF 미검증.
- 재빌드 후 `tests/integration/test_reports_api.py`의 `_require_weasyprint` skip 해제 →
  실제 `%PDF` 매직바이트 + `Content-Disposition` 검증.
- 수동: 스캔된 프로젝트에서 PDF 다운로드 → 리스크/컴포넌트/CVE/라이선스 섹션 확인.

### 1.2 G4 cdxgen 생태계 — fixtures 실스캔
- 회귀 대상: `node-yarn`, `gradle*`, `python-poetry` (직전 0건/저조).
- 게이트: 각 ≥1 컴포넌트 추출. sanity: `node`/`maven` 회귀 없음.
- 제약: Colima 12GiB + DT 4GB → 순차, breaker 모니터링.

### 1.3 G3 소스 보존 end-to-end
- 실 스캔 → `{workspace}/scan-sources/{pid}/{scan_id}.tar.gz` + `ScanArtifact(kind='source_tarball')`.
- `scan_source_cleaner` (6h beat, latest 보존) quota/retention 동작.
- 경계: `scan_source_max_tarball_bytes`(512MiB) / `project_quota_bytes`(1GiB) 초과 graceful skip.

## 2. P1 — Frontend E2E (신규 UI 3종)
- 하네스(`PortalPage.ts`) verb 추가: `selectSourceTab`, `expandSourceTreeNode`,
  `openSourceFile`, `downloadVulnReportPdf`, `downloadNotice(format=html)`.
- 신규 spec: `source_tree.spec.ts`, `vulnerability_report.spec.ts`, `obligations.spec.ts`(html 확장).
- 회귀: `scan_flow.spec.ts` upload/folder method 경로 보강.

## 3. P1 — 커버리지 & CI 게이트
- 신규 파일 라인 커버리지 ≥80% 확인.
- CI 재활성화(deploy 전): e2e/image-scan/SAST/frontend-bundle-audit 복원,
  Trivy build-gate `exit-code "1"` 복원. **일괄 켜기 전 사용자 승인**.

## 4. P2 — 보안·엣지
- source_tree: scan_id 교차 IDOR, page/size 경계, UTF-8 디코드 실패.
- PDF/NOTICE: 악성 컴포넌트명/라이선스 표현식 적대적 입력 parametrize.
- 외부입력·파일서빙 신규 코드 security-reviewer 통과 후 머지.

## 실행 순서 & 결과
1. P0.1 → 2. P0.2/P0.3 → 3. P1 specs → 4. 커버리지/CI → 5. P2 reviewer

> 실행 결과는 본 문서 하단 "## 실행 로그"에 누적 기록한다.

## 실행 로그 (2026-05-22)

### P0.1 — PDF 실렌더 ✅ PASS
- backend 이미지 재빌드 → weasyprint 62.3 설치 확인.
- `tests/integration/test_reports_api.py` 5개 PASS (이전 skip되던 `test_report_happy_path_returns_pdf`가 실제 렌더, no-skip).
- 라이브 HTTP: `GET /v1/projects/{id}/vulnerability-report.pdf` → 200, `application/pdf`,
  `Content-Disposition` UTF-8 파일명, magic `%PDF-`, 유효 PDF v1.7 (10,984 B).
- 부수: auth route는 `/auth/login`(no /v1 prefix). e2e-admin 비번 해시 재설정함.

### P0.2 — G4 cdxgen 생태계 ✅ PASS (DT 없이 prep+cdxgen 직접 호출)
| 생태계 | components | 비고 |
|--------|-----------|------|
| node-yarn | **3** | 회귀 fix (이전 0) |
| python-poetry | **1** | 회귀 fix (이전 0) |
| gradle / gradle-kts / gradle-no-wrapper | **7 / 7 / 7** | JDK21 + gradle8 compat (guava+transitive 6) |
| node / maven / python-pip | 1 / 8 / 5 | sanity |
| go / rust / ruby | 1 / 8 / 5 | prep(go mod tidy/cargo/bundle) 실행 후 |
| gradle-with-wrapper | 0 | fixture가 빈 build.gradle (버그 아님) |
- transitive 검출 확인(§2.5): gradle 7 = direct 1 + transitive 6, maven 8, rust 8.
- worker(celery-worker)에 cdxgen+node20+JDK21(javac)+scancode+`BACKEND=real` 갖춤.

### P0.3 — G3 소스 보존/트리 ✅ PASS (보존은 파이프라인 함수 직접 호출로 검증)
- 실 스캔(mixed-policy zip 업로드)은 **dt_upload(70%)에서 실패 — DT 401(DT_API_KEY 무효, 환경)**.
- **설계 관찰**: 보존(Stage 6.5)이 DT 업로드(Stage 5) **이후**에 위치하고 happy-path에서만 호출됨
  (코드 주석: "실패 스캔은 tarball 미생성, 성공 스캔만 보존"). → DT 장애 시 cdxgen/scancode가
  성공해도 소스 트리 미보존. 의도된 설계지만, breaker 철학과의 긴장은 백로그 검토 가치.
- 보존 검증: `_preserve_source_tree` 직접 호출 → tarball `scan-sources/{pid}/{sid}.tar.gz`(4,997 B,
  files=7, scancode_json 폴딩) + ScanArtifact 기록. backend↔worker **공유 볼륨** 통해 backend가 읽음.
- source-tree API(scan_id 명시): root + `src/` 파일별 라이선스 — index.js→Apache-2.0,
  charts.js→MPL-2.0(조건부), legacy.c→**GPL-3.0-or-later(금지)**, LICENSE/package.json→MIT.
- source-file API: content + `license_matches`(spdx_id/start_line/end_line/score). legacy.c → GPL-3.0-or-later, 4–12행, score 100.
- vendor/foo/LICENSE: 트리에 보이되 finding `[]` (first-party 제외 end-to-end 확인).

### scancode 검출 fixture 추가 ✅ (사용자 요청, bd-scan/tests/fixtures/projects)
4개 프로젝트, scancode 32.4.0 실검증:
- `scancode-spdx-tags` (6 src): MIT/Apache-2.0/BSD-3-Clause/GPL-3.0-or-later/LGPL-2.1-or-later/`MIT OR Apache-2.0`.
- `scancode-license-headers` (4 src): Apache-2.0/MIT/BSD-3-Clause/GPL-3.0(forbidden) + copyright.
- `scancode-license-files`: LICENSE=MIT, COPYING=GPL-3.0-or-later, NOTICE=Apache-2.0; vendor/=제외(음성).
- `scancode-mixed-policy`: 허용(MIT/Apache)·조건부(MPL-2.0)·금지(GPL-3.0) + vendor 제외(음성). G3 E2E 대상 겸용.
- 발견: `vendor/` 제외(EXCLUDED_DIR_NAMES), `spdx_id` 64자 한도 → 복합표현 drop(검증 후 fixture 정정).

### 환경 이슈 (후속)
- **DT_API_KEY 401** — DT(dtrack-api) admin 로그인 실패. 제공된 비번(`admin`/`Elvps1193!`, username `admin`/`administrator`)
  모두 INVALID_CREDENTIALS. 반복 시도로 lockout 가능성. 키 재발급 불가 → 풀 스캔 happy-path 차단(보존은 함수 직접호출로 검증 완료).
- CI: e2e/image-scan/frontend-bundle-audit/SAST/visual-regression 비활성(`if:false`).
- baseline `2026-05-22-fixtures-scan-results.md`는 **#106에 존재**(처음엔 로컬 stale로 미발견). complete handoff는 PR #110.

### P1 — 커버리지 ✅ (신규 모듈 전부 ≥80%)
대상 테스트 252개 PASS. 신규 모듈 라인 커버리지:
| 모듈 | Cover |
|------|-------|
| api/v1/source_tree.py | 100% |
| services/report_service.py | 97% |
| services/source_tree_service.py | 95% |
| tasks/scan_source_cleaner.py | 95% |
| api/v1/obligations.py / api/v1/reports.py | 94% / 94% |
| services/obligation_service.py | 94% |
| services/source_preservation_service.py | 91% |
| tasks/scan_source.py (파일 전체) | 74% — 미커버는 기존 git fetch/DT poll/container 분기. 신규 prep·보존은 커버 |

- **갭 관찰**: in-task 보존(Stage 6.5) 호출을 자동 단언하는 테스트 없음(파이프라인 mock이 tarball 생성을 assert 안 함). → follow-up: pipeline mock에 source_tarball ScanArtifact 단언 추가 권고.

### P1 — CI 게이트 재활성화 계획 (deploy 전, 일괄 켜기 전 승인)
현재 `if: false`로 비활성: `ci.yml`의 `image-scan`/`frontend-bundle-audit`/`e2e`,
`sast.yml`(bandit/semgrep), `visual-regression.yml`, `sca-self.yml`(nightly).
재활성 순서 권고: (1) 신규 e2e spec 머지 후 `e2e` 복원, (2) SAST/`frontend-bundle-audit` 복원,
(3) `image-scan`(Trivy) + ci.yml build-gate `exit-code "1"` 복원(CVE는 `.trivyignore`/dep bump 선조치).

### P1 — 프론트엔드 E2E spec ✅ (작성+typecheck, 전체 실행은 seed 대기)
- 하네스(`PortalPage.ts`) verb 추가: source-tree(`selectSourceTab`/`expandSourceTreeNode`/`openSourceFile`/
  `expectSourceLineLicense` 등), `downloadVulnReportPdf`, `downloadNotice(format)` 확장, `startScanByUpload`.
- 신규 spec: `source_tree.spec.ts`(4), `vulnerability_report.spec.ts`(2), `obligations.spec.ts`(html 추가),
  `scan_flow.spec.ts`(upload method 추가). 총 16 테스트 parse, `tsc`/eslint clean. 제품 버그 없음. test-id 추가 0.
- **hand-off (seed 갭)**: e2e seed가 per-scan **preserved-source tarball**을 안 만들어서 source-tree S3/S4는
  `--with-source` seed 플래그 추가 전까지 런타임 auto-skip(빈 상태만 검증). seed에 nested dir + utf8(MIT
  매치) + binary + oversized 파일 staging 필요. → scan-pipeline-specialist/test-writer.

### P2 — 보안 리뷰 ✅ (Medium 2 / Low 3 — ⚠️ stale 코드 리뷰였음)
security-reviewer 판정 CHANGES REQUESTED (Critical 0 / High 0). 핵심 방어(traversal·zip-slip 양방향·
IDOR·PDF XSS·subprocess argv/env scrub·Content-Disposition·asyncpg)는 견고+테스트됨으로 확인.

> **중요 정정**: 리뷰가 실행된 로컬 main이 origin/main보다 **4커밋 뒤진 stale 상태(#105)**였다.
> 발견된 Medium 2건은 origin/main에서 **이미 수정 완료**임이 머지 후 확인됨:
> - **Medium #1** markdown/text NOTICE XSS → **#107**(`_md_escape` 전 필드 이스케이프 + label 버킷화)에서 수정됨.
> - **Medium #2** NOTICE 본문 총량 캡(DoS) → **#109**(license/obligation/component 캡 + raw download 스트리밍)에서 수정됨.
> 따라서 이 세션에서 시도했던 동일 수정은 **중복이라 폐기**(origin/main 채택). security-reviewer는
> 향후 **HEAD 동기화 후 호출**해야 한다(stale 리뷰 방지).

**잔여 후속 권고(origin/main 기준 미수정):**
- **Low #3** `report_service._safe_href`가 http(s) URL 내부 CR/LF 등 제어문자 허용(현재 비익스플로잇,
  PDF는 `base_url=None`). origin/main에 미반영 → 제어문자 거부 한 줄 + 테스트 권고(이 PR 범위 외, follow-up).
- **Low #4** cross-team posture 불일치(list/notice 403 vs detail/report/source-tree 404) — 정책 결정
  (404 existence-hide 통일 권고, 또는 CLAUDE.md에 의도 명시).
- **Low #5** inline HTML NOTICE에 CSP 부재(이스케이프는 견고, 방어심층). route CSP 또는 전역 CSP.
- **Info** upload-source GA 시 prep 리졸버(go/cargo/dotnet/bundle)가 공격자 매니페스트 실행 → egress 제한 샌드박스.
