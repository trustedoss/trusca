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
자체 서비스를 스캔하는 엔지니어와 팀 리드. 로그인 필요. 생성·아카이브는 프로젝트 소속 팀의 `developer` 이상, 가시성 변경·삭제는 `team_admin` 권한이 필요합니다.
:::

## 프로젝트 구성 요소

| 필드 | 설명 |
|---|---|
| **이름** | 표시용 라벨(자유 텍스트). 팀 내에서 유일해야 합니다. |
| **저장소 URL** | 스캔 파이프라인이 클론할 git URL. HTTPS·SSH 모두 지원. 사설 저장소는 자격증명 필요 — [사설 저장소](#사설-저장소)를 보세요. |
| **기본 브랜치** | 스캔 파이프라인이 체크아웃할 브랜치(보통 `main`). |
| **가시성** | `team-only`(기본 — 소속 팀 멤버에게만 보임) 또는 `org-wide`(조직 내 로그인 사용자 모두에게 보임). |
| **소속 팀** | 프로젝트가 속한 팀. 기본값은 활성 팀; super-admin은 변경 가능. |
| **컨테이너 이미지** | 선택. 설정하면 컨테이너 스캔(`Trivy`)이 이 레퍼런스(`<registry>/<image>:<tag>`)를 대상으로 합니다. |
| **태그** | 자유 형식 라벨. 대시보드 포트폴리오 뷰에서 그룹핑에 유용. |

## 프로젝트 추가 — UI

1. 로그인.
2. 사이드바의 **Projects** 클릭.
3. 우측 상단 **New project** 클릭.
4. 폼 작성:
   - **이름** (필수)
   - **저장소 URL** (소스 스캔에 필수)
   - **기본 브랜치** — 기본 `main`
   - **가시성** — 기본 `team-only`
   - **컨테이너 이미지** (선택)
5. **Create** 클릭.

프로젝트의 **Overview** 탭으로 이동합니다. 여기서 첫 스캔을 실행할 수 있습니다 — [스캔](./scans.md) 참고.

## 프로젝트 추가 — API

```bash
curl -sS -X POST https://trustedoss.example.com/api/v1/projects \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "checkout-service",
    "repository_url": "https://github.com/acme/checkout-service.git",
    "default_branch": "main",
    "visibility": "team_only",
    "container_image": "ghcr.io/acme/checkout-service:latest"
  }' | jq .
```

응답에 프로젝트 UUID가 포함됩니다 — GitHub Action의 `project-id` 입력값과 GitLab CI 변수에 사용하므로 보관하세요.

## 가시성

- **`team_only`** (기본) — 소속 팀 멤버만 프로젝트·스캔·결과를 볼 수 있습니다.
- **`org_wide`** — 조직 내 로그인 사용자 누구나 읽기 가능. 쓰기는 여전히 소속 팀의 역할이 필요합니다.

가시성 변경은 권한이 필요한 작업입니다. 감사 로그가 행위자와 이전 값을 기록합니다.

:::caution 가시성 다운그레이드
`org_wide` → `team_only` 전환은 다른 팀이 의존하던 프로젝트를 숨길 수 있습니다. 토글 전 이해관계자와 확인하세요.
:::

## 태그

태그는 대시보드 포트폴리오 뷰에서 프로젝트 그룹핑에 유용합니다. 환경(`prod`, `staging`), 언어 스택(`go`, `node`), 컴플라이언스 범위(`pci-dss`, `hipaa`)에 활용하세요.

태그 변경은 비파괴적이며 스캔을 차단하지 않습니다.

## 아카이브 vs. 삭제

- **아카이브** — 프로젝트와 그 이력·스캔·결과는 유지하되 기본 목록에서 숨기고 새 스캔을 막습니다. 서비스가 종료되었지만 컴플라이언스 추적이 필요한 경우에 유용합니다.
- **삭제** — 프로젝트와 그 하위 모든 항목을 영구 제거. 되돌릴 수 없습니다. 감사 로그 항목은 유지(행은 추가 전용)되지만 삭제된 프로젝트의 이름이 아닌 UUID를 참조합니다.

**Delete** 버튼은 사고 방지를 위해 이름 입력 확인 모달 뒤에 숨겨져 있습니다.

## 사설 저장소

소스 스캔은 워커 컨테이너 안에서 저장소를 클론합니다. 인증 옵션:

- **HTTPS + Personal Access Token** — URL을 `https://<token>@github.com/acme/checkout-service.git` 형태로 설정. 토큰은 저장 시 암호화되며 API에서 반환되지 않습니다.
- **SSH 배포 키** — `Project Settings → Repository`에서 배포 키 생성, 호스트에 읽기 전용 배포 키로 추가.

`org_wide` 프로젝트는 SSH 배포 키를 권장합니다 — URL이 로깅되면 임베딩된 HTTPS 토큰이 유출될 수 있습니다.

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
3. 감사 로그(`/admin/audit`)에 본인의 `user_id`로 `project.create`가 기록됩니다.

## 트러블슈팅

### "저장소 URL이 유효하지 않음"

마법사는 URL 형식(`https://...`, `git@...`, `ssh://...`)을 검증합니다. 도달 가능성은 검증하지 **않으며** 그것은 스캔 시점에 일어납니다. 폼 제출에서 거부되면 오타를 다시 확인하세요.

### "이미 사용 중인 프로젝트 이름"

이름은 팀별로 유일합니다. 기존 프로젝트의 이름을 변경하거나 접미사를 추가하세요(`checkout-service-legacy`).

### 프로젝트 생성 시 Forbidden

소속 팀에서 본인의 역할이 `developer` 미만입니다. 팀 admin에게 적절한 역할로 초대를 요청하세요 — [사용자 및 팀](../admin-guide/users-and-teams.md).

## 함께 보기

- [스캔](./scans.md) — 첫 스캔 실행
- [취약점](./vulnerabilities.md) — 결과 분류
- [컴포넌트·라이선스](./components-and-licenses.md) — 컴포넌트 목록 읽기
- [사용자 및 팀](../admin-guide/users-and-teams.md) — 역할 모델
