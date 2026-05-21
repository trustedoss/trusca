---
session_prompt: 스캔 파이프라인 갭 구현 (NOTICE html / 취약점 PDF / 소스 트리 뷰 / cdxgen 생태계 보강)
date_authored: 2026-05-22
authoring_session: bd-scan fixtures e2e (zip-upload full scans, DT 구성)
status: ready
---

# 다음 세션 시작 지시문 — 스캔 파이프라인 갭 구현

> bd-scan fixtures e2e(zip 업로드 풀 스캔, DT 구성) 세션에서 사용자가 요구한
> 제품 기능 갭들을 새 세션에서 구현하기 위한 지시문. 첫 메시지로 본 파일 경로를
> 인용하면 컨텍스트가 자동 복원된다.

## 0. 컨텍스트 (자동 복원)

- 레포 `github.com/trustedoss/trustedoss-portal`, main HEAD ≈ `faba275` 이후.
- 단일 진실: `CLAUDE.md`, 메모리 `project_scan_hardening_prs`, 본 문서.
- 직전 세션 머지: PR #85–93 (scancode/안정성/동시성/zip 업로드/UI), #92 (Trivy build-gate 임시 off).
- 직전 세션 산출 PR (머지 대기): **#94 (`fix/scan-pipeline-e2e-bugs`)** — fixtures e2e가 발견한 실제 버그 3건.
- fixtures e2e 조사 결과: `docs/sessions/2026-05-22-fixtures-scan-results.md` (별도 배치 세션 산출).

## 0.5 ⚠️ 병행 실행 제약 (배치 세션과 동시 진행 시)

이 구현 세션은 fixtures e2e **배치 조사 세션과 병행**될 수 있다(시간 절약 목적).
같은 로컬 환경을 공유하므로 아래를 반드시 지킨다:

1. **git worktree 필수** — `claude --worktree`(또는 EnterWorktree)로 별도
   작업트리+브랜치에서 작업한다. 같은 작업트리를 공유하면 브랜치 전환·파일
   수정이 배치 세션과 충돌한다.

2. **dev 스택(backend/worker/DT/Postgres/Redis)은 배치 세션이 점유 중** —
   배치가 끝나기 전엔 다음을 **하지 않는다**:
   - worker/backend **이미지 재빌드**(PDF weasyprint, 트리뷰 시스템 의존성),
   - **컨테이너 재시작**(`docker-compose restart/up/down`),
   - **DB 마이그레이션**(`alembic upgrade`),
   - DT / breaker 조작.
   하나라도 하면 진행 중인 배치 스캔이 즉시 깨진다. (Colima 12GiB로는 DT 4GB
   때문에 별도 스택을 따로 띄울 수도 없다.)

3. **배치 중 가능한 작업** — 코드 작성 + **단위/mock 테스트**(dev 스택 불요)까지.
   NOTICE html(§2.1)·PDF 생성 코드(§2.2)·트리뷰 backend/frontend 코드는 작성
   가능하다. **단** PDF 의존성 worker 이미지 빌드·트리뷰 Alembic 마이그레이션·
   통합/E2E 검증은 **배치 완료 후**로 미룬다.

4. **배치 완료 확인** — `/tmp/fixture_results.txt`에 `=== BATCH DONE ===`가
   찍히거나 배치 세션이 보고하면 완료. 그 후 통합 검증(이미지 빌드/마이그레이션/
   스택 재시작)을 진행한다.

## 1. 선행 — PR 머지 + prod 확인

- **PR #94 머지**: cdxgen `--no-validate`(schema 실패 시 SBOM 드롭 버그) + DT BOM hash sanitize(cdxgen base64 hash를 DT가 400 거부) + `docker-compose.dev.yml` backend↔worker `scan-workspace` 공유 볼륨(zip 업로드 archive 공유).
- **prod 확인**: `docker-compose.yml`(prod)에도 backend↔worker workspace 공유 볼륨이 필요한지 점검(zip 업로드가 prod에서 동작하려면). dev에만 추가됨.

## 2. 구현 대상 갭 (사용자 요구)

### 2.1 NOTICE 고지문 html 출력
- 현재 `GET /v1/projects/{id}/notice?format=text|markdown` 만. **html 미지원**(422).
- `services/obligation_service.py` `generate_notice` + `api/v1/obligations.py`(또는 notice 라우터)에 `format=html` 추가. `download` 옵션(Content-Disposition) 이미 존재.
- 사용자 요구: html, txt 둘 다.

