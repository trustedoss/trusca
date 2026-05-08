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

## 사용자 초대

### Super-admin으로

1. **/admin/users** → **Invite user**.
2. 이메일, 이름, 기본 팀, 그 팀의 역할.
3. 제출.

초대된 사용자는 일회성 초대 링크(24시간 만료)가 담긴 이메일을 받습니다. 링크 클릭 시 비밀번호(12자 이상, bcrypt cost 12, NIST 차단 비밀번호 제외)를 설정합니다.

### Team admin으로

`team_admin` 권한이 있는 팀으로만 초대 가능. 흐름은 동일하되 팀 선택기가 없습니다.

## 기존 사용자를 팀에 추가

사용자는 여러 팀에 소속될 수 있습니다. 기존 사용자 추가:

1. **/admin/teams**(super-admin) 또는 **Team settings → Members**(team admin).
2. **Add member** → 이메일로 검색 → 역할 선택.

사용자가 즉시 추가됩니다; 이메일 확인 단계 없음(이미 계정 보유).

## 역할 변경

1. **/admin/users** → 사용자 → **Memberships**.
2. 해당 팀 행의 **Change role** 클릭.
3. 새 역할 선택 → 제출.

감사 로그에 `team_membership.update`가 `previous_role`, `new_role`과 함께 기록됩니다.

## 팀에서 사용자 제거

1. **Team settings → Members** → 사용자 → **Remove**.

사용자는 팀의 프로젝트 접근을 잃지만 계정은 남습니다. 계정 자체를 비활성화하려면 [비활성화](#사용자-비활성화) 참고.

## 마지막 super-admin 보호

포털은 조직의 마지막 `super_admin` 강등·비활성화를 **거부**합니다. 시도하면 API가 다음을 반환합니다.

```json
{
  "type": "https://trustedoss.io/problems/last-super-admin",
  "title": "Cannot demote the last super_admin",
  "status": 409,
  "detail": "At least one super_admin must remain in the organization.",
  "instance": "/api/v1/admin/users/01H…/role"
}
```

마지막 super-admin 교체:

1. 다른 사용자를 `super_admin`으로 먼저 승격.
2. 그 다음 원래 사용자를 강등·비활성화.

본 규칙은 UI뿐 아니라 데이터베이스 수준에서 강제됩니다(`CHECK` 제약 + API 사전 체크) — 제약을 비활성화하지 않는 한 직접 SQL로도 우회 불가합니다.

## 사용자 비활성화

비활성화는 모든 세션과 refresh 토큰을 회수합니다. 사용자는 로그인할 수 없습니다. 감사 로그 항목은 유지(행 추가 전용)됩니다.

1. **/admin/users** → 사용자 → **Deactivate**.
2. 확인.

같은 화면에서 한 번의 클릭으로 재활성화 가능합니다.

## 삭제 vs. 비활성화

- **비활성화** — 사용자 행 유지, 외래키가 깔끔하게 끊김. 기본·권장.
- **삭제** — 사용자 소프트 삭제. 계정은 복구 불가하나 감사 로그가 삭제된 사용자의 UUID를 참조. GDPR 잊혀질 권리 요청에만 사용; 그렇지 않으면 비활성화 권장.

"Delete" 버튼은 이메일 입력 확인 모달 뒤에 숨겨져 있습니다.

## 팀 생성

`super_admin` 전용.

1. **/admin/teams** → **New team**.
2. 이름·설명·새 프로젝트의 기본 가시성(`team_only` 또는 `org_wide`, 선택).
3. 제출.

팀의 첫 멤버는 다음 화면에서 배정합니다.

## 팀 이름 변경·아카이브

`super_admin`과 팀의 `team_admin`은 이름 변경 가능. 아카이브는 `super_admin` 필요:

- 기본 목록에서 팀을 숨김.
- 새 프로젝트 생성 차단.
- 기존 프로젝트·스캔·결과는 읽을 수 있게 유지.

팀을 삭제하려면 모든 프로젝트가 먼저 아카이브 또는 이동되어야 합니다.

## 세션

| 토큰 | 수명 | 저장 |
|---|---|---|
| **Access 토큰 (JWT)** | 30분 | 메모리(인앱), `Authorization: Bearer …`. |
| **Refresh 토큰** | 7일, rotation + 재사용 탐지. | HttpOnly + Secure 쿠키, SameSite=Lax. |

재사용 탐지: refresh 토큰이 두 번 제시되면 토큰 패밀리 전체가 무효화되어 모든 디바이스에서 재인증을 강제합니다. 이는 refresh 토큰 탈취를 잡습니다.

## 정상 동작 확인

사용자 초대 후:

1. **/admin/users**가 사용자를 `pending` 상태로 표시.
2. 감사 로그에 `user.invite` 기록.
3. 사용자가 링크를 활성화하면 상태가 `active`로 전환.
4. 사용자가 배정 역할로 팀 멤버 목록에 등장.

## 트러블슈팅

### 초대 이메일이 도착하지 않음

`.env`의 `SMTP_*`를 확인하세요. 이메일 워커가 SMTP 트랜잭션을 로깅합니다.

```bash
docker-compose -f docker-compose.yml logs --tail=200 worker | grep -i smtp
```

흔한 원인: `SMTP_USER` / `SMTP_PASSWORD` 누락, SMTP 호스트가 워커 IP 차단, 수신자 스팸 필터. 사용자 행에서 초대를 재발송하면 새 링크가 생성됩니다.

### 자기 자신의 역할을 승격할 수 없음

자기 승격은 차단됩니다. 다른 `super_admin`에게 요청하세요. 본인이 유일한 super-admin이라면 다른 super-admin으로 로그인하세요(항상 둘 이상을 유지해야 합니다).

### 초대 시 "User already exists"

이메일이 이미 등록되어 있습니다(다른 팀 소속일 수 있음). 대신 [기존 사용자를 팀에 추가](#기존-사용자를-팀에-추가)를 사용하세요.

## 함께 보기

- [API Key](./api-keys.md) — 서비스 계정 자격증명
- [감사 로그](./audit-log.md)
- [승인](../user-guide/approvals.md)
