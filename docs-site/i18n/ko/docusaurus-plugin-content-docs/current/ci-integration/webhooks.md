---
id: webhooks
title: Webhooks
description: GitHub과 GitLab Webhook을 구성해 push와 PR/MR 이벤트로 TrustedOSS 스캔을 트리거합니다 — HMAC 서명 검증 포함.
sidebar_label: Webhooks
sidebar_position: 4
---

# Webhooks

Webhook은 Git 호스트가 포털로 이벤트를 푸시하게 합니다 — 보통 `push`와 `pull_request`(GitHub) / `merge_request`(GitLab) — 그리고 포털이 자동으로 스캔을 시작합니다. CI에서 스캔을 돌리는 방식의 대안이며, 많은 팀이 둘 다 사용합니다.

:::note 대상 독자
프로젝트별 Webhook을 구성하는 `team_admin`과 Git 호스트 측을 연결하는 엔지니어. 포털 엔드포인트는 공개 인터넷에서 접근 가능합니다.
:::

## 엔드포인트

| 출처 | URL | 인증 |
|---|---|---|
| GitHub | `POST https://trustedoss.example.com/api/v1/webhooks/github` | `X-Hub-Signature-256`의 HMAC-SHA256 서명. |
| GitLab | `POST https://trustedoss.example.com/api/v1/webhooks/gitlab` | `X-Gitlab-Token`의 토큰. |

두 엔드포인트 모두 공개(JWT 없음)이지만 프로젝트의 webhook secret을 요구합니다. 시크릿은 프로젝트별이며 Webhook 활성화 시 생성됩니다.

## 셋업 — GitHub

### 1. 포털에서 Webhook 활성화

v2.0.0에서 Webhook 활성화는 운영자 전용입니다. Project Settings 탭은 아직 Webhook 컨트롤을 노출하지 않습니다. 운영자는 서버 측에서 프로젝트별 `webhook_secret`을 부트스트랩하고(`apps/backend/services/webhook_service.py` 참고), 생성된 Webhook URL은 **Integrations** 페이지 → Webhooks 섹션에 표시됩니다. 셀프 서비스 활성화 UI는 로드맵에 있습니다.

### 2. GitHub에서 구성

1. 레포 → **Settings → Webhooks → Add webhook**.
2. **Payload URL** — 전송 URL.
3. **Content type** — `application/json`.
4. **Secret** — 포털에서 복사한 시크릿.
5. **Which events** — 선택
   - **Push** events.
   - **Pull requests** events.
6. **Active** — yes.
7. **Add webhook**.