### 2.2 취약점 PDF 보고서
- **미구현** (report/pdf 엔드포인트 없음).
- 신규 `GET /v1/projects/{id}/vulnerability-report.pdf` (또는 `/report?format=pdf`). PDF 생성 라이브러리(weasyprint 권장: HTML→PDF, 또는 reportlab) — requirements 추가 + worker/backend 이미지에 시스템 의존성.
- 내용: 프로젝트 리스크 요약 + 컴포넌트 + 취약점(severity별, CVE/CVSS) + 라이선스 분포. 기존 project_detail/vulnerability 서비스 재사용.
- frontend: 다운로드 버튼(프로젝트 상세).

### 2.3 소스 코드 파일 트리 뷰 (Protex식)
- **미구현**. 현재 UI는 컴포넌트(의존성 패키지) 중심(Overview/Components/Vulnerabilities/Licenses/Obligations 탭). 파일 탐색기·파일별 라이선스 매치 없음.
- backend: scancode `source_path` 데이터(현재 `license_findings.kind='detected'` + `source_path`) 기반 파일 트리 API. **설계 주의**: 현재 source는 스캔 후 workspace `rmtree`로 삭제됨 → 트리 뷰가 파일 경로/내용을 보여주려면 (a) scancode 결과(파일별 라이선스)만으로 트리 구성, 또는 (b) 소스 파일 보존 정책(스토리지/비용) 결정 필요.
- frontend: 트리 컴포넌트(shadcn) + 파일 선택 시 detected 라이선스/매치 패널(드로어).

### 2.4 cdxgen 생태계별 검출 보강
- fixtures 조사에서 **node-yarn=0**(yarn.lock인데 cdxgen 0 추출) 등 생태계별 차이 발견.
- `docs/sessions/2026-05-22-fixtures-scan-results.md`의 32개 생태계별 컴포넌트/취약점 표로 0건/저조 생태계 파악 → `tasks/scan_source.py` `_prepare_for_cdxgen`(prep) + cdxgen 옵션 보강(yarn/gradle/maven 등).

### 2.5 Transitive dependency
- cdxgen이 lockfile 기반으로 direct + transitive 검출(설계됨). node(lodash)=1은 lodash가 transitive 없는 라이브러리라서. **생태계별 transitive 검출률은 §2.4 조사 결과로 검증** — 둘은 같은 작업.

## 3. 미해결 follow-up (이전 세션)

- **Trivy build-gate 재활성화**: launch 전 `.github/workflows/ci.yml` `exit-code "0"`→`"1"`. CVE는 `.trivyignore`/dep bump로 조치 후. (사용자 의도 비활성화 — 함부로 켜지 말 것.)
- **test-writer**: `tests/_harness/PortalPage.ts` `clickTriggerScan`이 이제 `SourceSelectDialog` 경유 → harness verb + `scan_flow.spec.ts`/`capture_user_guide.spec.ts` 갱신. `test_scan_timeout.py`의 `_FakeScan`에 `scan_metadata` 추가.
- **DB pool 사이징**: 단일 호스트는 `.env`에서 `DB_POOL_SIZE` 10+5 (default 20+10 × uvicorn 4 = 120 > Postgres 100).

## 4. 환경 (이 작업에 필요)

- **DT**: `docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml up -d`. dtrack-api(4.13.2, embedded H2). 잔재 볼륨에 NVD 26만 CVE 보유(최신 NVD 미러는 1.1 feed 종료로 실패하지만 기존 데이터로 매칭 가능). `.env`의 `DT_API_KEY` 유효. breaker reset: `POST /v1/admin/dt/breaker/reset`.
- **포털 super admin**: `e2e-admin@trustedoss.dev` / `E2eAdminPass2026` (`scripts/create_super_admin.py`로 생성. 데모 admin 비번은 시드 시 랜덤이라 모름).
- **fixtures**: `~/projects/bd-scan/tests/fixtures/projects` (32개, Protex/Black Duck 비교용).
- **로컬**: Apple Silicon + Colima 12GiB. worker 이미지에 scancode 32.4.0 + dotnet(dotnet-install.sh).

## 5. 시작 prompt (새 세션 첫 메시지)

```
docs/sessions/2026-05-22-scan-pipeline-gaps-handoff.md 에 따라 진행한다.
배치 조사 세션과 병행 중이면 §0.5를 반드시 준수한다 — git worktree로 격리,
dev 스택(이미지 빌드/컨테이너 재시작/DB 마이그레이션) 비점유. 배치 완료 전엔
코드 작성 + 단위/mock 테스트까지만 하고, 통합 검증은 배치 후로 미룬다.
PR #94 머지 후, §2 갭을 구현한다 — NOTICE html, 취약점 PDF 보고서,
소스 코드 파일 트리 뷰(Protex식), cdxgen 생태계별 검출 보강.
핵심 보안/외부입력 코드는 security-reviewer 통과 후 PR.
```
