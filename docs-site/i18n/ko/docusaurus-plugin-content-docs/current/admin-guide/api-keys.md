---
id: api-keys
title: API Key
description: 서비스 계정과 CI 통합을 위한 API Key 발급·scope·회전.
sidebar_label: API Key
sidebar_position: 6
---

# API Key

API Key는 **비대화형** 클라이언트(CI 러너·Webhook·스크립트·GitHub Action)를 위한 자격증명입니다. 사용자의 JWT 세션을 소비하지 않고 머신 간 트래픽을 인증합니다.

:::note 대상 독자
팀 범위 Key를 발급하는 `team_admin`, 조직 범위 Key를 발급하는 `super_admin`.
:::

## /integrations UI 로 관리하기

대부분의 사용자는 [통합 페이지](../user-guide/integrations.md)에서 본인의 Key를 직접 발급·폐기합니다. `/integrations` UI는 다음을 제공합니다.

- 로그인한 사용자가 관리할 권한이 있는 모든 Key 나열.
- **Create** 시 1회 노출 모달과 클립보드 복사 버튼, 전체 Key가 단 한 번만 표시된다는 강한 경고.
- 행별 **Revoke**와 확인 다이얼로그 — 폐기는 ~5초 내에 전파.

![/integrations — admin이 Key를 발급·폐기하는 API keys 섹션](/img/screenshots/user-integrations-keys.png)

생성 다이얼로그는 `team_admin`과 `super_admin`이 동일한 화면을 사용합니다. super-admin의 경우 scope 드롭다운에 `org`가 추가됩니다.

![/integrations — Label과 scope 입력이 있는 API key 생성 다이얼로그](/img/screenshots/user-integrations-key-create.png)

이 페이지는 **서버 측 동작** — Key 형태, 해싱, scope 시멘틱, 감사 로그, 회전 전략을 다룹니다. CI에 Key를 연결하기만 하면 되는 사용자는 [통합 사용자 가이드](../user-guide/integrations.md)에서 멈춰도 됩니다.

## Key 형태

```
tos_<8-char-prefix>_<32-char-secret>
```

예시: `tos_a1b2c3d4_eaff8b91d36c5e0a2f1c4d7e8a9b0c2d`.

- **`tos_`** — 고정 prefix.
- **`<8-char-prefix>`** — 랜덤이며 **공개**. 조회와 표시 라벨에 사용. 감사 로그에 노출.
- **`<32-char-secret>`** — 랜덤이며 **비공개**. 서버에는 bcrypt 해시로만 저장. 전체 Key는 운영자에게 생성 시 **단 한 번** 표시되며 그 이후로는 절대 보이지 않음.

조회는 prefix 전반에서 상수 시간이며, secret 비교는 타이밍 공격을 막기 위해 `bcrypt.checkpw`를 사용합니다.

## Scope 모델

각 Key는 권한 경계를 결정하는 단일 **리소스 scope**를 가집니다.

- **`org`** — 조직 전체에서 행위하며, 발급자가 호출할 수 있던 어떤 endpoint 든 호출 가능.
- **`team`** — 특정 팀을 대신해 행위하며, 팀 간 호출은 403으로 실패.
- **`project`** — 특정 프로젝트에 묶이며, 해당 프로젝트 외의 호출은 403으로 실패.

각 scope 발급 권한:

| Scope    | 발급 가능 주체            |
|----------|---------------------------|
| `org`    | super-admin 만            |
| `team`   | super-admin, team-admin   |
| `project`| super-admin, team-admin, developer (소속 팀의 프로젝트 한정) |

Key는 요청 시점에 **발급한 사용자의 역할**을 상속합니다 — v0.10.0에는 별도의 "effective role"이나 "allowed actions" 목록이 없습니다. 권한 검사는 JWT로 인증된 요청과 동일한 RBAC 코드 경로를 따릅니다.

Key는 현재 릴리스에서 **만료되지 않습니다**. 수동으로 폐기될 때까지 유효합니다. 키별 만료 프리셋과 세분화된 `allowed_actions` taxonomy(`scan:trigger`, `scan:read`, `report:download`, …)는 로드맵입니다.

## Key 발급

### Team admin 으로

1. **/integrations**(`team_admin` 이상 사용 가능한 최상위 사이드바 항목)을 엽니다.
2. **API keys** 탭으로 전환.
3. **New API key** 클릭.
4. 채우기:
   - **Label**(예: `github-action-checkout-service`)
   - **Scope** — `team`(기본) 또는 `project`
   - **Project** — scope가 `project`일 때 필수
