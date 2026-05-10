---
id: gitlab-ci
title: GitLab CI
description: include 가능한 templates/gitlab-ci.yml로 TrustedOSS Portal을 GitLab CI에 연결합니다 — 트리거·폴링·게이트·코멘트.
sidebar_label: GitLab CI
sidebar_position: 2
---

# GitLab CI

포털은 GitHub Action을 미러링하는 `include` 가능한 GitLab CI 템플릿을 제공합니다 — 스캔을 트리거하고 최종 상태까지 폴링한 다음 빌드 게이트를 평가합니다. 템플릿은 단일 잡이며, 어떤 필드든 확장하거나 오버라이드할 수 있습니다.

:::warning GitLab MR 코멘트 — 아직 출하되지 않음
v2.0.0의 포털 PR-코멘트 통합은 GitHub 전용입니다. `templates/gitlab-ci.yml`의 MR-코멘트 잡은 요청을 스테이징하지만, 백엔드 `services/sca_comment.py`는 `api.github.com` 호출만 알고 있습니다 — GitLab `repo_full_name`으로 호출하면 404가 반환됩니다. GitLab Notes API 클라이언트가 도착할 때까지 GitLab 측에서는 빌드 게이트의 종료 코드를 사용하세요.
:::

:::note 대상 독자
GitLab CI/CD를 사용하는 GitLab 프로젝트를 운영하는 엔지니어. 포털용 API Key가 필요합니다 — [API keys](../admin-guide/api-keys.md) 참고.
:::

## 빠른 시작

```yaml
# .gitlab-ci.yml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

variables:
  TRUSTEDOSS_API_URL: 'https://trustedoss.example.com'
  TRUSTEDOSS_PROJECT_ID: '01H7XYZ…'
  # TRUSTEDOSS_API_KEY는 masked CI/CD 변수입니다 — 여기에 절대 적지 마세요.
```

베이스 템플릿은 hidden입니다 — 직접 만든 잡에서 extend해 materialize해야 하며, 베이스를 extend하지 않는 파이프라인은 SCA를 자동 트리거하지 않습니다. 다음과 같은 잡을 추가하세요.

```yaml
sca:
  extends: .trustedoss-sca
```

## 셋업

### 1. API Key 생성

포털에서 **Project Settings → CI/CD → API keys → New API key**. 허용 동작 — `scan:trigger`, `scan:read`, `report:download`. [API keys](../admin-guide/api-keys.md) 참고.

### 2. masked CI/CD 변수로 Key 저장

GitLab 프로젝트에서 **Settings → CI/CD → Variables → Add variable**.

- Key — `TRUSTEDOSS_API_KEY`
- Value — 전체 Key(`tos_<prefix>_<secret>`)
- Type — `Variable`
- Flags — **Masked**(yes), **Protected**(`main` 한정 권장)

masked 플래그는 잡 로그에 Key가 그대로 노출되는 것을 막습니다.

### 3. URL과 프로젝트 ID 설정

`TRUSTEDOSS_API_URL`과 `TRUSTEDOSS_PROJECT_ID`는 다음 중 하나에 둘 수 있습니다.

- `.gitlab-ci.yml`의 `variables:`(읽기 권한자에게 보임).
- 또는 CI/CD 변수(여러 환경을 운영한다면 더 나은 선택).

어느 쪽이든 `TRUSTEDOSS_API_KEY`만 masked여야 합니다.

## 변수

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `TRUSTEDOSS_API_URL` | yes | — | 포털 base URL. |
| `TRUSTEDOSS_API_KEY` | yes | — | API Key(masked CI/CD 변수). |
| `TRUSTEDOSS_PROJECT_ID` | yes | — | 프로젝트 UUID. |
| `TRUSTEDOSS_SCAN_KIND` | no | `source` | `source` 또는 `container`. |
| `TRUSTEDOSS_FAIL_ON_GATE` | no | `true` | `true`이면 게이트 실패 시 잡이 1로 종료. |
| `TRUSTEDOSS_POLL_TIMEOUT` | no | `1800` | 최종 상태까지 기다리는 최대 초. |
| `TRUSTEDOSS_POLL_INTERVAL` | no | `30` | 폴링 간격(초). |
| `TRUSTEDOSS_POST_MR_COMMENT` | no | `true` | 향후 GitLab Notes API 통합용 예약. v2.0.0에서는 요청이 스테이징되지만 백엔드가 전송할 수 없습니다 — 위 경고 참고. |

