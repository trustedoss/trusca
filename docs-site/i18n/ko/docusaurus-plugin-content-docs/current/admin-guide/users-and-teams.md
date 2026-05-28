---
id: users-and-teams
title: 사용자 및 팀
description: TrustedOSS Portal의 사용자·팀 관리 — RBAC 역할, 마지막 super-admin 보호, 초대, 조직 vs. 팀 모델.
sidebar_label: 사용자 및 팀
sidebar_position: 1
---

# 사용자 및 팀

포털은 권한을 하나의 **조직**, 다수의 **팀**, 세 가지 **역할**로 모델링합니다. 모든 사용자는 하나 이상의 팀에 소속되며 프로젝트는 팀에 귀속됩니다. 배포당 정확히 하나의 조직이 존재합니다.

:::note 대상 독자
배포를 셋업하는 super-admin; 자기 팀의 멤버십을 관리하는 team admin.
:::

## 모델

```
Organization (배포당 하나)
├── Super Admin            — 시스템 전반(install.sh 이후의 본인)
├── Team A
│   ├── Team Admin         — 팀 설정·멤버 관리
│   └── Developer          — 스캔 실행, 결과 분류
└── Team B
    └── ...
```

- **Organization** — 배포의 경계. super-admin은 조직 단위.
- **Team** — 프로젝트·스캔·결과가 속하는 단위.
- **User** — 이메일 + 비밀번호(또는 데모 SaaS의 OAuth ID)를 가진 사람.

## 역할 {#역할}

| 역할 | 범위 | 권한 |
|---|---|---|
| **`super_admin`** | 조직 전반 | 모든 admin 화면(`/admin/**`). 팀 생성·삭제. 모든 프로젝트 편집. 모든 감사 로그 읽기. |
| **`team_admin`** | 팀별 | 팀 멤버십·설정 관리. 팀 소유 프로젝트 편집. 승인 처리. 팀 API Key 관리. |
| **`developer`** | 팀별 | 팀 프로젝트 읽기. 프로젝트 생성·편집. 스캔 실행·취소. 결과 분류(VEX 상태). 멤버·설정 관리는 불가. |

역할은 **여러 팀에 걸쳐 누적**됩니다 — 사용자는 한 팀에서 `team_admin`이고 다른 팀에서 `developer`일 수 있습니다. 역할은 프로젝트 소속 팀 기준으로 평가됩니다.

`super_admin`은 팀별 역할이 **아닙니다** — 팀 멤버십과 무관하게 조직 전반 접근을 부여합니다.

## Users 페이지

`/admin/users` 페이지는 배포 내 모든 계정을 역할 배지, 활성화 상태, 마지막 로그인 시간, 팀 멤버십 카운트와 함께 보여줍니다. 이메일·이름으로 검색하고 역할·상태로 필터링할 수 있습니다.

![Admin Users 페이지 — 검색·필터 툴바와 역할·상태 컬럼이 있는 사용자 표](/img/screenshots/admin-users-list.png)

`/admin/teams` 페이지는 팀 목록과 각 팀이 보유한 프로젝트·멤버 수를 보여줍니다:

![Admin Teams 페이지 — 팀별 멤버·프로젝트 카운트](/img/screenshots/admin-teams-list.png)

## 새 사용자 온보딩

v0.10.0 에서는 포털이 초대 이메일을 보내지 않습니다. 새 사용자는 회사 이메일로 `/register`에서 **셀프 가입**하며, 비밀번호 정책은 가입 시점에 강제됩니다(12자 이상, bcrypt cost 12, NIST 차단 비밀번호 제외).

가입 후, `super_admin`이 사용자를 적절한 팀에 추가하고 역할을 배정합니다.

1. 사용자에게 `/register`에서 가입을 요청합니다.
2. **/admin/users**에 사용자가 나타나면 사용자 드로어를 엽니다.
3. **Add to team**(또는 팀의 **Members → Add member** 흐름)으로 선택한 역할의 팀 멤버십을 부여합니다.