5. **Create**.

전체 Key는 모달에서 **단 한 번** 표시됩니다. 복사해 CI 시크릿 저장소(GitHub secrets, GitLab CI variables, Jenkins credentials)에 보관하세요. 모달을 닫으면 UI에서는 prefix만 보이고 전체 Key는 복구 불가입니다.

### Super-admin 으로

같은 **/integrations** 흐름이지만, 팀 경계를 넘는 Key는 scope를 `org`로 설정할 수 있습니다(드묾 — 대부분의 CI 통합은 `team` 또는 `project` scope에 머물러야 함).

## API Key 사용

`Authorization` 헤더에 Bearer 토큰으로 Key를 전달:

<!-- docs-uat: id=apikeys-use-curl kind=shell ctx=host tier=nightly waiver=example-host-and-placeholder-bearer-token -->
```bash
curl -sS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  https://trustedoss.example.com/v1/projects
```

포털은 추적성을 돕기 위해 모든 요청에 prefix를 로깅합니다.

## 회전

### 회전 사유

- **침해** — Key가 공개 레포에 커밋되었거나 CI 러너가 침해됨. **즉시 폐기.**
- **인사 변경** — Key를 발급한 team admin이 떠나는 경우. 새 Key 발급 후 CI 시크릿 교체, 옛 Key 폐기.
- **정책** — 분기별 회전을 심층 방어 조치로 진행.

### 무중단 회전 방법

1. 같은 scope로 **새 Key 발급**.
2. CI 시크릿을 **새 Key로 갱신**.
3. 한 번의 CI 사이클을 **기다려** 새 Key 동작 확인.
4. 옛 Key를 **폐기**.

폐기 후 ~5초 이내(인증 캐시 TTL)에 옛 Key가 거절됩니다.

## 폐기

1. **/integrations → API keys** → Key 행 → **Revoke**.
2. 확인.

폐기는 즉시이며 되돌릴 수 없습니다. Key를 되살리려면 새로 발급하세요.

## Key 목록

UI는 라벨, prefix, scope(`org` / `team` / `project`), 발급자, 생성 시각, 마지막 사용 시각, 폐기 상태를 표시합니다. 기존 Key의 secret을 복구할 방법은 없습니다 — 의도된 설계입니다. 키별 역할, 허용 동작, 만료, 마지막 사용 IP 컬럼은 로드맵입니다(해당 모델 컬럼들이 아직 없음).

## 감사 로그

Key 라이프사이클 이벤트가 로그됩니다.

- `target_table=api_keys&action=create` — Key 행이 insert될 때 ORM 리스너가 발신(actor, target prefix, scope).
- `api_key.revoked` — 명시적 폐기 시 API Key 서비스가 발신하는 **structlog 이벤트 전용**(actor, target prefix). v0.10.0 에서는 `audit_logs` 행을 **생성하지 않습니다**. 단, ORM 리스너가 `revoked_at` 변경에 대한 `api_keys.update` 행을 여전히 기록하므로 폐기 자체는 감사 테이블에 캡처됩니다 — 다만 structlog 이벤트 이름이 아닌 `target_table=api_keys&action=update` 로 기록됩니다.

현재 릴리스에서는 API Key 인증의 요청별 감사 행이 발행되지 않습니다(`api_key.use` 이벤트는 로드맵). API Key 요청으로 생성되는 감사 행은 도메인 액션(예: `target_table=scans&action=create`)을 그대로 기록하며, Key의 prefix는 요청에 대한 구조화된 로그에 캡처되지만 감사 행의 `actor_user_id`는 발급한 사용자입니다(Key 자체가 아님).

## Webhook 시크릿 vs API Key

이 둘은 호환되지 않습니다. 포털은 다음을 구분합니다.

- **API Key** — CI 클라이언트에서 포털 API로 향하는 아웃바운드.
- **Webhook 시크릿** — Webhook의 인바운드 HMAC 서명 검증에 사용(GitHub `X-Hub-Signature-256`, GitLab `X-Gitlab-Token`).

Webhook 흐름은 [Webhooks](../ci-integration/webhooks.md) 참고.

## 정상 동작 확인

Key 발급 후:

