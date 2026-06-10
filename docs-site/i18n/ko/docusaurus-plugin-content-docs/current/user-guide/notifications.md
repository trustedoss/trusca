---
id: notifications
title: 알림
description: 스캔·게이트·CVE·승인·디스크 이벤트에 대한 인앱·이메일·Slack·Teams 알림을 읽고, 설정하고, 무음 처리합니다.
sidebar_label: 알림
sidebar_position: 8
---

# 알림

<!-- docs-uat: id=notifications-login kind=ui harness=login(dev@demo.trustedoss.dev,DemoTest2026!) tier=nightly -->
알림 시스템은 본인이 관심 있는 프로젝트의 이벤트 — 스캔 종료, 게이트 실패, 의존하는 컴포넌트에 신규 CVE 등록, 승인 대기, 라이선스 정책 위반 — 를 알려줍니다. 알림은 **네 채널**(인앱·이메일·Slack·Microsoft Teams)로 팬-아웃되며, 어떤 채널로 받을지는 전역적으로 결정합니다.

:::note 대상 독자
로그인된 모든 사용자. 헤더 종 아이콘과 `/notifications` 페이지는 모든 역할에 표시됩니다. admin은 추가로 SMTP / Slack / Teams 전송 경로를 [디스크·시스템 health](../admin-guide/disk-and-health.md)에서 구성합니다.
:::

