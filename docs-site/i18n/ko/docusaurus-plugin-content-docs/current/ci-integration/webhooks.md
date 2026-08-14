---
id: webhooks
title: Webhooks
description: GitHub과 GitLab Webhook을 구성해 push와 PR/MR 이벤트로 TRUSCA 스캔을 트리거합니다 — HMAC 서명 검증 포함.
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
| GitHub | `POST https://trustedoss.example.com/v1/webhooks/github` | `X-Hub-Signature-256`의 HMAC-SHA256 서명. |
| GitLab | `POST https://trustedoss.example.com/v1/webhooks/gitlab` | `X-Gitlab-Token`의 토큰. |

두 엔드포인트 모두 공개(JWT 없음)이지만 프로젝트의 webhook secret을 요구합니다. 시크릿은 프로젝트별이며 Webhook 활성화 시 생성됩니다.

### Webhook 시크릿 부트스트래핑 (v0.10.0 에서는 운영자 전용)

Project Settings 탭은 아직 Webhook 컨트롤을 노출하지 않습니다.
운영자가 데이터베이스에서 직접 시크릿을 설정합니다.

<!-- docs-uat: id=webhooks-secret-sql kind=sql ctx=postgres tier=manual waiver=operator-sql-placeholder-project-uuid -->
```sql
UPDATE projects
   SET webhook_secret = encode(gen_random_bytes(32), 'base64')
 WHERE id = '<project-uuid>'
RETURNING webhook_secret;
```

`RETURNING`이 생성된 시크릿을 출력하므로 따로 조회할 필요가 없습니다. 이 값을
레포 소유자에게 전달해 GitHub/GitLab → Settings → Webhooks → "Secret"에 붙여
넣게 하세요.

이 컬럼은 시크릿을 그대로 저장합니다. 포털이 전송을 검증할 때 쓰는 HMAC 키라
해시할 수 없기 때문입니다. 데이터베이스 덤프를 다룰 때 이 점을 감안하시고,
교체할 때는 위 문장을 다시 실행하면 됩니다.

셀프 서비스 활성화 UI는 로드맵 항목입니다.

## 셋업 — GitHub

### 1. 포털에서 Webhook 활성화

현재 릴리스에서 Webhook 활성화는 운영자 전용입니다. Project Settings 탭은 아직 Webhook 컨트롤을 노출하지 않습니다. 운영자는 서버 측에서 프로젝트별 `webhook_secret`을 부트스트랩하고(`apps/backend/services/webhook_service.py` 참고), 생성된 Webhook URL은 **Integrations** 페이지 → Webhooks 섹션에 표시됩니다. 셀프 서비스 활성화 UI는 로드맵에 있습니다.

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

현재 릴리스에서 Webhook 활성화는 운영자 전용입니다. Project Settings 탭은 아직 Webhook 컨트롤을 노출하지 않습니다. 운영자는 서버 측에서 프로젝트별 `webhook_secret`을 부트스트랩하고(`apps/backend/services/webhook_service.py` 참고), 생성된 Webhook URL은 **Integrations** 페이지 → Webhooks 섹션에 표시됩니다. 셀프 서비스 활성화 UI는 로드맵에 있습니다.

### 2. GitLab에서 구성

1. 프로젝트 → **Settings → Webhooks → Add new webhook**.
2. **URL** — 전송 URL.
3. **Secret token** — 포털에서 복사한 토큰.
4. **Trigger** — 체크
   - Push events
   - Merge request events
5. **SSL verification** — enabled.
6. **Add webhook**.

**Test → Push event** 버튼으로 연결을 검증합니다. 포털은 전송을 기록하고 결과를 담은 JSON 본문과 함께 200으로 응답합니다.

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

## 요청 한도

두 수신 엔드포인트는 공개입니다. 서명이 본문 전체를 덮기 때문에 본문을 읽고 저장소를 찾은 뒤에야 호출자가 누구인지 판단할 수 있습니다. 그 앞에 놓인 작업량을 두 가지 한도로 제한합니다.

| 설정 | 기본값 | 거부 코드 |
|---|---|---|
| `WEBHOOK_MAX_BODY_BYTES` | 2 MiB | `413` |
| `WEBHOOK_RATE_LIMIT` | 출발 IP당 `120/minute` | `429` |

