# 핸드오프 — W9-#57/#58 + admin reset + worktree sync (2026-05-28)

> v2.4.0 GA-ready 핸드오프 (`docs/sessions/2026-05-28-v2.4.0-ga-ready.md`) 의 직후 후속. 사용자 인테이크 3건 + main worktree 정리.

## 한 줄 요약
2026-05-28 GA-ready 직후 사용자 인테이크 3건 처리: ① admin@demo 비번/활성화 ② W9-#57 chart segment toggle (PR #257) ③ W9-#58 Compliance 통합 그리드 (PR #258). 마지막에 main worktree 가 다른 세션 sub-agent 작업으로 silent-switched 되어 docker-compose mount 가 stale 코드 가리키던 문제를 stash + sub-agent worktree remove + checkout main + dev container restart 로 해결.

## 3 인테이크 완료

| # | 작업 | PR | 효과 |
|---|---|---|---|
| 1 | admin@demo 비번 + 활성화 | — | `admin@demo.trustedoss.dev` / `DemoTest2026!` 즉시 로그인 (원인 = `is_active=false` + random password) |
| 2 | **W9-#57** chart segment toggle | **#257** (`112c86f`) | `searchParamsToggle` helper 3 함수 + 17 unit tests + 5 chart consumer 화면 적용. LicensesTab round-trip 단위 케이스만 Playwright E2E 후속 위임 (skip). |
| 3 | **W9-#58** Compliance 통합 그리드 | **#258** (`e75b5f2`) | BE 신규 `GET /v1/projects/:id/compliance` + FE 통합 그리드 552줄 (sub-tab wrapper 138줄 대체). `?cview=` backward-compat. LicenseDrawer fork 0. |

본 세션 PR 누계: **28** (#231~#258).

## main worktree 정리

### 문제 (사용자 보고)
- 사용자가 본 dev portal 화면이 W9-#57/#58 + W11 + W9-#50 + W10 의 어떤 것도 반영 안 됨.

### 진단
- main worktree (CWD `/Users/1112821/projects/trustedoss-portal`) 가 다른 세션 sub-agent 작업으로 `feat/scan-log-persist-and-stage-rename` brand 로 silent-switched (HEAD `90813b8`, main 보다 2 commits ahead, origin push 안 됨).
- `docker-compose.dev.yml` volume mount (`./apps/backend:/app`, `./apps/frontend:/app`) 가 이 stale working tree 코드를 mount → dev portal 화면이 main 의 PR 들 미반영.
- 추가: sub-agent worktree `agent-a977ac079c302616c` 가 main brand 점유 + locked → main worktree 가 main 으로 checkout 불가.

### 해결 (실제 명령)
```bash
cd /Users/1112821/projects/trustedoss-portal

# wip 보존 (modified 10 + untracked 9)
git stash --include-untracked -m "wip-before-main-sync-20260528-1957"

# sub-agent worktree 의 main brand 점유 해제
git worktree unlock /Users/1112821/projects/trustedoss-portal/.claude/worktrees/agent-a977ac079c302616c
git worktree remove /Users/1112821/projects/trustedoss-portal/.claude/worktrees/agent-a977ac079c302616c

# main 으로 정리
git checkout main
git pull --ff-only origin main   # → e75b5f2 (W9-#58)

# dev container reload — volume mount 가 새 코드 보게
docker-compose -f docker-compose.dev.yml restart frontend backend celery-worker
```

검증:
- `main` HEAD = `e75b5f2`
- backend `{"status":"ok"}`
- `GET /v1/projects/.../compliance` → HTTP 401 (인증 실패 = endpoint 등록 정상 ✓)
- frontend `searchParamsToggle.ts` + `ComplianceTab.tsx` 통합 그리드 docstring 확인

### 보존된 wip (`stash@{0}`)
`feat/scan-log-persist-and-stage-rename` brand 의 commit 들 (`90813b8` + `ea75d1f`) — local-only, origin push 안 됨. 다른 세션 sub-agent 의 scan-log persistence + scan-detail page 작업. 본 세션과 무관.

사용자 판단 필요:
- 의미 있으면 `git checkout feat/scan-log-persist-and-stage-rename && git stash pop stash@{0}` → 검토 + push
- 무의미하면 `git stash drop stash@{0}`

stash list 의 다른 entries (`stash@{1}~{4}`) 는 2026-05 초중반 오래된 wip — 별도 정리 가능.

## 발견된 패턴 (메모리 강화)

`feedback_parallel_subagent_worktree_isolation.md` + `feedback_parallel_agent_worktree_shared_files.md` 가 이미 존재. 본 사례가 그 패턴 재현 + 새 변종:

**새 변종**: sub-agent worktree 가 **main brand 를 점유 + lock** 한 채 종료. main worktree 가 main 으로 checkout 불가. 해결 = `git worktree unlock <path>` + `git worktree remove <path>` 후 main checkout.

향후 sub-agent 위임 prompt 에서:
- worktree 작업 종료 시 main brand 로 checkout 되지 않고 own brand 유지 (main brand 점유 회피)
- 또는 작업 종료 시 worktree remove 명시 요청

## 다음 세션 권장 sanity check (첫 작업)

```bash
cd /Users/1112821/projects/trustedoss-portal
pwd                          # main worktree 인지
git status                   # clean?
git branch --show-current    # main?
git log --oneline -3         # HEAD = origin/main?
git worktree list            # 다른 worktree 가 main brand 점유 중인지
docker-compose -f docker-compose.dev.yml ps   # 모두 healthy?
```

5 항목 모두 OK 후 작업 시작. 한 줄이라도 어긋나면 위 sync 절차 참조.

## v2.4.0 GA-ready 상태 (변경 없음)

운영자 release tag cut 만 남음 — `docs/sessions/2026-05-28-v2.4.0-ga-ready.md` §운영 레인 참조.

본 세션 추가 작업 (W9-#57/#58 + admin reset) 모두 origin/main 에 포함되어 GA tag cut 시 자동 포함.