:::note
알림 트리거는 producer 측 서비스에 연결되어 있습니다 — 스캔, 취약점
수집, 라이선스 탐지, 정책 게이트, 승인 워크플로가 인앱 알림 행을
발생시키며, Preferences 설정에 따라 외부 채널로도 전달됩니다. 전체
트리거 목록은 [트리거](#트리거) 단락을 참고하세요.
:::

## 헤더 종 아이콘

모든 페이지의 헤더 우측 상단에 종 아이콘이 있습니다. 배지는 **읽지 않은** 인앱 알림 수를 표시합니다.

- 0 — 배지 없음.
- 1–99 — 정확한 개수.
- 100+ — 레이아웃이 깨지지 않도록 **99+**로 상한 처리.

![헤더 종과 읽지 않은 카운트 배지 — 종 우상단의 빨간 원이 현재 읽지 않은 합계를 표시](/img/screenshots/user-notifications-bell.png)

종을 클릭하면 **`/notifications`** 인박스로 곧장 이동합니다. 현재 릴리스에서는 종에 드롭다운 미리보기가 노출되지 않습니다 — [로드맵](#로드맵) 참고.

## `/notifications` 인박스

`/notifications`는 본인이 받은 모든 알림 목록입니다(최신순). 20개 단위로 한 페이지씩 로드되며, 하단의 **Previous** / **Next** 컨트롤로 이력을 탐색합니다.

![/notifications 의 알림 인박스 — 읽음/읽지않음 행, kind 배지, 페이지네이션 컨트롤](/img/screenshots/user-notifications-inbox.png)

각 행은 다음을 표시합니다.

- **제목** — 읽지 않은 동안 굵게.
- **본문** — 한두 줄.
- **채널 아이콘** — 본인에게 이 이벤트를 어떤 채널로 전달했는지(인앱은 항상 표시; 이메일·Slack·Teams는 옵트인한 경우만).
- **타임스탬프** — 호버 시 절대시각, 평소에는 상대시각.
- **읽음 처리** — 행의 어디든 클릭하면 읽음 처리되며 행이 흐리게 표시됩니다.
- **열기** — 클릭하면 원본 리소스로 이동하며 도중에 읽음 처리됩니다.

일괄 동작: **Mark all as read**(현재 페이지의 읽지 않음 배지를 정리).

## 환경설정

인박스 아래의 **Preferences** 섹션은 트리거별로 **전역·채널별** 토글 4개를 나열합니다. 선택은 모든 트리거에 동일하게 적용됩니다 — 현재 릴리스에서는 트리거별 매트릭스가 없습니다([로드맵](#로드맵) 참고).

![알림 환경설정 — 이메일·Slack·Teams 채널 토글과 항상 켜진 인앱 행 — KO locale](/img/screenshots/user-notifications-prefs-ko.png)

| 채널 | 기본값 | 비고 |
|---|---|---|
| 인앱 | 켜짐 | 폴백 채널. 항상 사용 가능; 대칭성을 위해 토글은 노출됩니다. |
| 이메일 | 켜짐 | 운영자가 `SMTP_*`를 구성해야 동작. |
| Slack | 꺼짐 | `SLACK_WEBHOOK_URL` 구성 필요. |
| Teams | 꺼짐 | `TEAMS_WEBHOOK_URL` 구성 필요. |

토글은 draft 상태로 들어갑니다. **Save**를 클릭해 변경을 영속화하세요 — 페이지가 dirty 상태를 추적하며, 변경이 없으면 Save가 비활성화됩니다.

## 종 아이콘은 얼마나 신선한가?

프론트엔드는 탭이 활성 상태인 동안 **60초마다** 읽지 않은 개수를 폴링합니다. 두 가지 최적화로 채널을 가볍게 유지합니다.

- 브라우저 탭이 **숨겨진 상태**일 때(Page Visibility API가 `hidden`을 보고하면) 폴링이 **일시 정지**되어 배터리·서버 대역을 아낍니다.
- 탭이 다시 포커스를 받으면 프론트엔드는 **즉시** 1회 폴링을 발사하여 다음 60초 틱 이전에 배지가 따라잡습니다.

여러 포털 탭을 열어둔 경우 각자 독립적으로 폴링하지만 — 읽지 않음 상태는 서버가 정답이므로 카운트는 수렴합니다.

## 트리거

일곱 가지 서로 다른 트리거가 알림을 발생시킵니다.

| 트리거 | 발생 시점 |
|---|---|
| `scan_completed` | 본인이 시작한 스캔 또는 본인이 watch 중인 프로젝트의 스캔이 성공으로 종료. |
| `scan_failed` | 본인이 시작한 스캔 또는 본인이 watch 중인 프로젝트의 스캔이 실패. |
| `cve_detected` | 본인의 스캔에 이미 존재하는 컴포넌트에 신규 CVE 등록(DT NVD ingest가 기존 컴포넌트와 재상관). |
| `license_violation` | 본인이 watch 중인 프로젝트의 스캔에서 금지 라이선스 컴포넌트가 발견. |
| `approval_pending` | 컴포넌트가 승인을 요구할 때 발송. 현재 릴리스에서는 조직의 **모든 super-admin**에게 전달됩니다(팀별 승인자 라우팅 없음). 지정 승인자(designated approver) 모델은 로드맵 항목입니다. |
| `approval_state_changed` | 본인이 제출한 승인 요청의 상태가 바뀔 때(승인 / 거부 / 검토 전환) — 요청자에게 처리 결과를 알립니다. |
| `policy_gate_failed` | CI 빌드 게이트가 실패(Critical CVE 또는 금지 라이선스가 빌드를 차단). |

채널 선택은 전역입니다 — **Preferences** 탭이 모든 트리거의 전달 채널을 결정합니다.

## 정상 동작 확인

<!-- docs-uat: id=notifications-scan-completed-badge kind=manual tier=manual -->
- 본인 소유 프로젝트에서 스캔을 트리거하면 완료 후 몇 초 내에 종 배지가 증가하고 `/notifications`에 새 행이 나타납니다.
<!-- docs-uat: id=notifications-mark-read-decrements kind=ui harness=notificationsMarkReadDecrements tier=nightly -->
- 두 번째 탭에서 `/notifications`를 열고 한 행을 읽음 처리하면 첫 번째 탭의 종 배지가 60초 내에 감소합니다.
<!-- docs-uat: id=notifications-email-disable-inapp-only kind=ui harness=notificationsEmailDisableInappOnly tier=nightly -->
- **Preferences**에서 이메일 채널을 전역 비활성화하고 **Save**를 클릭한 뒤 다른 스캔을 실행하면, 다음 `scan_completed` 알림은 인앱으로만 도착합니다.

## 트러블슈팅

- **종 배지가 갱신되지 않음** — 탭이 다른 창 뒤에 숨어 있을 수 있습니다. 전면으로 가져오면 포커스 시 즉시 폴링이 카운트를 새로 고칩니다.
- **이메일이 도착하지 않음** — 운영자가 SMTP를 구성했는지, 목적지가 본인의 검증된 이메일(`/profile`에서 확인)인지 검증하세요.
- **Slack 메시지가 도착하지 않음** — 운영자가 `SLACK_WEBHOOK_URL`을 설정했는지, 채널이 여전히 존재하는지 확인하세요. Webhook이 폐기되면 Slack은 조용히 404로 응답합니다.

## 로드맵

매뉴얼이 이전에 약속했으나 v0.10.0에 포함되지 않은 항목.

- 헤더 종 드롭다운(최근 5개 알림 + "인박스로 가기" 푸터) — 예정. 현재는 종이 `/notifications`로 곧장 이동합니다.
- `/notifications`의 무한 스크롤 — 예정. 현재는 Previous / Next 페이지 네비게이션을 사용합니다.
- 트리거 × 채널 환경설정 매트릭스(예: `policy_gate_failed`만 Slack으로) — 예정. 현재는 채널 선택이 모든 트리거에 전역 적용됩니다.
- admin용 `disk_pressure` 알림 트리거 — 예정. 디스크 압박 이벤트는 현재 admin 대시보드에만 노출됩니다.

## 함께 보기

- [인증과 프로필](./auth-and-profile.md) — 검증된 이메일이 이메일 알림의 목적지입니다.
- [통합](./integrations.md) — Webhook 시크릿은 알림 채널과 별개입니다.
- [디스크·시스템 health](../admin-guide/disk-and-health.md) — SMTP / Slack / Teams의 운영자 셋업.
