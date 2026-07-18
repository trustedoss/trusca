---
id: integrations
title: 통합
description: /integrations 페이지에서 CI 러너용 API Key를 발급하고 GitHub·GitLab Webhook을 구성합니다.
sidebar_label: 통합
sidebar_position: 9
---

# 통합

`/integrations`는 **비대화형 자격증명**을 다루는 사용자용 허브입니다. 두 가지 서로 다른 항목을 묶습니다.

- **API Key** — CI 러너·스크립트·외부 서비스가 포털 API에 인증할 때 사용하는 자격증명.
- **Webhook** — GitHub와 GitLab이 저장소 이벤트(push, pull request)를 푸시하기 위해 포털이 노출하는 인바운드 URL.

:::note 대상 독자
조회는 `developer`, 팀 범위 API Key 발급·폐기는 `team_admin`, 조직 범위 Key 발급은 `super_admin`. 페이지는 본인이 수행할 수 없는 동작을 숨깁니다.
:::

## API Key

`/integrations`를 열고 **API keys** 섹션으로 스크롤합니다. 목록은 본인이 관리할 수 있는 모든 Key를 표시합니다 — 라벨, prefix, scope, 만료, 마지막 사용 메타데이터.

![통합 — Create 버튼과 Key 표가 있는 API keys 섹션](/img/screenshots/user-integrations-keys.png)

### Key 생성

1. **New API key**를 클릭합니다. 다이얼로그에서 이름과 scope를 입력하고 제출하면 Key가 발급됩니다.

   ![통합 — name과 scope 입력이 있는 API key 생성 다이얼로그](/img/screenshots/user-integrations-key-create.png)

2. 폼을 채웁니다.
   - **Name** — Key 용도를 떠올리게 하는 자유 텍스트(예: `github-action-checkout-service`).
   - **Scope** — `org`, `team`, `project`. 낮은 scope가 더 엄격합니다. 필요한 호출을 커버하는 가장 작은 scope를 선택하세요. 폼에는 `team_id`(scope=`team`일 때 필수)와 `project_id`(scope=`project`일 때 필수)를 위한 평문 UUID 입력란이 있습니다. 해당 admin 페이지에서 ID를 복사해 넣으세요.

   각 scope 발급 권한:

   | Scope    | 발급 가능 주체            |
   |----------|---------------------------|
   | `org`    | super-admin 만            |
   | `team`   | super-admin, team-admin   |
   | `project`| super-admin, team-admin, developer (소속 팀의 프로젝트 한정) |

3. **Create**를 클릭합니다.

:::caution 현재 릴리스에서는 Key가 만료되지 않음
Key 생성 폼이 아직 만료를 받지 않습니다. 현재 릴리스에서 발급된 모든 Key는 명시적으로 **Revoke** 할 때까지 유효합니다. 다른 장기 시크릿과 동일하게 취급하세요 — CI 시크릿 매니저에 보관하고 절대 소스 컨트롤에 두지 마세요.
:::

포털은 전체 Key가 담긴 **1회 노출 모달**을 엽니다.

```text
tos_a1b2c3d4_eaff8b91d36c5e0a2f1c4d7e8a9b0c2d
```

:::caution One-time reveal
전체 Key는 **단 한 번만** 표시됩니다. 모달을 닫으면 prefix만 보입니다. 지금 복사해서 CI의 시크릿 저장소에 붙여 넣은 다음 **Done**을 클릭하세요.
:::

모달에는 **Copy** 버튼과 명시적 경고가 있습니다 — *"This is the only time you will see the full key. If you lose it, you must create a new one."*

### Key 사용

모든 요청의 `Authorization` 헤더에 Key를 `Bearer` 스킴으로 전달하세요. API Key는 CI 영역 — **스캔 트리거**와 **상태 폴링** — 을 인증합니다. 대화형 읽기 엔드포인트에서는 받지 않습니다(예: `GET /v1/projects`는 JWT 전용이며 Key에는 `401`을 반환합니다).

<!-- docs-uat: id=integrations-api-trigger-scan kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind":"source"}' \
  https://trustedoss.example.com/v1/projects/<project-id>/scans
```

**GitHub Actions**에서는 Key를 저장소 또는 조직 시크릿에 보관하고 환경 변수로 노출합니다.

```yaml
- name: Trigger TRUSCA scan
  env:
    TRUSTEDOSS_API_KEY: ${{ secrets.TRUSTEDOSS_API_KEY }}
  run: curl -sS -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" ...
```

**Jenkins**에서는 **Credentials** 플러그인(Secret text)을 사용해 stage 안에서 바인딩합니다.

```groovy
stage('Scan') {
  withCredentials([string(credentialsId: 'trustedoss-api-key', variable: 'TRUSTEDOSS_API_KEY')]) {
    sh 'curl -sS -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" ...'
  }
}
```

### Key 폐기

API Key 목록에서 행에 마우스를 올리고 **Revoke**를 클릭한 뒤 다이얼로그에서 확인합니다. 폐기는 즉시 적용되며(인증 캐시 TTL ~5초) 되돌릴 수 없습니다.

## Webhook

**Webhooks** 섹션으로 스크롤합니다. API Key와 달리 Webhook URL은 **고정**입니다 — 포털이 well-known 경로로 노출하며, 공급자(GitHub / GitLab)에서 그 URL로 요청을 보내도록 연결합니다.

![통합 — GitHub와 GitLab URL 카드가 있는 Webhooks 섹션](/img/screenshots/user-integrations-webhooks.png)

### GitHub

GitHub에 등록할 URL — `https://<your-host>/v1/webhooks/github`.

