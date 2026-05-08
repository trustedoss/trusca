# TrustedOSS Portal v2 — 자율 실행 계획서

> **목적**: Claude Opus 4.7이 세션 중단 없이, 혹은 세션이 종료·재시작되더라도 이 파일 하나만 읽고 현재 위치를 파악해 다음 작업을 이어갈 수 있도록 설계된 단일 진실 상태 머신.
>
> **사용법**: 새 세션 시작 시 이 파일을 **먼저** 읽고 `## 세션 시작 루틴`을 실행한다. 절대로 기억이나 추측에 의존하지 않는다.
>
> **기반 commit**: `688bfed` (Phase 4 PR #14 머지, 2026-05-08)
>
> **작성일**: 2026-05-08

---

## 세션 시작 루틴 (매 세션 반드시 실행)

```bash
# 1. 현재 commit이 계획 기반과 일치하는지 확인
git log --oneline -1

# 2. 이 파일을 읽어 현재 단계 파악
#    - [x] DONE: 완료됨 (git log로 재확인)
#    - [~] IN_PROGRESS: 이전 세션이 중단됨 → 아래 "재개 검증 명령" 실행
#    - [ ] TODO: 아직 미착수 → 해당 단계 "구현 프로토콜" 실행

# 3. 현재 단계의 "재개 검증 명령" 실행 → 실제 상태와 파일 상태가 일치하는지 교차 확인

# 4. 환경 건강 확인
docker-compose -f docker-compose.dev.yml ps       # 6/6 healthy
gh run list --limit 3                              # CI 최근 상태
```

> **상태 파일 업데이트 규칙**: 단계를 시작할 때 `[ ]` → `[~]`로, 완료(PR 머지)되면 `[~]` → `[x]`로 변경하고 즉시 커밋한다. 이것이 세션 연속성의 핵심이다.

---

## 전체 단계 현황

| 단계 | 작업 | 상태 | PR |
|------|------|------|----|
| Step 1 | 공통 앱 레이아웃 + New Project 폼 | [x] DONE | #16 (merged c5ed441) |
| Step 2 | chore PR #9 — Admin 보안 follow-up | [x] DONE | #17 (merged 9554d40) |
| Step 3 | Phase 4 PR #15 — 컴포넌트 승인 워크플로우 | [x] DONE | #18 (merged 7699d52) |
| Step 4 | Phase 3 미완 — SBOM·Settings·/scans | [x] DONE | #19 (merged 2dc547f) |
| Step 5 | Phase 5 PR #16 — API Key + Webhook (backend only) | [x] DONE | #20 (merged c0cf4c1) |
| Step 6 | Phase 5 PR #17 — 빌드 게이트 + PR 코멘트 + GitHub Actions | [x] DONE | #21 (merged 2f7c5d7) |
| Step 7 | Phase 6 PR #18 — i18n + 알림 시스템 | [~] IN_PROGRESS | — |
| Step 8 | Phase 6 PR #19 — 안정성 + 백업 | [ ] TODO | — |
| Step 9 | Phase 7 PR #20 — 설치 스크립트 + prod compose | [ ] TODO | — |
| Step 10 | Phase 7 PR #21 — Docusaurus + 가이드 | [ ] TODO | — |
| Step 11 | Phase 8 PR #23 — OAuth + Demo SaaS | [ ] TODO | — |
| Step 12 | Phase 8 PR #24/#25 — 보안·성능·릴리스 | [ ] TODO | — |

---

## 자율 실행 프로토콜 (모든 단계 공통)

### 구현 → 테스트 → 보완 → 재구현 루프

```
LOOP:
  1. 구현 (에이전트 배치 또는 직접 구현)
  2. 로컬 테스트 실행:
       cd apps/backend && python -m pytest tests/ -x -q 2>&1 | tail -20
       (프론트 있으면) cd apps/frontend && npx tsc --noEmit 2>&1 | tail -10
  3. DoD 체크리스트 교차 확인
  4. 미흡점이 있으면:
       a. 미흡점을 구체적으로 기록 (어느 파일 몇 번째 테스트)
       b. 보완 구현
       c. 2로 돌아감
  5. DoD 모두 통과 → PR 생성 → CI 확인 → 머지
  6. 이 파일의 해당 단계 상태 [~] → [x] 로 업데이트 + 커밋
  7. 다음 단계로 이동
  
  최대 반복: 3회. 3회 후에도 DoD 미통과 시 → BLOCKED 표시 후 세션 종료.
  BLOCKED 재개: 다음 세션에서 이 파일 확인 후 미흡점 분석부터 재시작.
```

### CI 확인 명령

```bash
gh run list --limit 1 --json status,conclusion,headSha \
  | python3 -c "import sys,json; r=json.load(sys.stdin)[0]; print(r['status'], r['conclusion'])"
```

### PR 생성 명령 패턴

```bash
gh pr create \
  --title "$(title)" \
  --body "$(cat <<'EOF'
## Summary
- 항목1
- 항목2

## Test plan
- [ ] pytest green
- [ ] 기능 확인

🤖 Generated with Claude Code
EOF
)"
```

---

## Step 1: 공통 앱 레이아웃 + New Project 폼

**상태**: `[x] DONE` — PR #16 머지 (commit c5ed441, 2026-05-08).  
**브랜치**: `feature/step1-app-layout-new-project`  
**예상 PR**: GitHub PR #16  
**에이전트**: `frontend-dev` (주), `test-writer` (보조)  
**예상 소요**: 1 세션

### 재개 검증 명령

```bash
# 브랜치 존재 여부
git branch -r | grep step1

# 핵심 파일 존재 여부
ls apps/frontend/src/components/AppShell.tsx 2>/dev/null && echo "EXISTS" || echo "MISSING"
ls apps/frontend/src/features/projects/ProjectCreatePage.tsx 2>/dev/null && echo "EXISTS" || echo "MISSING"
```

### 배경 및 목적

현재 문제:
- `Home.tsx` (`/`): 의미없는 카드 플레이스홀더. `/projects` 링크조차 없음
- `ProjectListPage.tsx`: 독립 헤더 — 사이드바·다른 페이지 링크 없음
- `ProjectDetailPage.tsx`: 독립 헤더 — 목록으로만 돌아갈 수 있음
- 어디에도 `/scans`, `/approvals`, `/integrations` 등으로 가는 링크 없음
- New Project: API 함수·백엔드 엔드포인트 모두 존재, UI만 없음

### 구현 대상

#### 1-A. AppShell 컴포넌트

파일: `apps/frontend/src/components/AppShell.tsx`

```
레이아웃 구조:
┌─────────────────────────────────────────┐
│ Header (48px)  [앱명]    [언어토글] [사용자] │
├──────────┬──────────────────────────────┤
│          │                              │
│ Sidebar  │   <children />               │
│ (224px)  │                              │
│          │                              │
└──────────┴──────────────────────────────┘

사이드바 메뉴 항목:
- Projects  (/projects)
- Scans     (/scans)        ← 이 단계에서는 링크만, 페이지는 Step 4에서
- Approvals (/approvals)    ← 링크만, 페이지는 Step 3에서
- [Admin 섹션, is_superuser만 표시]
  - Users, Teams, DT, Scans, Disk, Audit, Health
```

- `RequireAuth`로 감싸진 모든 라우트가 AppShell 안으로 들어가야 함
- Admin 섹션은 `is_superuser=true` 일 때만 표시 (존재 숨김 불필요 — 사이드바 표시/미표시)
- 현재 경로에 맞는 메뉴 항목 active 스타일 적용
- 로그아웃 버튼: 헤더 우측 사용자 메뉴 드롭다운

#### 1-B. `/` 리다이렉트

`Home.tsx` 역할 변경: `/projects`로 즉시 리다이렉트.  
실제 전사 대시보드(리스크 포트폴리오)는 Phase 5 이후 구현. 지금은 리다이렉트가 가장 적절.

#### 1-C. New Project 폼

파일: `apps/frontend/src/features/projects/ProjectCreatePage.tsx`  
라우트: `/projects/new`

필드:
- Name (필수, max 100자)
- Description (선택, max 500자)
- Git URL (선택, URL 형식 검증)

동작:
- 제출 → `createProject()` API 호출 (`lib/projectsApi.ts`에 이미 존재)
- 성공 → `/projects/:newId` 로 리다이렉트
- 실패 → 인라인 에러 (RFC 7807 Problem 파싱)
- 사이드바 "Projects" 옆 `+` 버튼 또는 프로젝트 목록 헤더에 "New Project" 버튼

#### 1-D. AppShell을 router.tsx에 연결

```tsx
// router.tsx 변경 포인트
<Route path="/" element={<RequireAuth><AppShell /></RequireAuth>}>
  <Route index element={<Navigate to="/projects" replace />} />
  <Route path="projects" element={<ProjectListPage />} />
  <Route path="projects/new" element={<ProjectCreatePage />} />
  <Route path="projects/:id" element={<ProjectDetailPage />} />
  <Route path="scans" element={<ComingSoonPage label="Scans" />} />
  <Route path="approvals" element={<ComingSoonPage label="Approvals" />} />
  // admin은 별도 AdminLayout (기존 유지)
</Route>
```

`ComingSoonPage`: "준비 중" 메시지 단순 컴포넌트 — Step 3, 4에서 실제 페이지로 교체.

### DoD 체크리스트

- [ ] `AppShell` 마운트 시 사이드바 224px 고정, 헤더 48px 고정
- [ ] 로그인 후 `/` 접속 → `/projects` 자동 리다이렉트
- [ ] Projects 목록 → 사이드바 "Projects" active 스타일
- [ ] 프로젝트 상세 → 사이드바 "Projects" active 스타일 유지
- [ ] Admin 메뉴: `admin@trustedoss.dev`로 로그인 시 표시, `dev@trustedoss.dev`로 로그인 시 미표시
- [ ] New Project 버튼 클릭 → `/projects/new` 이동
- [ ] 폼 제출 → 프로젝트 생성 → 상세 페이지 이동
- [ ] 폼 유효성: 이름 필수 (빈칸 시 에러), Git URL 형식 검증
- [ ] `npx tsc --noEmit` 오류 없음
- [ ] 기존 E2E: `scan_flow.spec.ts`, `project_detail.spec.ts` 리그레션 없음

### 구현 프로토콜

```
1. frontend-dev 에이전트에 위임:
   - AppShell 컴포넌트 구현 (사이드바 + 헤더 + Outlet)
   - router.tsx 재구성
   - Home.tsx → Navigate 단순화
   - ProjectCreatePage 구현 (zod 유효성, createProject 호출)
   - i18n 키 추가 (common.json에 nav.*, projects.create.*)

2. 구현 완료 후 로컬 확인:
   cd apps/frontend && npx tsc --noEmit
   docker-compose -f docker-compose.dev.yml exec -T frontend npm test -- --run 2>&1 | tail -20

3. DoD 체크리스트 수동 확인 (UAT 계정으로 브라우저 테스트):
   - dev@trustedoss.dev 로그인 → 사이드바 보임
   - admin@trustedoss.dev 로그인 → Admin 섹션 보임
   - New Project 폼 동작 확인

4. PR 생성 + CI 확인 + 머지
5. 이 파일 Step 1 상태 [~] → [x] 업데이트 + 커밋
```

---

## Step 2: chore PR #9 — Admin 보안 follow-up

**상태**: `[x] DONE` — PR #17 머지 (commit 9554d40, 2026-05-08).  
**브랜치**: `feature/chore-pr9-admin-followups`  
**예상 PR**: GitHub PR #17  
**에이전트**: `backend-developer` (주)  
**예상 소요**: 0.5 세션 (Step 3과 병렬 또는 선행)

### 재개 검증 명령

```bash
git branch -r | grep chore-pr9
# 이미 브랜치 존재 시 → git checkout + git log 로 진행 상황 확인
```

### 구현 대상

메모리 `project_phase4_admin_followup_pr.md` 기준 12건:

**M1** — `core/errors.py: _redact_validation_errors`가 `msg`·`ctx` 필드도 sanitize (현재 `input`만)  
**M2** — `scripts/seed_e2e_user.py: _seed()` 내부에 APP_ENV 가드 추가 (defense-in-depth)  
**M3** — `api/v1/admin/teams.py: _problem_for_admin_team_error`에 PII echo 경고 주석 추가  
**L1** — `validation_error`/`invalid_role_assignment` Problem extension whitelist 정리 (미발행 키 drop)  
**L2** — `services/admin_team_service.py: _is_team_fk_violation` → `IntegrityError.orig.diag.constraint_name` 사용  
**G2** — `tasks/dt_orphan_cleanup_task`: lock release를 terminal 실패 시에만 (CWE-362)  
**G3** — `api/v1/admin/dt.py`: AuditLog insert에 `request_id`/`ip`/`user_agent` 채움  
**G4** — `services/admin_{disk,health,dt}_service.py`: error/last_error/detail 필드 connection-string credential strip  
**G5** — `api/v1/admin/audit.py`: ILIKE `q` 파라미터 `%`/`_` wildcard escape  
**G6** — `OrphanCleanupRequest.dt_project_uuids` 빈 리스트 wipe-all → 별도 endpoint 분리  
**G7** — `DiskPathUnavailable` dead code 제거  
**G10** — `dt_orphan_cleanup` lock TTL: 600s → 3600s (worst-case 500 uuid × 5s = 2500s)  

### DoD 체크리스트

- [ ] M1: `_redact_validation_errors` 테스트 — msg/ctx에 이메일 포함 시 마스킹 확인
- [ ] G4: connection-string 포함 exception 발생 시 응답에서 credential 미노출 확인
- [ ] G5: `q="%admin%"` 쿼리 → 정상 응답 (crash 없음)
- [ ] G6: `dt_project_uuids=[]` 요청 → 400 에러 반환
- [ ] pytest green (기존 admin 테스트 포함)
- [ ] security-reviewer 에이전트 PASS (PASS-with-conditions 이상)

### 구현 프로토콜

```
1. backend-developer 에이전트: M1, M2, M3, L1, L2, G2~G7, G10 순서로 구현
   (각 항목이 독립적이므로 파일별 순차 처리)

2. pytest 실행:
   cd apps/backend && python -m pytest tests/ -x -q 2>&1 | tail -30

3. security-reviewer 에이전트: 변경 파일 diff 리뷰
   - PASS 또는 PASS-with-conditions → PR 생성
   - FAIL → 지적 사항 수정 후 재검토

4. PR 생성 (Step 3보다 먼저 머지)
5. 이 파일 Step 2 상태 [x] 업데이트
```

---

## Step 3: Phase 4 PR #15 — 컴포넌트 승인 워크플로우

**상태**: `[x] DONE` — PR #18 머지 (commit 7699d52, 2026-05-08).  
**브랜치**: `feature/phase4-pr15-component-approval-workflow`  
**예상 PR**: GitHub PR #18  
**에이전트**: `backend-developer` + `frontend-dev` + `test-writer` (병렬)  
**선행 조건**: Step 2 머지 후 진행  
**예상 소요**: 1~1.5 세션

### 재개 검증 명령

```bash
git branch -r | grep phase4-pr15

# DB에 approvals 테이블 존재 여부
docker-compose -f docker-compose.dev.yml exec -T postgres \
  psql -U trustedoss -d trustedoss -t -c \
  "SELECT tablename FROM pg_tables WHERE tablename='component_approvals';" 2>/dev/null

# 백엔드 라우터 등록 여부
grep -r "approvals" apps/backend/api/v1/admin/__init__.py 2>/dev/null
```

### 구현 대상

`docs/sessions/_next-session-prompt-phase4-pr15-component-approval-workflow.md` (169줄) 를 단일 진실로 참조.

핵심 요약:

**백엔드**:
- `models/component_approval.py`: `ComponentApproval` 모델 (status: pending/under_review/approved/rejected)
- `alembic/versions/0008_component_approvals.py`
- `api/v1/admin/approvals.py`: CRUD + 상태 전이 엔드포인트
- `services/component_approval_service.py`: 비즈니스 로직
- 상태 전이: pending → under_review → approved / rejected

**프론트엔드**:
- `/approvals` 페이지 (`features/admin/approvals/ApprovalsPage.tsx`)
- 승인 드로어 (`ApprovalsDrawer.tsx`)
- Step 1에서 추가한 사이드바 "Approvals" 링크를 실제 페이지로 교체

**Vulnerabilities 화면 연동** (선택):
- conditional 라이선스 컴포넌트에서 "승인 요청" 버튼 → `/approvals` 생성

### DoD 체크리스트

- [ ] DB migration `alembic upgrade head` 성공
- [ ] `POST /v1/admin/approvals` → 승인 항목 생성
- [ ] `PATCH /v1/admin/approvals/{id}/status` → 상태 전이 (invalid 전이 시 422)
- [ ] `/approvals` 페이지: pending 목록, under_review 목록, 완료 목록 탭
- [ ] 드로어: 상태 변경 버튼 (Approve/Reject/Under Review)
- [ ] 상태 변경 시 Audit Log 기록
- [ ] require_super_admin_or_404 적용 (Developer는 404)
- [ ] pytest green (unit + integration)
- [ ] security-reviewer PASS

### 구현 프로토콜

```
1. Step 2가 main에 머지됐는지 확인:
   git checkout main && git pull --ff-only

2. 백그라운드: backend-developer 에이전트
   - 모델 + migration + API + service 구현

3. 메인: frontend-dev 에이전트 (브랜치에서 직접)
   - ApprovalsPage + ApprovalsDrawer 구현
   - Step 1 사이드바 ComingSoonPage → 실제 ApprovalsPage 교체

4. 두 에이전트 완료 후 통합:
   cd apps/backend && python -m pytest tests/ -x -q 2>&1 | tail -30

5. security-reviewer 에이전트 (상태 전이 로직 + RBAC 집중 검토)

6. PR 생성 + CI + 머지
7. 이 파일 Step 3 [x] 업데이트
```

---

## Step 4: Phase 3 미완 — SBOM·Settings·/scans 페이지

**상태**: `[x] DONE` — PR #19 머지 (commit 2dc547f, 2026-05-08).  
**브랜치**: `feature/step4-sbom-settings-scans`  
**예상 PR**: GitHub PR #19  
**에이전트**: `backend-developer` + `frontend-dev` (병렬)  
**예상 소요**: 1~1.5 세션

### 재개 검증 명령

```bash
# SBOM export 서비스 존재 여부
ls apps/backend/services/sbom_export.py 2>/dev/null && echo "EXISTS" || echo "MISSING"

# /scans 라우트 등록 여부
grep "path.*scans" apps/frontend/src/router.tsx | grep -v admin
```

### 구현 대상

#### 4-A. SBOM 탭

**백엔드** `services/sbom_export.py`:
- CycloneDX JSON (최소: metadata + components 배열, SPDX expression 포함)
- CycloneDX XML
- SPDX JSON
- SPDX Tag-Value

엔드포인트: `GET /v1/projects/{id}/sbom?format={cyclonedx-json|cyclonedx-xml|spdx-json|spdx-tv}`
- 응답: `Content-Disposition: attachment; filename=sbom-{project-slug}.{ext}`
- 인증 필수 (team-scoped IDOR)

**프론트엔드** `ProjectDetailPage.tsx`에 SBOM 탭 추가:
- 4개 포맷 다운로드 버튼 (각 버튼 클릭 → 파일 다운로드)
- SBOM 생성 일시 표시

#### 4-B. Settings 탭

`PATCH /v1/projects/:id` 백엔드 이미 존재. UI만 추가.

**프론트엔드** Settings 탭:
- 프로젝트 이름, 설명, Git URL, 기본 브랜치 편집 폼
- Archive 버튼 (확인 다이얼로그)
- 저장 성공/실패 토스트

#### 4-C. /scans 전역 스캔 큐 (일반 사용자용)

`listScans()` API 함수 이미 존재 (`lib/projectsApi.ts`).

파일: `apps/frontend/src/features/scans/ScansPage.tsx`
- 내 팀의 스캔 목록 (running/queued/succeeded/failed 탭)
- 각 행: 프로젝트명, 스캔 종류, 상태 배지, 시작/완료 시각
- Step 1 사이드바 ComingSoonPage → 실제 ScansPage 교체

### DoD 체크리스트

- [ ] SBOM JSON 다운로드: 파일이 유효한 JSON, `bomFormat: "CycloneDX"` 포함
- [ ] SBOM XML 다운로드: XML 파싱 성공
- [ ] SPDX JSON 다운로드: `spdxVersion` 필드 존재
- [ ] SPDX TV 다운로드: `SPDXVersion:` 헤더 존재
- [ ] Settings 탭: 이름 변경 → 저장 → 새로고침 후 반영
- [ ] Archive: 확인 후 프로젝트 목록에서 미표시
- [ ] `/scans`: dev 계정 스캔 목록 표시 (my-nodejs-app의 스캔 포함)
- [ ] pytest green
- [ ] `npx tsc --noEmit` 오류 없음

### 구현 프로토콜

```
1. backend-developer: sbom_export.py 구현 + 엔드포인트 등록
2. frontend-dev: SBOM 탭 + Settings 탭 + ScansPage 구현
3. 통합 테스트:
   TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email":"dev@trustedoss.dev","password":"TrustedDev2026!"}' \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
   curl -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/v1/projects/543fe8d9-7975-4c59-a130-d01604da9653/sbom?format=cyclonedx-json" \
     -o /tmp/test-sbom.json && python3 -m json.tool /tmp/test-sbom.json | head -5
4. DoD 체크리스트 확인
5. PR + CI + 머지
6. 이 파일 Step 4 [x] 업데이트
```

---

## Step 5: Phase 5 PR #16 — API Key + Webhook

**상태**: `[x] DONE` (backend only) — PR #20 머지 (commit c0cf4c1, 2026-05-08).
**미흡 (별도 chore)**: 단위/통합 테스트 보강, `/integrations` 프론트 페이지.  
**브랜치**: `feature/phase5-pr16-apikey-webhook`  
**예상 PR**: GitHub PR #20  
**에이전트**: `backend-developer` (주) + `security-reviewer` (보조)  
**예상 소요**: 1.5 세션

### 재개 검증 명령

```bash
ls apps/backend/api/v1/api_keys.py 2>/dev/null && echo "EXISTS" || echo "MISSING"
ls apps/backend/api/v1/webhooks/ 2>/dev/null && echo "EXISTS" || echo "MISSING"
docker-compose -f docker-compose.dev.yml exec -T postgres \
  psql -U trustedoss -d trustedoss -t -c \
  "SELECT tablename FROM pg_tables WHERE tablename='api_keys';" 2>/dev/null
```

### 구현 대상

#### 5-A. API Key 관리

- DB 모델: `APIKey` (prefix `tos_`, hash 저장, 절대 평문 저장 금지)
- 엔드포인트:
  - `POST /v1/api-keys` → 키 생성 (생성 시 1회만 평문 반환)
  - `GET /v1/api-keys` → 목록 (prefix만, hash 미노출)
  - `DELETE /v1/api-keys/{id}` → 폐기
  - 스코프: `project` / `team` / `org`
- 인증 미들웨어: `Authorization: Bearer tos_xxx_...` 형식 API Key 인증
- `/integrations` 프론트 페이지: 키 목록 + 생성 버튼 + 폐기 버튼

#### 5-B. Webhook 수신

- `POST /v1/webhooks/github`: HMAC-SHA256 서명 검증 (`X-Hub-Signature-256`), `delivery_id` 멱등성
- `POST /v1/webhooks/gitlab`: 토큰 헤더 검증 (`X-Gitlab-Token`)
- 수신 후 Celery 스캔 태스크 enqueue (이미 존재)
- 이벤트 타입: push / pull_request(GitHub), push / merge_request(GitLab)

### DoD 체크리스트

- [ ] API Key 생성 시 `tos_` prefix 포함, DB에 hash만 저장
- [ ] 생성 응답에 평문 키 1회 포함, 이후 재조회 불가
- [ ] 잘못된 서명 Webhook → 401
- [ ] 동일 `delivery_id` 재전송 → 200 (중복 처리 없음)
- [ ] `/integrations` 페이지: 키 생성/폐기 동작
- [ ] security-reviewer PASS (API Key 해싱, Webhook 서명 검증 집중)
- [ ] pytest unit + integration green

---

## Step 6: Phase 5 PR #17 — 빌드 게이트 + PR 코멘트 + GitHub Actions

**상태**: `[x] DONE` — PR #21 머지 (commit 2f7c5d7, 2026-05-08).  
**브랜치**: `feature/phase5-pr17-build-gate-pr-comment`  
**예상 PR**: GitHub PR #21  
**에이전트**: `backend-developer` + `devops-engineer` (병렬)  
**예상 소요**: 1~1.5 세션

### 구현 대상

- `services/policy_gate.py`: Critical CVE or 금지 라이선스 → `{"gate": "fail", "reason": "..."}` 반환
- `GET /v1/projects/{id}/gate-result`: 현재 스캔의 게이트 결과 JSON
- `services/sca_comment.py`: GitHub App Token / PAT로 PR 코멘트 게시 (동일 PR 재스캔 시 업데이트)
- `actions/scan/action.yml`: composite action (`trustedoss/scan-action@v1`)
- `templates/gitlab-ci.yml`: GitLab CI 템플릿

### DoD 체크리스트

- [ ] Critical CVE 있는 프로젝트 → `gate: "fail"` 반환
- [ ] `GITHUB_TOKEN` 환경변수 있으면 PR에 코멘트 게시
- [ ] GitHub Actions action YAML 유효 (`action.yml` 스키마 검증)
- [ ] `docker-compose.dev.yml exec` 환경에서 gate API 정상 응답

---

## Step 7: Phase 6 PR #18 — i18n 완성 + 알림 시스템

**상태**: `[~] IN_PROGRESS`  
**브랜치**: `feature/phase6-pr18-i18n-notifications`  
**예상 PR**: GitHub PR #22  
**에이전트**: `backend-developer` + `frontend-dev` + `i18n-specialist` (병렬)  
**예상 소요**: 1.5 세션

### 구현 대상

#### 7-A. i18n 완성

- Step 1~6에서 추가된 신규 화면 EN/KO 번역 키 100% 추출
- `i18next-parser` CI 게이트: `untranslated` 카운트 0 강제
- `docs/glossary.md` 용어집과 일관성 확인

#### 7-B. 알림 시스템 백엔드

현재 `apps/backend/notifications/` 디렉토리 비어있음.

- `notifications/email.py`: SMTP (aiosmtplib) 이메일 발송
- `notifications/slack.py`: Slack Webhook POST
- `notifications/teams.py`: MS Teams Webhook POST
- 5개 트리거: 새 Critical CVE, 스캔 완료, 승인 상태 변경, 사용자 비활성화, 비밀번호 리셋
- 발송 실패 → Celery retry (exponential backoff)

#### 7-C. 알림 센터 UI

- `/notifications` 페이지: 인앱 벨 아이콘 (헤더) + 알림 목록
- 읽음/안읽음 상태, 묶음 표시

#### 7-D. 비밀번호 찾기 실제 연동

- `POST /auth/forgot-password` 백엔드 엔드포인트 (현재 미구현, Phase 6 예정)
- ForgotPasswordPage stub 제거 → 실제 API 호출
- 이메일 존재 여부와 무관하게 uniform 204 반환 (CWE-204)

### DoD 체크리스트

- [ ] SMTP 설정 시 스캔 완료 이메일 발송 확인 (docker mailhog 테스트)
- [ ] Slack Webhook URL 설정 시 알림 수신 확인
- [ ] 비밀번호 찾기 → 이메일 발송 (mailhog)
- [ ] EN/KO i18n 미번역 키 0개
- [ ] pytest green

---

## Step 8: Phase 6 PR #19 — 안정성 + 백업

**상태**: `[ ] TODO`  
**브랜치**: `feature/phase6-pr19-stability-backup`  
**예상 PR**: GitHub PR #23  
**에이전트**: `backend-developer` + `frontend-dev` + `devops-engineer` (병렬)  
**예상 소요**: 1 세션

### 구현 대상

- **React Error Boundary**: 전역(`App.tsx`) + 페이지별 — 에러 시 fallback UI
- **디스크 가드**: `DISK_HARD_LIMIT_PCT` (기본 90%) 도달 시 스캔 차단 + Admin 알림
- **자동 백업**: Celery Beat 매일 자정 `pg_dump` + workspace tar → 로컬 보존 7일
- **수동 백업/복원 Admin UI**: 다운로드 버튼, 업로드 복원
- **WebSocket 재연결**: 브라우저 탭 이탈 후 복귀 시 진행률 즉시 동기화

### DoD 체크리스트

- [ ] 컴포넌트 에러 발생 시 fallback UI (전체 앱 crash 없음)
- [ ] 디스크 > 90% → 새 스캔 트리거 시 503
- [ ] 백업 파일 생성 (`pg_dump` 성공) + 다운로드 가능
- [ ] WebSocket 재연결 후 진행률 표시 재개

---

## Step 9: Phase 7 PR #20 — 설치 스크립트 + prod compose

**상태**: `[ ] TODO`  
**브랜치**: `feature/phase7-pr20-install-scripts`  
**예상 PR**: GitHub PR #24  
**에이전트**: `devops-engineer` (주)  
**예상 소요**: 0.5~1 세션

### 구현 대상

- `scripts/install.sh`: 인터랙티브 wizard (비밀번호 자동 생성, .env 자동 작성, 완료 시 URL 출력)
- `scripts/upgrade.sh`: DB 백업 선행 + `alembic upgrade head` + zero-downtime 권고
- `scripts/backup.sh`, `scripts/restore.sh`
- `docker-compose.yml` (프로덕션): Traefik + TLS (Let's Encrypt) + restart policy + 리소스 제한

### DoD 체크리스트

- [ ] `bash scripts/install.sh` → 완료 시 `http://localhost` 접속 가능 (fresh machine 가정)
- [ ] `bash scripts/backup.sh` → 백업 파일 생성
- [ ] `docker-compose.yml` `docker-compose config` 유효성 통과

---

## Step 10: Phase 7 PR #21 — Docusaurus + 가이드

**상태**: `[ ] TODO`  
**브랜치**: `feature/phase7-pr21-docs`  
**예상 PR**: GitHub PR #25  
**에이전트**: `doc-writer` (주)  
**예상 소요**: 1 세션

### 구현 대상

- Docusaurus 사이트 (`docs/` 하위 또는 별도 `docs-site/`)
- GitHub Pages 자동 배포 (`.github/workflows/docs.yml`)
- 관리자 가이드 EN+KO: 설치/설정/백업/업그레이드/트러블슈팅 (스크린샷 포함)
- 사용자 가이드 EN+KO: 프로젝트 등록/스캔/결과 해석/SBOM
- API Reference (OpenAPI → Docusaurus)
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`

### DoD 체크리스트

- [ ] `npm run build` 성공 (Docusaurus)
- [ ] GitHub Pages 배포 workflow 실행
- [ ] 관리자 가이드 EN+KO 각 5개 섹션 이상
- [ ] API Reference redoc 렌더링

---

## Step 11: Phase 8 PR #23 — OAuth + Demo SaaS

**상태**: `[ ] TODO`  
**브랜치**: `feature/phase8-pr23-oauth-demo-saas`  
**예상 PR**: GitHub PR #26  
**에이전트**: `backend-developer` + `devops-engineer` (병렬)  
**예상 소요**: 1.5 세션

### 구현 대상

- OAuth 통합 (GitHub, Google): 가입 시 개인 Team 자동 생성
- GCP 배포: Cloud Run + Cloud SQL PostgreSQL + Memorystore Redis + Terraform 스크립트
- 데모 시드 데이터 (`scripts/seed_demo.py`)

### DoD 체크리스트

- [ ] GitHub OAuth 로그인 → 개인 팀 자동 생성
- [ ] GCP Terraform `plan` 오류 없음
- [ ] 비용 추정 < $50/월

---

## Step 12: Phase 8 PR #24/#25 — 보안·성능·릴리스

**상태**: `[ ] TODO`  
**브랜치**: `feature/phase8-pr24-security-perf` + `feature/phase8-pr25-release`  
**예상 PR**: GitHub PR #27 + #28  
**에이전트**: `security-reviewer` + `devops-engineer` (병렬)  
**예상 소요**: 1 세션

### 구현 대상

- SAST: bandit + semgrep CI 추가
- SCA on self: TrustedOSS Portal로 자기 자신 스캔 (dog-fooding)
- 부하 테스트 (Locust): 동시 스캔 3개 / 동시 사용자 50명 → p95 < 1s
- v2.0.0 태그 + GitHub Release
- Trivy CI 게이트 HARD-FAIL 활성화 (현재 soft-fail)

### DoD 체크리스트

- [ ] bandit/semgrep CI High+ 0건
- [ ] 부하 테스트 보고서 (p95, error rate)
- [ ] GitHub Release v2.0.0 생성
- [ ] CHANGELOG.md 자동 생성

---

## BLOCKED 단계 기록

현재 BLOCKED 단계 없음.

> BLOCKED 발생 시 아래 양식으로 기록:
> ```
> ## BLOCKED: Step N — [이름]
> 발생일: YYYY-MM-DD
> 사유: [구체적인 미통과 DoD 항목]
> 시도 횟수: 3/3
> 재개 방법: [다음 세션에서 할 일]
> ```

---

## 세션 종료 체크리스트

세션을 종료하기 전 반드시 실행:

```bash
# 1. 현재 단계 상태를 이 파일에 반영 ([~] IN_PROGRESS 또는 [x] DONE)
# 2. 미완성 작업을 브랜치에 draft commit
git add -A && git commit -m "wip: step N — 중단 지점 기록"

# 3. 이 파일의 현재 상태 commit
git add docs/autonomous-execution-plan.md
git commit -m "plan: step N 상태 업데이트"

# 4. 브랜치 push (다음 세션에서 checkout 가능하도록)
git push origin HEAD
```

---

## 참조 문서

| 문서 | 역할 |
|------|------|
| `CLAUDE.md` | 핵심 규칙 13개, 기술 스택, 아키텍처 결정 |
| `docs/v2-execution-plan.md` | Phase별 상세 계획 (DoD, 에이전트 패턴) |
| `docs/sessions/_next-session-prompt-phase4-pr15-*.md` | Step 3 단일 진실 (169줄) |
| `MEMORY.md` | 장기 기억 인덱스 (피드백, 프로젝트 상태) |
| `.claude/projects/.../memory/project_phase4_admin_followup_pr.md` | Step 2 chore PR #9 상세 |
