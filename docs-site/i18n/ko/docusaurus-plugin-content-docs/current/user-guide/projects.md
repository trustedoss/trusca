---
id: projects
title: 프로젝트
description: TrustedOSS Portal에서 프로젝트 등록·설정·아카이브 — 스캔·컴포넌트·취약점·의무사항을 묶는 단위입니다.
sidebar_label: 프로젝트
sidebar_position: 1
---

# 프로젝트

**프로젝트**는 포털이 인지하는 소스 추적 단위입니다. 스캔, 컴포넌트, 취약점, 라이선스 결과, 의무사항, 자동 생성된 `NOTICE` 파일을 보유합니다. 대부분의 워크플로우는 프로젝트 추가에서 시작합니다.

:::note 대상 독자
자체 서비스를 스캔하는 엔지니어와 팀 리드. 로그인 필요. 생성·아카이브는 프로젝트 소속 팀의 `developer` 이상, 가시성 변경은 `team_admin` 권한이 필요합니다.
:::

## 프로젝트 구성 요소

| 필드 | 설명 |
|---|---|
| **이름 (Name)** | 표시용 라벨(자유 텍스트). 팀 내에서 유일해야 합니다. |
| **설명 (Description)** | 선택. 프로젝트 목록과 Overview 탭에 노출되는 자유 텍스트 요약. |
| **Git URL** | 스캔 파이프라인이 클론할 git URL. HTTPS 지원. 사설 저장소는 URL에 자격증명을 포함해야 합니다 — [사설 저장소](#사설-저장소)를 보세요. |
| **기본 브랜치 (Default branch)** | 스캔 파이프라인이 체크아웃할 브랜치(기본값 `main`). 생성 후 **Project Settings**에서 수정. |
| **가시성 (Visibility)** | `team` (v2.0.0 시점에서 허용되는 유일한 값 — 소속 팀 멤버만 조회). 생성 시 자동 설정되며 PATCH로만 변경 가능. |
| **소속 팀 (Owning team)** | 프로젝트가 속한 팀. 생성 시 활성 팀으로 자동 설정. |

## 프로젝트 추가 — UI

사이드바의 **Projects** 항목은 활성 팀 범위의 프로젝트 목록 — 활성 팀에 속한 모든 프로젝트, 상태 배지와 인라인 **Scan** 액션 — 을 보여줍니다:

![/projects 목록 — 팀 범위의 표로 name, 마지막 스캔 상태 배지, severity 카운트, 행별 인라인 Scan 액션](/img/screenshots/user-projects-list.png)

1. 로그인.
2. 사이드바의 **Projects** 클릭.
3. 우측 상단 **New project** 클릭.
4. 폼 작성:
   - **이름** (필수)
   - **설명** (선택)
   - **Git URL** (소스 스캔에 필수)
5. **Create** 클릭.

   ![New project 폼 — name / description / Git URL 필드](/img/screenshots/user-projects-create-form.png)

프로젝트의 **Overview** 탭으로 이동합니다. 여기서 첫 스캔을 실행할 수 있습니다 — [스캔](./scans.md) 참고.

![프로젝트 상세 — 리스크 게이지와 빠른 액션이 있는 Overview 탭](/img/screenshots/user-project-detail-overview.png)

기본 브랜치(`main`), 가시성(`team`), 소속 팀(활성 팀)은 서버에서 자동 설정되며 **Project Settings**에서 확인 가능합니다.

## 프로젝트 추가 — API

```bash
curl -sS -X POST https://trustedoss.example.com/v1/projects \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "checkout-service",
    "description": "Storefront checkout service",
    "git_url": "https://github.com/acme/checkout-service.git"
  }' | jq .
```

응답에 프로젝트 UUID가 포함됩니다 — GitHub Action의 `project-id` 입력값과 GitLab CI 변수에 사용하므로 보관하세요.

스키마는 알 수 없는 필드를 거부합니다(`extra="forbid"`). 생성 시 허용되는 필드는 `name`, `description`, `git_url` 뿐입니다. `default_branch`는 이후 `PATCH /v1/projects/{id}`로 설정합니다.

생성 페이로드에 `team_id` 는 **필요하지 않습니다** — 서버가 활성
팀에서 자동으로 도출합니다. 이 필드는 향후 멀티 팀 스코핑을 위해
예약되어 있으며, 그 전까지 생성 호출에서는 무시해도 됩니다.

## 가시성

- **`team`** (v2.0.0 기본값이자 유일하게 허용되는 값) — 소속 팀 멤버만 프로젝트·스캔·결과를 볼 수 있습니다.

가시성은 생성 시 자동으로 설정됩니다. PATCH는 현재 `team` 외의 값을 거부합니다. 모든 PATCH 호출에서 감사 로그가 행위자를 기록합니다.

`organization`(조직 전체 읽기) 가용 시점은 [로드맵](#로드맵-v2x) 참고.

## 아카이브

- **아카이브** — 프로젝트와 그 이력·스캔·결과는 유지하되 기본 목록에서 숨기고 새 스캔을 막습니다. 서비스가 종료되었지만 컴플라이언스 추적이 필요한 경우에 유용합니다.

`DELETE /v1/projects/{id}`는 soft-delete(아카이브)를 수행합니다. 영구 삭제 동작은 현재 노출되지 않으며, 감사 로그 항목은 어떤 경우에도 유지됩니다.

아카이브 동작은 **Project Settings → Archive**에 위치하며, 사고 방지를 위해 인라인 확인 스트립을 사용합니다.

## 사설 저장소

소스 스캔은 워커 컨테이너 안에서 저장소를 클론합니다. v2.0.0에서 지원되는 인증 옵션:

- **HTTPS + Personal Access Token** — URL을 `https://<token>@github.com/acme/checkout-service.git` 형태로 설정. 토큰은 `git_url`의 일부로 저장되며, 읽기 엔드포인트가 평문으로 반환하지 않습니다.

:::caution v2.0.0 의 사설 저장소
현재 지원되는 자격증명 모델은 **git URL 에 PAT 를 임베드한 HTTPS**
(`https://<token>@github.com/acme/payment-service.git`) 뿐입니다.
PAT 는 프로젝트 행에 영구 저장됩니다(읽기 엔드포인트가 평문 PAT 를
절대 반환하지 않으며, `git_url` 은 감사 로그에서 마스킹됩니다).

함의:
- 유출된 DB 스냅샷은 임베드된 모든 PAT 를 함께 유출합니다.
  read-only scope 의 단기 PAT 를 사용하세요.
- SSH key 와 GitHub-App 설치는 v2.1 로드맵 항목입니다;
  그때까지 적극적으로 회전하세요.
:::

SSH 배포 키는 [로드맵](#로드맵-v2x)을 보세요.

## 리스크 점수

각 프로젝트는 종합 **리스크 점수**(0–100)를 표시합니다. 다음을 결합합니다.

- 심각도별 미해결 취약점(Critical, High, Medium, Low).
- 라이선스 분류 비율(금지 라이선스가 점수를 지배).
- 마지막 스캔 후 경과 시간(오래된 스캔은 가중치 감소).

점수는 매 스캔 후, 그리고 매 CVE 재탐지 후 갱신됩니다. 절대적 SLA가 아닌 포트폴리오 내 상대 지표로 읽으세요. 프로젝트로 들어가면 분해도가 보입니다.

## 정상 동작 확인

프로젝트 생성 후:

1. **Projects**에 프로젝트가 **Idle**(스캔 없음) 상태로 표시됩니다.
2. Overview 탭은 컴포넌트·취약점 모두 0을 보여줍니다.
3. 감사 로그(`/admin/audit`, super-admin 전용)에 본인의 `user_id`로 `target_table=projects&action=create`가 기록됩니다.

## 트러블슈팅

### "저장소 URL이 유효하지 않음"

마법사는 URL이 `http://` 또는 `https://`로 시작해야 합니다(HTTPS 강력 권장). v2.0.0 시점에서 `git@…`·`ssh://…` URL은 폼이 받지 않습니다. HTTPS 클론 URL을 사용하세요. 포털은 도달 가능성을 검증하지 **않으며** 그것은 스캔 시점에 일어납니다. 폼 제출에서 거부되면 오타를 다시 확인하세요.

### "이미 사용 중인 프로젝트 이름"

이름은 팀별로 유일합니다. 기존 프로젝트의 이름을 변경하거나 접미사를 추가하세요(`checkout-service-legacy`).

### 프로젝트 생성 시 Forbidden

소속 팀에서 본인의 역할이 `developer` 미만입니다. 팀 admin에게 적절한 역할로 초대를 요청하세요 — [사용자 및 팀](../admin-guide/users-and-teams.md).

## 로드맵 (v2.x)

매뉴얼이 이전에 약속했으나 v2.0.0에 포함되지 않은 항목 — 향후 릴리스에서 다룹니다.

- 프로젝트의 `container_image` 필드(및 Container 스캔 트리거) — v2.1 예정.
- 포트폴리오 그룹핑용 프로젝트 태그 — v2.1 예정.
- `organization`(조직 전체) 가시성 — v2.2 예정.
- **Project Settings**에서의 SSH 배포 키 생성 — v2.2 예정.
- 이름 입력 확인을 동반한 프로젝트 영구 삭제 — 설계 중. 현재는 soft-delete(아카이브)만 가능.
- 생성 마법사의 SSH(`git@…`, `ssh://…`) URL 수용 — v2.1 예정.

## 함께 보기

- [스캔](./scans.md) — 첫 스캔 실행
- [취약점](./vulnerabilities.md) — 결과 분류
- [컴포넌트·라이선스](./components-and-licenses.md) — 컴포넌트 목록 읽기
- [사용자 및 팀](../admin-guide/users-and-teams.md) — 역할 모델
