---
session: Screenshot series cleanup (Session 4)
date: 2026-05-10
phase: post-GA / docs
branch: chore/screenshot-series-cleanup
status: complete
---

# Screenshot series cleanup — Session 4

## 목적

`docs/chore-backlog.md` "Screenshots automation" 시리즈의 **잔여 4개 항목** 처리.
Session 1~3 (PR #53/#54/#55/#56) 가 남긴 cleanup 을 단일 chore PR 로 묶음.

## 처리 항목

### 1. `test.fixme` 3컷 해제 — root cause + fix

세 fixme 의 공통 root cause: harness `expectMounted()` 가 빈 결과 시 visibility race 에 빠지는 selector 패턴 (`tbody OR empty-cell` — empty tbody zero-height 가능).

| Slug | Root cause | Fix |
|---|---|---|
| `user-scans-queue` | `ScansQueueHarness.expectMounted` 가 `scans-tbody` OR `scans-empty` 기다림. 시드 scan 이 succeeded 이고 default tab 은 running → 0 rows → empty cell 의 visibility 가 zero-height race. | predicate 를 항상-render 되는 `scans-table` + `scans-pagination` + `aria-busy != "true"` polling 으로 교체. 추가로 `selectTab(tab)` verb 도입, capture spec 에서 `succeeded` 탭으로 switch (의미있는 row 가 보이는 자산). |
| `user-approvals-inbox` | 동일 패턴 (`approvals-tbody` OR `approvals-empty`). 시드는 approvals 미생성 → 0 rows. | 동일 fix: `approvals-table` + `approvals-pagination` + `aria-busy` polling. capture 는 빈 inbox 자체가 valid 가이드 자료라 그대로 유지. |
| `user-notifications-prefs` | `NotificationsHarness.gotoPreferences` 가 `notifications-prefs-form` 만 기다림. `useNotificationPrefs` 가 loading/error 상태에 머무르면 form testid 가 mount 안 됨 → timeout. | predicate 를 `form OR loading OR error` any-of 로 완화. 인터랙티브 verb (`togglePreference`/`savePreferences`) 는 form 자체를 별도로 wait 하므로 회귀 없음. |

### 2. 신규 캡처 1컷

- **`user-integrations-webhooks`** — `/integrations` 의 Webhooks 섹션 (탭 아닌 section). `IntegrationsHarness.scrollToWebhooks()` verb 신규: `integrations-webhooks-section` scrollIntoView + `webhook-github`/`webhook-gitlab` 카드 가시성 검증.

### 3. `admin/api-keys.md` (EN+KO) — 기존 PNG 재사용

`/admin/api-keys` 는 별도 컴포넌트가 아니라 `/integrations` 와 동일 surface. "Manage with the /integrations UI" 섹션에 `user-integrations-keys.png` + `user-integrations-key-create.png` 재사용 (alt text 만 admin 관점으로 조정).

### 4. `./img/...` placeholder 절대경로 정정

| 파일 | placeholder | 처리 |
|---|---|---|
| `notifications.md` (EN+KO) | `./img/notifications-bell.png` | **라인 제거**. 0-unread 시드로는 가이드 가치 낮음 → backlog 로 분리 (시드 보강 chore). |
| `notifications.md` (EN+KO) | `./img/notifications-prefs.png` | `/img/screenshots/user-notifications-prefs.png` 로 정정 + alt text 강화. |
| `integrations.md` (EN+KO) | `./img/integrations-webhooks.png` | `/img/screenshots/user-integrations-webhooks.png` 로 정정 + alt text 강화. |

## 변경 파일

- **Harness (4)**: `ScansQueueHarness.ts` / `ApprovalsHarness.ts` / `NotificationsHarness.ts` / `integrations.ts`
- **Spec (1)**: `capture_user_guide.spec.ts` (3 fixme 해제 + 1 신규 spec)
- **Docs EN (3)**: `admin-guide/api-keys.md` / `user-guide/integrations.md` / `user-guide/notifications.md`
- **Docs KO (3)**: 동일 페이지 KO mirror
- **Backlog (1)**: `docs/chore-backlog.md` (Session 4 ✅ 등재 + bell capture chore 분리)
- **PNG (4 신규/회귀 갱신)**: `user-scans-queue` / `user-approvals-inbox` / `user-notifications-prefs` / `user-integrations-webhooks` + 캡처 런으로 갱신된 기존 PNG 들

## 검증

- `make screenshots-capture` — **29/29 pass, 0 fixme** (이전 26 pass + 3 fixme)
- `npm run lint` (frontend) — 0 errors / 18 warnings (pre-existing)
- `npx tsc --noEmit` (frontend) — 0 errors
- `npm run build` (docs-site) — EN+KO Docusaurus build SUCCESS (broken-link warning 1건은 pre-existing KO `installation/docker-compose` 의 `reference/*` 링크 — 본 PR 무관)

## 잔여 / Out of scope

backlog 에 별도 sprint 로 등재됨:

- 헤더 종 (unread badge) 캡처 — 시드 보강 (`seedE2eUser({ withNotifications })`) 필요. ~0.5 세션
- Visual regression CI (Percy / Chromatic / pixel diff) — ~1~1.5 세션
- Animated walkthroughs (`.gif` / `.mp4`) — ~1 세션
- Locale-specific 이미지 (한글 데이터 KO) — ~1 세션
- a11y alt text 감사 — ~0.5 세션
- 이미지 압축 자동화 (`oxipng` / `pngquant`) — ~0.5 세션

## 다음 세션 후보

backlog `## 새 세션 시작 시 사용` 섹션 그대로 (A4/A5 / L1 role 분리 / D2 backup refactor / D1 fixme / B v2.1 sprint).