GitHub은 즉시 `ping` 이벤트를 전송합니다. green ("Last delivery was successful") 표시를 확인하세요 — 그렇지 않다면 [트러블슈팅](#트러블슈팅) 참고.

### 3. 검증

커밋을 푸시. 포털에서 **Project → Scans**에 ~30초 내 새 스캔이 표시되어야 합니다.

## 셋업 — GitLab

### 1. 포털에서 Webhook 활성화

v2.0.0에서 Webhook 활성화는 운영자 전용입니다. Project Settings 탭은 아직 Webhook 컨트롤을 노출하지 않습니다. 운영자는 서버 측에서 프로젝트별 `webhook_secret`을 부트스트랩하고(`apps/backend/services/webhook_service.py` 참고), 생성된 Webhook URL은 **Integrations** 페이지 → Webhooks 섹션에 표시됩니다. 셀프 서비스 활성화 UI는 로드맵에 있습니다.

### 2. GitLab에서 구성

1. 프로젝트 → **Settings → Webhooks → Add new webhook**.
2. **URL** — 전송 URL.
3. **Secret token** — 포털에서 복사한 토큰.
4. **Trigger** — 체크
   - Push events
   - Merge request events
5. **SSL verification** — enabled.
6. **Add webhook**.

**Test → Push event** 버튼으로 연결을 검증. 포털이 전송을 로깅하고 204를 ack합니다.

### 3. 검증

커밋을 푸시. 포털 스캔 큐가 ~30초 내 픽업합니다.

## 서명 검증

### GitHub — HMAC-SHA256

GitHub은 다음을 계산:

```
X-Hub-Signature-256: sha256=<hex(hmac_sha256(secret, body))>
```

포털은 raw body에 대해 동일 HMAC을 재계산해 상수 시간 비교합니다. 불일치 시 401을 반환하고 전송을 로깅합니다.

### GitLab — token equality

GitLab은 토큰을 그대로 보냅니다:

```
X-Gitlab-Token: <token>
```

포털은 프로젝트의 저장된 토큰과 상수 시간 비교합니다. 불일치 시 401.

GitLab은 기본으로 HMAC을 지원하지 않습니다. 보안 정책상 HMAC이 필요하면 앞단에 reverse proxy를 두어 추가하고 포털 레이어에서 proxy를 검증하세요.

## 멱등성

두 Git 호스트 모두 실패 시 전송을 재시도합니다. 포털은 `delivery_id` 디듀플리케이션으로 반복을 처리합니다.

- GitHub은 `X-GitHub-Delivery`(전송별 UUID)를 제공.
- GitLab은 `X-Gitlab-Event-UUID`(14.x 이후 전송별 UUID)를 제공.

포털은 unique 인덱스가 걸린 `webhook_deliveries`에 `(source, delivery_id)`를 저장합니다. 중복 전송은 두 번째 스캔을 트리거하는 대신 200과 `{"status": "duplicate"}`로 응답합니다. 호스트 측 재시도 폭풍에서도 시스템이 멱등합니다.

## 스캔을 트리거하는 이벤트

| 이벤트 | 동작 |
|---|---|
| GitHub `push` to default branch | 새 커밋에 대해 `source` 스캔 트리거. |
| GitHub `pull_request` (opened, synchronize, reopened) | PR head SHA에 대해 `source` 스캔 트리거, SCA 코멘트 게시. |
| GitLab `Push Hook` to default branch | GitHub `push`와 동일. |
| GitLab `Merge Request Hook` (open, update, reopen) | GitHub `pull_request`와 동일. |

다른 이벤트는 수락되지만(200) 스캔을 트리거하지 않습니다. 포털은 수락된 모든 전송을 감사 로그에 기록합니다.

## 정상 동작 확인

Webhook 구성 후:

1. Git 호스트의 Webhook 페이지가 **ping / test** 전송 성공을 표시.
2. 커밋 푸시 시 포털에 30초 내 새 스캔이 생성됨.
3. 감사 로그가 `delivery_id`와 `event` 필드 포함 `webhook.deliver`를 기록.

## 트러블슈팅

### "Could not deliver: 401 Unauthorized"

서명이 일치하지 않습니다. 원인:

- 포털에서 Webhook 시크릿을 회전했지만 Git 호스트에 갱신하지 않음.
- 포털 앞단의 proxy가 body를 수정함(압축, JSON 재직렬화). 서명은 raw 바이트 기준이므로 1바이트 변경도 무효화.

재동기화: 포털에서 시크릿 회전, 새 값을 Git 호스트에 붙여넣고 redelivery 트리거.

### "Could not deliver: 404 Not Found"

URL이 틀렸습니다. 흔한 오타: `/api/` 누락, `/v1/` 누락, 백엔드 대신 프론트엔드 적중(`/webhooks/github`이 아니라 `/api/v1/webhooks/github`).

### Webhook은 발사되지만 스캔이 나타나지 않음

전송은 수락되었지만 트리거되지 않은 경우. 가능한 이유:

- 푸시가 프로젝트의 default branch가 아닌 곳으로 감. 포털은 default branch만 스캔합니다(프로젝트별 구성 — [Projects](../user-guide/projects.md) 참고).
- PR head SHA가 이전 스캔의 커밋과 동일(예: 같은 SHA를 재사용한 force-push). 포털은 SHA로 디듀플리케이션합니다.

### 포털 장애 후 옛 전송이 replay됨

GitHub과 GitLab 모두 미전송 이벤트를 ~24시간 큐잉합니다. 포털이 복구되면 전송이 재생됩니다. 위 멱등성이 중복 스캔을 막아 줍니다. 재생을 건너뛰려면 포털을 다시 띄우기 전 Git 호스트에서 큐를 수동으로 비우세요 — 다만 대부분의 설치는 장애 동안 발생한 이벤트를 잡기 위해 재생의 이점을 봅니다.

### GitLab에서 HMAC을 원함

GitLab Webhook을 작은 proxy(예: Lua 스니펫이 들어간 nginx, 또는 작은 Cloudflare Worker)를 통해 보내 HMAC 헤더를 추가하세요. 포털 측에서 커스텀 미들웨어로 강제하도록 구성. 기본이 아니며 번들 배포의 범위를 벗어납니다.

## 함께 보기

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [API keys](../admin-guide/api-keys.md)
- [감사 로그](../admin-guide/audit-log.md)