<!-- docs-uat: id=apikeys-verify-curl kind=manual tier=manual -->
1. `curl -sS -H "Authorization: Bearer <key>" .../v1/projects`가 200과 팀 프로젝트를 반환.
<!-- docs-uat: id=apikeys-verify-audit-row kind=manual tier=manual -->
2. 감사 로그가 prefix와 함께 `target_table=api_keys&action=create` 행을 기록. Admin UI 에서는 `target_table=api_keys` 로 필터링할 수 없습니다 — `api_keys` 가 `AuditTargetTable` 화이트리스트에 없기 때문입니다([감사 로그 → 필터 노출 vs raw 행 테이블](./audit-log.md#무엇이-기록되는가) 참고). 검증은 raw SQL 로:

   ```sql
   SELECT * FROM audit_logs
    WHERE target_table = 'api_keys'
      AND action = 'create'
      AND created_at > now() - interval '1 hour'
    ORDER BY created_at DESC;
   ```

<!-- docs-uat: id=apikeys-verify-ci-build kind=manual tier=manual -->
3. Key를 소비하는 CI 빌드가 첫 실행에 성공.

## 트러블슈팅

### 방금 만든 Key로 401

가장 흔한 두 가지 원인:

- Key가 앞뒤 공백과 함께 복사됨. 원본 모달에서 다시 붙여넣기 — Key는 정확히 `tos_` + 8 + `_` + 32자.
- 포털은 두 실패 모드를 구분합니다:
  - **401** = 자격증명 문제(헤더 없음, 잘못된 Bearer 형식, 알 수 없는
    prefix, 서명 불일치, 폐기됨, 만료됨).
  - **403** = 자격증명은 유효하지만 Key 의 scope 가 리소스를 커버하지
    않음(예: `team`-scope Key 가 `org` 전용 엔드포인트 호출).

### "Key prefix exists but secret does not match"

누군가 secret을 brute-force 시도했거나 잘못된 형식의 Key가 전송됐습니다. 포털은 모든 미스를 구조화된 백엔드 로그에 기록합니다. brute-force 감지(단일 Key가 분당 N회 미스를 넘으면 Slack 알림)는 로드맵입니다 — 그때까지는 백엔드 로그에서 반복되는 `secret_mismatch` 라인을 주기적으로 grep하세요:

<!-- docs-uat: id=apikeys-secret-mismatch-grep kind=shell ctx=host tier=nightly waiver=production-compose-log-grep-diagnostic -->
```bash
docker-compose -f docker-compose.yml logs --tail=2000 backend \
  | grep secret_mismatch | sort | uniq -c | sort -rn | head
```

단일 prefix가 반복되면 즉시 폐기 후 회전.

### 로컬에선 동작하는데 CI에선 안 됨

확인 사항:

- CI 시크릿이 적절한 환경 / 브랜치에 설정되어 있는지.
- 러너의 아웃바운드 IP가 포털 방화벽에 차단되지 않았는지(일부 설치는 사무실 IP만 화이트리스트).
- CI 트래픽이 통과하는 reverse proxy에서 `Authorization` 헤더가 보존되는지.

## Roadmap

다음 기능들은 초기 문서에서 언급되지만 현재 릴리스에서는 **출시되지 않았습니다**.

- 키별 역할 오버라이드(`effective_role`)와 세분화된 `allowed_actions` taxonomy(`scan:trigger`, `scan:read`, `report:download`, `webhook:receive`, `*`). 현재 Key는 발급자 역할과 전체 RBAC 표면을 상속.
- 키별 `expires_at` 필드 + New API key 폼의 30 / 90 / 180 / 365일 만료 프리셋. 현재 Key는 만료 없이 폐기될 때까지 유효.
- `actor_kind = api_key`인 요청별 `api_key.use` 감사 이벤트. 현재 Key 라이프사이클(ORM 리스너의 insert와 명시적 `api_key.revoked` 액션)은 감사되지만 요청별 사용은 구조화된 로그에만 캡처됨.
- 목록의 `last_used_ip` 컬럼.
- brute-force secret-mismatch 알림(단일 Key가 60초 내 5회 미스 시 Slack 알림).

## 함께 보기

- [GitHub Actions](../ci-integration/github-actions.md)
- [GitLab CI](../ci-integration/gitlab-ci.md)
- [Webhooks](../ci-integration/webhooks.md)
- [감사 로그](./audit-log.md)
