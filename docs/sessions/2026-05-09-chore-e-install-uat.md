# Session Handoff — 2026-05-09 — Chore E (install/restore UAT + shellcheck CI)

> Post-GA cleanup 세션. Fresh-Linux UAT 자동화 + 운영자 수동 체크리스트 + shellcheck CI 게이트.
> 시작 시점: main HEAD = `9816f21` (post-GA cleanup plan + chore-backlog M/N/O/P entries).
> 종료 시점: PR #38 머지 후 (chore/install-restore-uat → main).

## 1. 처리한 PR

| Chore | PR | 비고 |
|-------|-----|------|
| E     | #38 | install.sh `--no-prompt` + install-uat workflow + shellcheck CI gate + uat-checklist EN/KO |

## 2. 작업 내역

### 옵션 A — 자동 CI (회귀 가드)

신규: `.github/workflows/install-uat.yml`
- Ubuntu 22.04 GitHub-hosted runner.
- `docker-compose` V1 1.29.2 명시적 설치 (CLAUDE.md 핵심 규칙 #10 — runner는 V2만 ship).
- 핵심 우회: `docker-compose.yml`을 `docker-compose.dev.yml`로 swap한 뒤 install.sh 실행. 프로덕션 compose는 `trustedoss/backend:2.0.0` 등 미공개 이미지를 풀해야 하므로 CI에서 직접 검증 불가. install.sh의 wrapper 로직 (env 생성, healthcheck 폴링, alembic 호출, super_admin 부트스트랩, backup/restore round-trip) 검증이 목적.
- Trigger: `workflow_dispatch` + cron `0 3 * * 0` (주간).
- 6단계 검증: install → /health → login + /v1/projects smoke → backup.sh → restore.sh (`BACKUP_RESTORE_CONFIRM=yes`) → post-restore /health.

신규: `scripts/install.sh` `--no-prompt` 모드
- env: `INSTALL_HOST` / `INSTALL_ADMIN_EMAIL` / `INSTALL_ADMIN_PASSWORD` / `INSTALL_SECRET_KEY` / `INSTALL_REUSE_ENV`.
- 미설정 시 합리적 default + `openssl rand` 폴백. `INSTALL_ADMIN_PASSWORD` 미설정 시 randoms를 stdout 1회 출력 (CI 캡처 가능, 즉시 rotate 안내 메시지).
- 기존 대화형 모드는 그대로 유지 — 회귀 X.

### 옵션 B — 운영자 체크리스트

신규: `docs-site/docs/installation/uat-checklist.md` (+ KO mirror)
- 8단계: 호스트 준비 → install.sh (대화형/비대화형 선택) → 첫 로그인 + 프로젝트 → backup.sh → cross-host restore (vm-a → vm-b) → multi-PG 16→17 (선택) → 정리 → 결과 보고.
- sidebar 등록 (`docs-site/sidebars.ts`).
- EN + KO build 양쪽 SUCCESS.

### Shellcheck CI 게이트

신규: `.github/workflows/ci.yml`의 `shellcheck` 잡
- `apt-get install shellcheck` → `shellcheck --severity=warning scripts/*.sh`.
- 정책: error + warning은 hard-fail, info (SC1091 등)는 advisory.
- `lint`와 `typecheck` 사이에 위치. 평균 ~30s job.

부수 fix:
- `scripts/upgrade.sh`: SC2034 (unused `i` 루프 변수 → `_`).
- `scripts/release.sh`: SC2046 (의도적 word-split — `# shellcheck disable=SC2046` 정당화 주석 추가).

## 3. 측정 가능한 결과

- **변경 라인** (커밋 시점 측정):
  - `scripts/install.sh`: +56 / -10
  - `scripts/upgrade.sh`: +1 / -1
  - `scripts/release.sh`: +4 / -0
  - `.github/workflows/ci.yml`: +30 / -0 (신규 shellcheck 잡)
  - `.github/workflows/install-uat.yml`: +136 / -0 (신규)
  - `docs-site/docs/installation/uat-checklist.md`: +175 / -0 (신규 EN)
  - `docs-site/i18n/ko/.../installation/uat-checklist.md`: +175 / -0 (신규 KO)
  - `docs-site/sidebars.ts`: +1 / -0
  - `docs/chore-backlog.md`: Chore E 항목 ~~취소선~~ + 결과 요약
- **shellcheck 결과** (로컬 0.11.0):
  - `--severity=warning`: PASS (모든 5개 스크립트 pass)
  - `--severity=info`: 2 advisory finding (SC1091 — `. ./.env` source — 정당화 주석 이미 인라인)
- **Docusaurus build**: EN + KO 양쪽 `[SUCCESS]`. 신규 페이지 `build/docs/installation/uat-checklist.html` + `build/ko/docs/installation/uat-checklist.html` 렌더링 확인.

## 4. 자율 실행 중 발생한 이슈와 처리

### 이슈 1 — 프로덕션 compose의 미공개 이미지

`docker-compose.yml`이 `trustedoss/backend:2.0.0`을 pull하도록 되어 있으나, 본 프로젝트는 이미지를 Docker Hub / GHCR에 push하지 않음. CI runner에서 풀 시 404. **해결**: install-uat workflow가 `docker-compose.yml`을 `docker-compose.dev.yml` 내용으로 swap (build context 사용) 한 뒤 install.sh 실행. teardown 단계에서 원본 복구. workflow header에 swap 사유 인라인 주석.

### 이슈 2 — install.sh 하드코딩된 `-f docker-compose.yml`

install.sh가 compose 파일 경로를 8군데 하드코딩. swap 외 대안 (예: `--compose-file` flag 추가)은 변경 surface가 커서 탈락. 위 swap이 더 안전.

### 이슈 3 — shellcheck severity 정책

기본 (info까지 fail) 시 `set -a; . ./.env; set +a` 두 줄에서 SC1091 발생. 인라인 disable 주석은 info 레벨까지 막지 못함 (shellcheck 0.11.0 동작). **해결**: `--severity=warning`으로 CI 게이트 조정 — error + warning만 hard-fail. SC1091 advisory는 로그에 남되 PR을 막지 않음. 본 정책은 CI yaml 인라인 주석에 명시.

## 5. 다음 세션 권장 작업

1. **chore-backlog Chore P** — Trivy HIGH hard-fail 전환 + worker-image refresh.
2. **chore-backlog Chore Q** — Cloud Run backend 외부 노출 가드 (Cloud Armor / IAP).
3. **chore-backlog Chore R** — Backup upload 이름 충돌 (`_NAME_RE` regex 보강) + restore.sh `--confirm` argv flag (BACKUP_RESTORE_CONFIRM env 회피 — 본 PR 검증 시 사용했으나 Chore O M5 대상).

## 6. 참고

- workflow_dispatch trigger: PR 머지 후 첫 manual run으로 실제 통과 확인 권장. cron 첫 실행은 다음 일요일 03:00 UTC.
- `BACKUP_RESTORE_CONFIRM=yes` env 우회는 의도적 (현재 동작). Chore O M5 (별도 chore)에서 argv flag로 변경 예정 — 그 PR 머지 후 install-uat workflow의 env를 `--confirm` 플래그로 마이그레이션.