## 동료 온보딩

v0.10.0 에서 포털은 초대 이메일을 보내지 않습니다. 흐름은 다음과 같습니다:

1. **관리자**가 `/admin/teams → New team` 에서 팀을 만듭니다. (아래
   UI 흐름은 team UUID 가 필요하지 않습니다 — 관리자는 이메일로
   동료를 식별합니다. UUID 가 필요한 경로는 스크립트 기반 대량
   온보딩뿐이며, `GET /v1/admin/teams` 와 본 섹션 하단의 일괄
   레시피를 참고하세요.)
2. **동료**가 `https://<your-host>/register` 에서 셀프 가입합니다.
3. **관리자**가 `/admin/users → <user>` 에서 해당 사용자의 행을 열고
   드로어 → **Memberships** → **Add to team** 으로 팀과 역할을 선택합니다
   (기본값으로 `developer` 가 안전).

결과: 동료는 다음 로그인부터 팀의 프로젝트를 볼 수 있습니다.
각 동료가 가입을 마친 뒤에는
`POST /v1/admin/teams/{team_id}/members {user_id, role}` 로
대량 온보딩을 스크립트화할 수 있습니다.

## 기존 사용자를 팀에 추가

사용자는 여러 팀에 소속될 수 있습니다. 기존 사용자 추가:

1. **/admin/teams**(super-admin) 또는 **Team settings → Members**(team admin).
2. **Add member** → 이메일로 검색 → 역할 선택.

사용자가 즉시 추가됩니다; 이메일 확인 단계 없음(이미 계정 보유).

## 사용자 역할 변경

**/admin/users → 사용자** 드로어는 **Role** 드롭다운과 **Memberships** 섹션을 노출합니다. 한 사용자가 팀마다 다른 역할을 보유할 수 있습니다(팀 A 에서 `team_admin`, 팀 B 에서 `developer`) — Memberships 리스트가 모든 배정을 표시하며 인라인으로 편집합니다. Role 드롭다운은 Memberships 리스트에서 선택된 팀의 역할을 설정합니다(또는 `super_admin` 으로 승격할 때의 글로벌 역할).

1. **/admin/users** → 사용자 → **Role**.
2. 새 역할 선택 → 제출.

감사 로그는 변경을 `users` 쓰기로 기록하며 역할 diff가 `diff` 컬럼에 담깁니다(감사 행의 `target_table`은 `users`).

## 팀에서 사용자 제거

1. **Team settings → Members** → 사용자 → **Remove**.