## 레시피

### Advisory 모드

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

variables:
  TRUSTEDOSS_API_URL: 'https://trustedoss.example.com'
  TRUSTEDOSS_PROJECT_ID: '01H7XYZ…'
  TRUSTEDOSS_FAIL_ON_GATE: 'false'
```

잡은 green을 유지하고 MR 노트는 그대로 게시됩니다.

### 보호된 브랜치에서만 실행

include한 잡의 rules를 오버라이드:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

.trustedoss-sca:
  rules:
    - if: '$CI_COMMIT_REF_PROTECTED == "true"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

### 컨테이너 스캔을 별도 잡으로

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

trustedoss:scan-container:
  extends: .trustedoss-sca
  variables:
    TRUSTEDOSS_SCAN_KIND: 'container'
```

### 태그 핀

재현 가능한 파이프라인을 위해 `include` URL을 `main`이 아닌 릴리스 태그(`v2.0.0`)에 핀하세요.

## 템플릿 해부 (고급)

러너가 `include`를 위해 GitHub에 도달하지 못하는 등의 이유로 잡을 복사·인라인해야 한다면 표준 형태는 다음과 같습니다.

```yaml
.trustedoss-sca:
  image: alpine:3.20
  stage: test
  before_script:
    - apk add --no-cache curl jq bash ca-certificates
  script:
    - bash -c '
        set -euo pipefail;
        SCAN_ID=$(curl -fsS -X POST
          -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}"
          -H "Content-Type: application/json"
          -d "{\"kind\": \"${TRUSTEDOSS_SCAN_KIND:-source}\"}"
          "${TRUSTEDOSS_API_URL}/api/v1/projects/${TRUSTEDOSS_PROJECT_ID}/scans"
          | jq -r .id);
        echo "scan_id=$SCAN_ID";
        # 최종 상태까지 폴링 …
        # 게이트 평가, MR 노트 게시, 0/1 종료
      '
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
```

전체 표준 버전은 [`templates/gitlab-ci.yml`](https://github.com/trustedoss/trustedoss-portal/blob/main/templates/gitlab-ci.yml)에 있습니다. fork 전에 읽어 보세요 — 다시 구현하고 싶지 않은 엣지 케이스(폴링 중 네트워크 단절, masked-token 회전)를 다룹니다.

## 브랜치 / 머지 보호

모든 MR에 SCA를 강제하려면:

1. **Settings → Repository → Protected branches** — `main`을 보호.
2. **Settings → Merge requests → Merge checks** — "Pipelines must succeed"를 켜기.

SCA 잡(`.trustedoss-sca`를 extend한 잡)이 실패하는 MR은 머지할 수 없습니다.

## 트러블슈팅

### include된 잡에 `Authorization` 헤더가 빠짐

GitLab은 빈 변수를 제거합니다. 관련 환경 / 브랜치에 `TRUSTEDOSS_API_KEY`가 정의되어 있는지 확인하세요. 변수의 "Protected" 플래그는 보호된 ref에만 주입됨을 의미하므로 — 일반 MR에도 필요하면 조정.

### MR 노트가 게시되지 않음

v2.0.0에서는 정상 동작입니다 — GitLab Notes API 클라이언트가 아직 출하되지 않았습니다(페이지 상단 경고 참고). verdict를 표면화하려면 GitLab 측에서 빌드 게이트 종료 코드(`TRUSTEDOSS_FAIL_ON_GATE=true`)를 사용하세요.

### 폴링 단계에서 잡이 시간 초과

`TRUSTEDOSS_POLL_TIMEOUT`은 기본 30분 — 큰 레포에서는 초과될 수 있습니다. 3600(1시간)으로 올리고 재실행.

### `POST /scans`에서 "Forbidden"

API Key의 허용 동작에 `scan:trigger`가 없습니다. 올바른 scope로 재발급.

## 함께 보기

- [GitHub Actions](./github-actions.md)
- [Jenkins](./jenkins.md)
- [Webhooks](./webhooks.md)
- [API keys](../admin-guide/api-keys.md)