실제 전송은 두 한도보다 훨씬 작습니다. GitHub은 푸시 페이로드의 커밋 목록을 잘라 보내고 25 MB를 넘으면 아예 전송하지 않으며, 한 주소에서 분당 120건은 바쁜 조직이 보내는 양보다 많습니다.

`skipped_*` 상태와 달리 이 둘은 오류입니다. 전송이 기록되지 않고 스캔도 일어나지 않습니다. 두 Git 호스트 모두 4xx를 스스로 재시도하지 않으므로, 여기서 거부된 전송은 한도를 올린 뒤 호스트에서 수동으로 재전송해야 합니다. 규모가 큰 설치에서 한도에 걸린다면 이벤트를 잃기보다 `WEBHOOK_RATE_LIMIT`을 올리세요. 서명을 확인하기 전에 알 수 있는 신원은 IP뿐이라 IP를 기준으로 삼았고, Git 호스트는 모든 저장소의 이벤트를 같은 주소 대역에서 보냅니다.

## 멱등성

두 Git 호스트 모두 실패 시 전송을 재시도합니다. 포털은 `delivery_id` 디듀플리케이션으로 반복을 처리합니다.

- GitHub은 `X-GitHub-Delivery`(전송별 UUID)를 제공.
- GitLab은 `X-Gitlab-Webhook-UUID`(전송별 UUID)를 제공합니다. 이 헤더를 보내지 않는 구버전에서는 포털이 페이로드에서 대체 식별자를 만들어 쓰는데, 이쪽이 더 성깁니다. [트러블슈팅](#같은-머지-리퀘스트에-두-번째-푸시가-스캔되지-않음)을 보세요.

포털은 unique 인덱스가 걸린 `webhook_deliveries`에 `(source, delivery_id)`를 저장합니다. 중복 전송은 두 번째 스캔을 트리거하는 대신 200과 `{"status": "duplicate"}`로 응답합니다. 호스트 측 재시도 폭풍에서도 시스템이 멱등합니다.

## 스캔을 트리거하는 이벤트

| 이벤트 | 동작 |
|---|---|
| GitHub `push` — 모든 브랜치·태그 | 그 ref를 키로 `source` 스캔을 트리거합니다. |
| GitHub `pull_request` — `opened`, `synchronize`, `reopened` | `pr-<번호>`를 키로 `source` 스캔을 트리거합니다. |
| GitLab `Push Hook` — 모든 브랜치·태그 | GitHub `push`와 같습니다. |
| GitLab `Merge Request Hook` — `open`, `reopen`, `update` | `mr-<iid>`를 키로 `source` 스캔을 트리거합니다. |

다른 이벤트는 수락되지만(200) 스캔을 트리거하지 않습니다. 위 목록 밖의 풀 리퀘스트 action도 마찬가지입니다. `closed`, `labeled`, `assigned` 같은 것들은 의존성 구성을 바꿀 수 없기 때문입니다. 수락된 전송은 모두 `webhook_deliveries`에 기록되고, 감사 리스너가 이를 `action=create`, `target_table=webhook_deliveries`로 남깁니다.

바쁜 저장소에 적용하기 전에 알아 둘 것이 둘 있습니다.

브랜치 필터가 없습니다. 어느 브랜치나 태그로 푸시해도 스캔이 큐에 들어가므로, 여러 브랜치를 한꺼번에 푸시하면 ref마다 하나씩 쌓입니다. 이 양이 부담되면 Git 호스트 쪽에서 원하는 이벤트만 선택하세요.

스캔은 자신을 트리거한 ref를 체크아웃합니다. 푸시된 브랜치이거나 풀 리퀘스트의 merge ref입니다. 그래서 의존성을 추가한 풀 리퀘스트는 그 이벤트가 만든 스캔에 그대로 보입니다.

워커가 처리할 시점에 그 ref가 사라졌다면(스캔이 큐에서 대기하는 동안 풀 리퀘스트가 머지되거나 force-push된 경우) 스캔은 원격의 기본 브랜치로 넘어가고, 원하던 ref와 fetch 실패 사유를 `metadata.ref_fallback`에 기록합니다. 이렇게 넘어간 스캔의 판정은 요청한 것과 다른 코드를 설명하므로, 그 스캔을 풀 리퀘스트에 대한 판단으로 읽기 전에 이 필드를 확인하세요.

## 응답 status가 뜻하는 것

| `status` | 뜻 |
|---|---|
| `enqueued` | 스캔이 만들어졌습니다. `scan_id`가 그 스캔입니다. |
| `duplicate` | 이미 기록된 전송 id입니다. Git 호스트의 재시도로 같은 전송이 다시 온 것이며, 아무것도 하지 않은 것이 맞습니다. |
| `ignored` | 스캔 대상 이벤트가 아닙니다. 목록 밖의 종류이거나, 의존성을 바꿀 수 없는 풀 리퀘스트 action입니다. |
| `skipped_active_scan` | 새 전송이고 스캔 대상이었지만, 그 ref에 이미 대기 중이거나 실행 중인 스캔이 있어 두 번째를 만들지 않았습니다. |
| `skipped_team_at_capacity` | 소유 팀이 동시 스캔 상한(`SCAN_CONCURRENCY_CAP_PER_TEAM`)에 도달했습니다. |
| `skipped_disk_full` | 작업 볼륨이 `DISK_HARD_LIMIT_PCT`를 넘었습니다. 운영자 조치가 필요합니다. |

`skipped_`로 시작하는 값은 모두 어떤 커밋이 스캔되지 않았다는 뜻이므로 눈여겨보세요.

`skipped_active_scan`은 흔한 쪽입니다. 포털은 `(project, ref)` 하나당 진행 중인 스캔을 하나만 허용하는데, 이미 돌고 있던 스캔은 더 이전 커밋에서 시작된 것이기 때문입니다. 활발한 브랜치라면 다음 전송이 정상적으로 스캔하므로 저절로 따라잡히지만, 오래 걸리는 스캔의 끝자락에 도착한 푸시는 그 자체로는 스캔되지 않습니다. 그 커밋을 꼭 확인해야 한다면 Git 호스트에서 재전송하세요.

나머지 둘은 용량 신호이고, 다른 조치에 앞서 운영자가 먼저 손을 써야 합니다. `skipped_team_at_capacity`는 팀이 이미 `SCAN_CONCURRENCY_CAP_PER_TEAM`만큼 스캔을 돌리고 있다는 뜻이니 상한을 올리거나 실행 중인 스캔이 끝나기를 기다리세요. `skipped_disk_full`은 작업 볼륨이 `DISK_HARD_LIMIT_PCT`를 넘어 공간을 확보하기 전까지 아무것도 스캔되지 않는다는 뜻입니다.

상황이 해소된 뒤 Git 호스트에서 그 이벤트를 재전송하면 정상적으로 스캔됩니다. 전송은 사유와 함께 기록되고, 재전송은 그 행을 덮어쓰며 중복으로 읽히지 않습니다. 식별자는 한 번 쓰면 없어지는 번호가 아니라 그 전송의 현재 상태를 가리킵니다.

이제 중복 검사가 먼저 돕니다. 이미 스캔된 전송을 다시 보내면 용량 상황과 무관하게 `duplicate`로 답하고, 그 전송에 기록된 결과는 계속 `enqueued`로 남습니다. 다시 실행되는 것은 스캔이 시작된 적 없는 전송뿐이고, 그쪽이 바로 다시 실행할 이유가 있는 경우입니다.

이들을 오류가 아니라 `200`으로 답하는 것은 의도된 것입니다. 4xx나 5xx를 주면 Git 호스트가 재시도하는데, 이미 한계에 있는 포털에 재시도가 몰려 봐야 도움이 되지 않습니다.

## 정상 동작 확인

Webhook 구성 후:

<!-- docs-uat: id=webhooks-ping-delivery kind=manual tier=manual -->
1. Git 호스트의 Webhook 페이지가 **ping / test** 전송 성공을 표시.
<!-- docs-uat: id=webhooks-push-creates-scan kind=manual tier=manual -->
2. 커밋 푸시 시 포털에 30초 내 새 스캔이 생성됨.
<!-- docs-uat: id=webhooks-audit-deliver kind=manual tier=manual -->
3. 감사 로그에 `webhook_deliveries`에 대한 `create`가 전송 id와 이벤트 종류를 담고 남습니다.

## 트러블슈팅

### "Could not deliver: 401 Unauthorized"

서명이 일치하지 않거나, 그 저장소가 이 포털에 설정되어 있지 않습니다. 두 경우 모두 같은 본문의 401로 답하는데, 이는 의도된 것입니다. 그렇지 않으면 인증 없는 호출자가 상태 코드만 보고 이 포털이 어떤 저장소를 보고 있는지 알아낼 수 있습니다. 원인은 이렇습니다.

- 그 저장소에 해당하는 프로젝트가 없거나, 프로젝트에 `webhook_secret`이 설정되지 않았습니다([Webhook 시크릿 부트스트래핑](#webhook-시크릿-부트스트래핑-v0100-에서는-운영자-전용) 참고).
- 포털에서 시크릿을 교체했지만 Git 호스트에 갱신하지 않았습니다.
- 포털 앞단의 proxy가 body를 수정합니다(압축, JSON 재직렬화). 서명은 raw 바이트 기준이라 1바이트만 달라져도 무효가 됩니다.

서버 로그에는 이 둘이 구분되어 남습니다. `webhook.unknown_repository`는 매칭에 실패한 URL을, `webhook.github.signature_invalid`는 매칭된 프로젝트를 함께 기록합니다. URL 오타인지 오래된 시크릿인지는 백엔드 로그에서 확인하세요.

재동기화: 포털에서 시크릿을 교체하고, 새 값을 Git 호스트에 붙여넣은 뒤 redelivery를 트리거하세요.

### "Could not deliver: 404 Not Found"

보통은 전송 URL 자체가 틀린 경우입니다. `/api/` 누락, `/v1/` 누락, 백엔드 대신 프런트엔드 적중(`/webhooks/github`이 아니라 `/v1/webhooks/github`)이 흔합니다. 포털도 페이로드에 알아볼 수 있는 저장소 URL이 아예 없으면 404로 답하는데, 이 경우는 본문이 잘못됐거나 손으로 만든 요청이라는 뜻입니다. 설정되지 않은 저장소는 404가 아니라 401로 답합니다.

### Webhook은 발사되지만 스캔이 나타나지 않음

전송은 수락되었지만 트리거되지 않은 경우입니다. 가능한 이유는 이렇습니다.

- 같은 ref의 스캔이 이미 대기 중이거나 실행 중입니다. 전송은 받아들이되 두 번째 스캔을 만들지 않으며, 응답은 `{"status": "skipped_active_scan"}`입니다.
- 이벤트 종류가 스캔 화이트리스트 밖입니다(GitHub은 `push`와 `pull_request`, GitLab은 `Push Hook`과 `Merge Request Hook`). `ping`은 수락되고 기록되지만 스캔하지 않습니다.
- 페이로드의 저장소 URL이 어느 프로젝트의 `git_url`과도 맞지 않거나, 그 프로젝트에 Webhook 시크릿이 없습니다. 어느 쪽이든 200이 아니라 401로 답합니다. 위 항목을 참고하세요.
- 팀이 동시 스캔 상한에 도달했거나 작업 볼륨이 가득 찼습니다(`skipped_team_at_capacity` / `skipped_disk_full`). 스캔이 시작된 적 없는 전송이므로, 운영자가 상황을 해소한 뒤 재전송하면 정상적으로 스캔됩니다.

### 같은 머지 리퀘스트에 두 번째 푸시가 스캔되지 않음

`X-Gitlab-Webhook-UUID`를 보내지 않는 GitLab 버전에서는 포털이 머지 리퀘스트의 id와 head 커밋 SHA를 합쳐 전송 식별자를 만듭니다. 브랜치가 움직이면 식별자도 함께 바뀝니다. 같은 머지 리퀘스트에 같은 커밋으로 전송이 두 번 오면(새 푸시가 아닌 재알림) 두 번째는 `duplicate`가 되며, 이는 의도된 동작입니다.

### 포털 장애 후 옛 전송이 replay됨

GitHub과 GitLab 모두 미전송 이벤트를 ~24시간 큐잉합니다. 포털이 복구되면 전송이 재생됩니다. 위 멱등성이 중복 스캔을 막아 줍니다. 재생을 건너뛰려면 포털을 다시 띄우기 전 Git 호스트에서 큐를 수동으로 비우세요 — 다만 대부분의 설치는 장애 동안 발생한 이벤트를 잡기 위해 재생의 이점을 봅니다.

### GitLab에서 HMAC을 원함

GitLab Webhook을 작은 proxy(예: Lua 스니펫이 들어간 nginx, 또는 작은 Cloudflare Worker)를 통해 보내 HMAC 헤더를 추가하세요. 포털 측에서 커스텀 미들웨어로 강제하도록 구성. 기본이 아니며 번들 배포의 범위를 벗어납니다.

## 함께 보기

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [API keys](../admin-guide/api-keys.md)
- [감사 로그](../admin-guide/audit-log.md)