사용자는 팀의 프로젝트 접근을 잃지만 계정은 남습니다. 계정 자체를 비활성화하려면 [비활성화](#사용자-비활성화) 참고.

## 마지막 super-admin 보호

포털은 조직의 마지막 활성 `super_admin` 강등·비활성화를 **거부**합니다. 사전 체크는 `SELECT … FOR UPDATE` 트랜잭션 안에서 실행되어 동시 강등 시도가 경합되지 않고 직렬화됩니다. 시도하면 API가 다음을 반환합니다.

```json
{
  "type": "about:blank",
  "title": "Last Super Admin Protected",
  "status": 422,
  "detail": "At least one active super_admin must remain in the organization.",
  "instance": "/v1/admin/users/01H…/role",
  "last_super_admin_protected": true
}
```

`last_super_admin_protected: true` 확장 필드는 클라이언트가 본 가드를 일반적인 422 검증 실패와 구분할 수 있게 합니다.

마지막 super-admin 교체:

1. 다른 사용자를 `super_admin`으로 먼저 승격.
2. 그 다음 원래 사용자를 강등·비활성화.

가드는 두 계층으로 강제됩니다.

1. **API 계층** — `admin_user_service` 의 `SELECT … FOR UPDATE` 행 락 카운트가 commit 전에 강등·비활성화 시도를 거부.
2. **DB 계층** — PostgreSQL 트리거(`trg_last_super_admin`, 마이그레이션 `0013`)가 활성 super-admin 수를 0 으로 만드는 모든 `UPDATE`/`DELETE` 에 대해 `SQLSTATE 23514` 를 발생. 직접 `psql` 쓰기로 API 를 우회하더라도 동일하게 차단됩니다. 어느 계층이 잡았든 동일한 `last_super_admin_protected` Problem Details 확장 필드가 반환됩니다.

## 비활성화된 super-admin 복구 {#비활성화된-super-admin-복구}

[마지막 super-admin 보호](#마지막-super-admin-보호) 가드에도 불구하고 **마지막** super-admin 행의 `is_active` 가 `false` 로 뒤집힌 경우 — 예를 들어 배포 DB 대상 통합 테스트가 `deactivate_user` 를 트립했거나, 다른 super-admin 이 교체자를 승격하기 전에 본인을 강등한 경우 — 설치 시 `scripts/install.sh` 가 사용한 부트스트랩 스크립트를 같은 방식으로 다시 실행합니다. 스크립트는 기존 행을 감지해 저장된 비밀번호는 건드리지 않고 `is_active` 만 다시 `true` 로 올립니다.

```bash
docker-compose -f docker-compose.yml exec -T \
  -e ADMIN_EMAIL="admin@example.com" \
  -e ADMIN_PASSWORD="<기존 비밀번호 — 12자 이상>" \
  backend python -m scripts.create_super_admin
```

스크립트 동작 순서:

1. `ADMIN_EMAIL` 로 행을 조회.
2. 행이 존재하고 `super_admin` 이며 비활성 상태이면 → `is_active` 를 `true` 로 되돌리고 커밋 후 `super admin <email> reactivated` 출력. 저장된 비밀번호 해시는 **그대로 둠**(행이 비활성된 사실만 잊은 경우 재실행이 비파괴적으로 동작하도록).
3. 행이 존재하고 이미 활성이면 → `super admin <email> already exists — noop` 출력 후 0 종료.
4. 행이 존재하지만 `super_admin` 이 **아니면** → 에러 출력 후 비0 종료. 재실행 전에 수동으로 승격·교체해야 합니다.
5. 일치하는 행이 없으면 → 전달된 비밀번호로 새 super-admin 생성.

:::caution 재활성화 경로에서는 비밀번호가 유지됩니다
`ADMIN_PASSWORD` 값은 행이 **생성**될 때만 사용됩니다. 재활성화 경로에서는 비밀번호 해시가 그대로 유지되므로 **현재** 비밀번호를 그대로 전달하면 됩니다(새 비밀번호 아님). 비밀번호도 잊었다면 재활성화로 `is_active` 가 회복된 **뒤** [비밀번호 재설정](../user-guide/auth-and-profile.md#비밀번호-재설정) 절차를 따르거나, 두 번째 super-admin 계정에서 운영자 측 `/admin/users/{id}/password-reset` 엔드포인트를 사용하세요.
:::

:::note 왜 UI 버튼이 아닌 재실행 방식인가요?
UI 에서 *마지막* 관리자를 재활성화하면 부트스트랩 역설이 발생합니다 — 버튼을 누를 활성 관리자가 없으니까요. 스크립트는 백엔드 컨테이너 안에서 DB 자격증명으로 직접 실행되므로 어떤 super-admin 도 로그인할 수 없는 상황에서도 안전한 복구 해치 역할을 합니다. 동작은 멱등합니다 — 이미 활성인 행에 대해서는 no-op.
:::

## 사용자 비활성화

비활성화는 모든 세션과 refresh 토큰을 회수합니다. 사용자는 로그인할 수 없습니다. 감사 로그 항목은 유지(행 추가 전용)됩니다.

1. **/admin/users** → 사용자 → **Deactivate**.
2. 확인.

같은 화면에서 한 번의 클릭으로 재활성화 가능합니다.

비활성화는 사용 가능한 유일한 오프보딩 동작입니다 — 별도의 사용자 삭제 작업이 없습니다. GDPR 삭제 요청을 처리하려면 사용자를 비활성화한 뒤 엔지니어링 팀에 수동 삭제를 의뢰하세요. 이메일 입력 확인 모달이 있는 1급 소프트-삭제는 로드맵 항목입니다.

## 팀 생성

`super_admin` 전용.

1. **/admin/teams** → **New team**.
2. 이름·설명·새 프로젝트의 기본 가시성(`team_only` 또는 `org_wide`, 선택).
3. 제출.

팀의 첫 멤버는 다음 화면에서 배정합니다.

## 팀 이름 변경

`super_admin`과 팀의 `team_admin`은 팀 이름을 변경할 수 있습니다. 팀의 `name`, `slug`, `description`은 `PATCH /v1/admin/teams/{team_id}`로 변경 가능합니다.

팀 아카이브(새 프로젝트 생성을 차단하면서 기존 프로젝트는 읽기 가능하게 유지하는 숨김 상태)는 로드맵 항목입니다. v0.10.0 에서는 팀 이름 변경, 또는 모든 프로젝트가 먼저 제거된 상태에서 `super_admin`이 직접 팀 삭제만 가능합니다.

## 세션

| 토큰 | 수명 | 저장 |
|---|---|---|
| **Access 토큰 (JWT)** | 30분 | 메모리(인앱), `Authorization: Bearer …`. |
| **Refresh 토큰** | 7일, rotation + 재사용 탐지. | HttpOnly + Secure 쿠키, SameSite=Lax. |

재사용 탐지: refresh 토큰이 두 번 제시되면 토큰 패밀리 전체가 무효화되어 모든 디바이스에서 재인증을 강제합니다. 이는 refresh 토큰 탈취를 잡습니다.

## 정상 동작 확인

사용자 온보딩 후:

1. 사용자가 가입 시 설정한 비밀번호로 `/login`에서 로그인 가능.
2. **/admin/users**가 사용자를 `is_active = true`로 표시.
3. 감사 로그에 팀-추가가 `memberships` insert로 기록.
4. 사용자가 배정 역할로 팀 멤버 목록에 등장.

## 트러블슈팅

### 신규 사용자가 가입할 수 없음

셀프 가입은 기본적으로 열려 있습니다. 사용자가 정확한 URL(`/register`)로 접근하는지, 이메일이 기본 형식 검증을 통과하는지, 선택한 비밀번호가 정책(12자 이상, NIST 차단 목록 외)을 만족하는지 확인하세요. 실패한 가입은 백엔드에 구조화 경고 로그를 남깁니다.

```bash
docker-compose -f docker-compose.yml logs --tail=200 backend | grep -i register
```

### 자기 자신의 역할을 승격할 수 없음

자기 승격은 차단됩니다. 다른 `super_admin`에게 요청하세요. 본인이 유일한 super-admin이라면 다른 super-admin으로 로그인하세요(항상 둘 이상을 유지해야 합니다).

### 팀에 추가 시 "User already exists"

이메일이 이미 포털 계정입니다(이미 다른 팀 소속일 수 있음). [기존 사용자를 팀에 추가](#기존-사용자를-팀에-추가)를 사용하세요 — 같은 흐름이 이메일로 사용자를 찾아 멤버십만 부착합니다.

## 로드맵

다음 기능들은 초기 문서에서 다른 곳에 기술되었으나 v0.10.0 에는 **반영되지 않았습니다**. 향후 마이너 릴리스를 위해 추적합니다.

- 24시간 일회성 활성화 링크와 `pending` 사용자 상태가 있는 이메일 기반 초대 흐름.
- 이메일 입력 확인 모달이 있는 사용자 소프트-삭제 동작.
- 팀 아카이브 상태(읽기 접근은 보존하면서 숨김+비활성화).

## 함께 보기

- [API Key](./api-keys.md) — 서비스 계정 자격증명
- [감사 로그](./audit-log.md)
- [승인](../user-guide/approvals.md)