- **Content-Type:** `application/json`.
- **Signature:** `X-Hub-Signature-256`. 프로젝트별 `webhook_secret`을 키로 raw body에 대해 HMAC-SHA256.
- **Events:** `push`와 `pull_request`가 지원되는 트리거.

포털은 인바운드 전송을 검증하기 위해 프로젝트별 `webhook_secret` 필드를 저장합니다. 해당 시크릿을 생성·회전하는 UI는 현재 릴리스에서 노출되지 않습니다 — [로드맵](#로드맵) 참고. 현재는 운영자가 서버 측에서 시크릿을 부트스트랩합니다.

### GitLab

GitLab에 등록할 URL — `https://<your-host>/v1/webhooks/gitlab`.

- **Content-Type:** `application/json`.
- **Token:** `X-Gitlab-Token` 헤더로 전송. 값을 프로젝트의 `webhook_secret`으로 설정.
- **Events:** **Push events**와 **Merge request events**.

## 정상 동작 확인

<!-- docs-uat: id=integrations-curl-200 kind=manual tier=manual -->
- Key 생성 후 그 Key로 스캔을 트리거하세요 — `curl -sS -X POST -H "Authorization: Bearer <key>" -H "Content-Type: application/json" -d '{"kind":"source"}' .../v1/projects/<project-id>/scans` — 새 스캔과 함께 `200` 응답이 오는지 확인합니다. 이어서 같은 Key로 `GET .../v1/scans/<scan-id>`를 폴링합니다. (`GET /v1/projects`는 JWT 전용이라 Key에는 `401`을 반환합니다 — 설정 오류가 아니라 정상 동작입니다.)
<!-- docs-uat: id=integrations-github-webhook-202 kind=manual tier=manual -->
- GitHub에 Webhook 등록 후 커밋을 푸시하고 GitHub의 **Webhook deliveries** 뷰에서 HTTP 200 성공 전송을 확인하세요.
<!-- docs-uat: id=integrations-audit-events kind=manual tier=manual -->
- super-admin이 `/admin/audit`에서 `target_table=api_keys&action=create`와 `target_table=webhook_deliveries&action=create` 이벤트를 확인할 수 있습니다. 현재 감사 로그는 super-admin 전용입니다 — [로드맵](#로드맵) 참고.

## 트러블슈팅

- **API에서 HTTP 401** — 자격증명 문제(헤더 없음, 잘못된 Bearer 형식, 알 수 없는 prefix, 서명 불일치, 폐기됨, 만료됨).
- **API에서 HTTP 403** — 자격증명은 유효하지만 Key 의 scope 가 리소스를 커버하지 않음(예: `team`-scope Key 가 `org` 전용 엔드포인트 호출). 더 넓은 scope 의 Key 를 새로 발급하거나 다른 엔드포인트를 호출하세요.
- **API에서 HTTP 429** — Key별 레이트 리밋에 도달. `Retry-After` 헤더가 대기 시간을 알려줍니다. 백오프 후 재시도.
- **GitHub Webhook이 401** — `X-Hub-Signature-256` 검증 실패. 시크릿 일치 여부와 GitHub가 **raw** body 기준으로 HMAC을 계산하는지(재직렬화된 JSON이 아님) 확인.
- **GitLab Webhook이 401** — `X-Gitlab-Token` 헤더 값이 프로젝트 `webhook_secret`과 일치하지 않음.

## 로드맵

매뉴얼이 이전에 약속했으나 v0.10.0에 포함되지 않은 항목.

- API Key 만료 프리셋(30 / 90 / 180 / 365일, 커스텀) — 예정. 현재 발급된 모든 Key는 폐기 전까지 만료되지 않습니다.
- Key 생성 폼의 팀/프로젝트 선택기 — 예정. 현재 폼은 평문 UUID 입력을 받습니다.
- **Project Settings → CI/CD** 서브탭과 **Rotate webhook secret** 동작 — 예정. 현재 프로젝트별 `webhook_secret`은 서버 측에서 부트스트랩됩니다.
- `team_admin`을 위한 팀 범위 감사 로그(`/audit`) — 예정. 현재 감사 로그는 super-admin 전용 (`/admin/audit`).

## 함께 보기

- [인증과 프로필](./auth-and-profile.md) — 사람을 위한 대화형 자격증명.
- [GitHub Actions](../ci-integration/github-actions.md) — 종단 간 CI 통합.
- [Webhooks (admin reference)](../ci-integration/webhooks.md) — 페이로드 스키마와 admin 측 구성.
- [API keys (admin reference)](../admin-guide/api-keys.md) — 백엔드 동작·해싱·감사 로그.
