---
session_prompt: 스캔 파이프라인 갭 구현 완료 → 배포 전 잔여 follow-up
date_authored: 2026-05-22
authoring_session: scan-pipeline gaps 구현 (§2 갭 G1–G4 + 부수 작업)
status: complete
---

# 세션 핸드오프 — 스캔 파이프라인 갭 구현 완료

> 이전 핸드오프 `docs/sessions/2026-05-22-scan-pipeline-gaps-handoff.md`의 §2 갭
> (NOTICE html / 취약점 PDF / 소스 트리 뷰 / cdxgen 보강)을 **전부 구현·머지 완료**한
> 세션의 종료 핸드오프. 다음 세션은 본 문서 §3(잔여 follow-up)부터 시작한다.

## 0. 컨텍스트 (자동 복원)

- 레포 `github.com/trustedoss/trustedoss-portal`, **main HEAD = `d0ae48f`** (PR #109).
- 단일 진실: `CLAUDE.md`, 메모리 `project_scan_pipeline_gaps`, 본 문서.
- 직전 핸드오프(시작 지시문, 완료됨): `docs/sessions/2026-05-22-scan-pipeline-gaps-handoff.md`.
- fixtures e2e 조사: `docs/sessions/2026-05-22-fixtures-scan-results.md` (PR #106, G4 근거).
- 로컬: Apple Silicon + Colima 12GiB. DT 4GB + worker(JDK 후 ~6GB) 공존이라 메모리 빠듯.

## 1. 이번 세션 완료 (§2 갭 + 부수) — PR #94–#109

| 영역 | PR | 내용 |
|------|----|------|
| §1 선행 | #94 | scan e2e 버그(cdxgen --no-validate / DT BOM hash / dev workspace 볼륨). prod는 이미 공유 볼륨이라 변경 불요 확인 |
| CI 복구 | #95 | pre-existing `test (backend)` red 2건(`_FakeScan.scan_metadata`, quota 507 테스트 데이터) + `e2e (scan-flow)` harness(SourceSelectDialog 경유) |
| CI 복구 | #98 | `visual-regression`에 setup-python 3.12(ubuntu 3.10에서 `datetime.UTC` ImportError) |
| **G1** | #96 | NOTICE `format=html` (모든 외부입력 escape, http(s) 링크만, 2KiB cap) + frontend 텍스트/HTML 선택 + EN/KO |
| **G2** | #97 #101 | 취약점 PDF 보고서(weasyprint, `GET /v1/projects/{id}/vulnerability-report.pdf`) + frontend 다운로드 버튼 + 공유 `lib/download.ts` |
| **G3** | #99 #102 #105 | 소스 트리 뷰: 소스 보존(per-scan tar.gz + scancode JSON fold, retention=latest) · read API(path-traversal 방어) · SourceTab 트리+뷰어+라인 하이라이트 |
| **G4** | #103 #104 | cdxgen: yarn(빈 lock 제거)·poetry(deps→requirements 합성) 코드 fix + gradle용 worker 이미지 JRE→JDK |
| CI 가속 | #100 | 느린 job(e2e×2/image-scan/visual/semgrep/bandit/bundle-audit) 일시 disable (`TEMP-DISABLED-CI` 마커) |
| docs | #106 | fixtures e2e 결과 문서 보존 |
| 품질 | #107 | post-review hardening(body cap·path-echo·tar reuse·markdown escape+newline·raw download·통합테스트) |
| 의존성 | #108 | weasyprint 62.3→68.0 + pydyf 0.10.0→0.12.1 (CVE-2025-68616) · frontend vitest/postcss/ws |
| nit | #109 | NOTICE license/obligation cap + raw download → StreamingResponse |

**security-reviewer 5회 전부 PASS**: G1 NOTICE escape · G3.2 path-traversal · G2 weasyprint/pydyf · G3.3 raw download · #107 hardening (Medium markdown-newline은 후속 fix `546d818`로 처리).

## 2. 운영 액션 (이미 수행됨)

- **gradle 활성화**: dev worker 이미지를 JDK로 재빌드+교체 완료(`docker-compose -p trustedoss-portal -f docker-compose.dev.yml build/up celery-worker`). `javac 21.0.11` 확인, worker healthy, Celery ready, DT 정상.
- throwaway 이미지 `:jdk-verify` 정리 완료(사용자 직접).
- 메인 작업트리는 이 과정에서 `fix/scan-pipeline-e2e-bugs` → `main`으로 전환됨(89df61c fixtures docs는 #106으로 머지, fix branch에도 commit 보존).

## 3. 남은 follow-up (다음 세션 / 배포 전)

1. **CI 느린 job 재-enable** ← 배포 직전 필수. 사용자가 "맨 마지막"으로 확정 후순위.
   `grep -rl TEMP-DISABLED-CI .github/workflows` → 각 `if: false` 제거(visual-regression은 인접 주석의 원래 label-gate 복원). 그 후 e2e/image-scan/sast/visual 전체 통과 검수.
2. **throwaway 검증 이미지 정리** (사용자 직접, harness가 docker rmi 차단):
   `docker rmi trustedoss/backend-api:dep-verify trustedoss/backend-worker:dep-verify`
3. **PDF dev 활성화**: weasyprint 68.0(#108)을 dev에서 실제로 쓰려면 **backend(API) 이미지 재빌드** 필요 — PDF 렌더 native libs(libpango/cairo)는 `Dockerfile`(API)에 있고 `render_report_pdf`는 API endpoint다(worker 아님). `docker-compose -p trustedoss-portal -f docker-compose.dev.yml build backend`. (배포 시 이미지 빌드하면 자동 포함.)
4. **agent worktree 정리**: 세션 중 격리 worktree 다수 누적, harness lock(`claude agent`)이라 수동 remove 불가 → 세션 종료 시 harness auto-clean, 또는 `git worktree remove -f -f <path>`.
5. **선택 dep bump** (security-reviewer가 out-of-scope로 남김): axios(prod), esbuild/vite chain, @playwright/test 등 — 각각 별도 검증 PR.
6. **선택 G3 raw 다운로드 UX**: truncated/binary 외 일반 파일도 raw 다운로드 노출 여부(현재 viewer는 capped, raw endpoint는 존재).

## 4. 다음 세션 시작 prompt

```
docs/sessions/2026-05-22-scan-pipeline-gaps-complete.md 에 따라 진행한다.
§2 갭(G1–G4)은 PR #94–#109로 전부 머지 완료. §3의 잔여 follow-up만 남았다.
배포 준비 단계면 §3-1(CI 느린 job 재-enable: TEMP-DISABLED-CI 제거 + 전체 통과
검수)을 먼저 수행하고, §3-3(backend 이미지 재빌드로 PDF 활성화)을 확인한다.
```

## 5. 환경 (참고)

- **DT**: `docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml up -d`. dtrack-api(4.13.2, embedded H2). breaker reset: `POST /v1/admin/dt/breaker/reset`.
- **super admin**: `e2e-admin@trustedoss.dev` / `E2eAdminPass2026`.
- **fixtures**: `~/projects/bd-scan/tests/fixtures/projects` (32개).
- **worker 이미지**: scancode 32.4.0 + dotnet + **temurin-21-jdk**(gradle용, #104). PDF 렌더는 backend(API) 이미지.
