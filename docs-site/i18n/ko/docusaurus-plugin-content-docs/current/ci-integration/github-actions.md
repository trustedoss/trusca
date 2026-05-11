---
id: github-actions
title: GitHub Actions
description: 모노레포의 actions/scan 컴포지트 액션으로 TrustedOSS Portal을 GitHub Actions 워크플로에 연결합니다 — 트리거·폴링·게이트·코멘트.
sidebar_label: GitHub Actions
sidebar_position: 1
---

# GitHub Actions

TrustedOSS 컴포지트 액션은 TrustedOSS 스캔을 트리거하고 종료를 기다린 다음 빌드 게이트를 평가하고 (pull request에서는) SCA 보고서를 PR로 다시 게시합니다. 게이트가 실패하면 non-zero로 종료해 PR 체크가 빨갛게 변하고 브랜치 보호 룰이 머지를 차단합니다.

:::note 대상 독자
GitHub Actions를 사용하는 GitHub 저장소를 운영하는 엔지니어. 포털용 API Key가 필요합니다 — [API keys](../admin-guide/api-keys.md) 참고.
:::

:::note 액션 출처
모노레포의 `actions/scan/action.yml` 컴포지트 액션을 `uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0`로 직접 참조하세요. 독립된 Marketplace 게시는 로드맵에 있습니다.
:::

## 빠른 시작

```yaml
# .github/workflows/sca.yml
name: TrustedOSS SCA
on:
  pull_request:
  push:
    branches: [main]

jobs:
  sca:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write    # PR 코멘트에 필요
    steps:
      - uses: actions/checkout@v4
      - name: TrustedOSS SCA scan
        uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
        with:
          api-url: https://trustedoss.example.com
          api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
          project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
```

이게 최소 구성입니다. action은 다음을 수행합니다.

1. `kind=source`로 `POST /v1/projects/{project-id}/scans`를 호출해 cdxgen + ORT + Dependency-Track을 큐에 넣습니다.
2. 30초마다 `GET /v1/scans/{scan-id}`를 폴링해 최종 상태(`succeeded` / `failed` / `cancelled`)에 도달할 때까지 대기, 30분 타임아웃.
3. `GET /v1/projects/{project-id}/gate-result`를 호출해 verdict를 워크플로의 job summary에 기록.
4. `pull_request` 이벤트에서는 `POST /v1/scans/{scan-id}/post-pr-comment`를 호출해 SCA Markdown 보고서를 PR 코멘트로 게시.
5. 게이트 verdict가 `fail`이면 1로 종료.

## 셋업

### 1. API Key 생성

포털에서 **/integrations → API keys → New API key**. 스코프는 `project` 를 선택하고 CI 가 스캔할 프로젝트에 바인딩(또는 한 팀의 모든 프로젝트를 커버해야 한다면 `team`). v2.0.0 에서 API Key 는 발급 사용자의 역할을 상속하며 키별 허용 동작 목록은 존재하지 않습니다. 스코프 모델은 [API keys](../admin-guide/api-keys.md) 참고.

### 2. GitHub에 Key 저장

저장소에서 **Settings → Secrets and variables → Actions → New repository secret**.

- Name — `TRUSTEDOSS_API_KEY`
- Value — 전체 Key(`tos_<prefix>_<secret>`)

### 3. 프로젝트 ID를 변수로 저장

같은 화면에서 **Variables**로 전환 후 추가.

- Name — `TRUSTEDOSS_PROJECT_ID`
- Value — **Project Settings → CI/CD**의 UUID.

(시크릿이 아니라) 변수에 두면 워크플로 로그에서 프로젝트 ID가 그대로 보입니다 — 민감 정보가 아니므로 무방합니다.

### 4. 워크플로 추가

위 `.github/workflows/sca.yml`을 저장소에 두세요. 다음 PR부터 SCA 체크가 PR 상태로 나타납니다.

## 입력

| 이름 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `api-url` | yes | — | 포털 base URL, 예: `https://trustedoss.example.com`. 끝의 슬래시는 무방. |
| `api-key` | yes | — | API Key. **항상** `${{ secrets.* }}`로 공급. |
| `project-id` | yes | — | 프로젝트 UUID. |
| `scan-kind` | no | `source` | `source`(cdxgen + ORT + DT) 또는 `container`(Trivy). |
| `fail-on-gate` | no | `true` | `true`이면 게이트 verdict가 `fail`일 때 잡이 1로 종료. |
| `post-pr-comment` | no | `true` | `true`이고 `pull_request`에 의해 트리거된 경우 SCA 보고서를 PR 코멘트로 게시. |
| `poll-timeout-seconds` | no | `1800` | 스캔이 최종 상태에 도달할 때까지 기다리는 최대 초. |
| `poll-interval-seconds` | no | `30` | 스캔 상태 폴링 간격(초). |

## 출력

| 이름 | 설명 |
|---|---|
| `scan-id` | 큐에 넣고 평가한 스캔의 UUID. |
| `gate` | `pass` 또는 `fail`. |
| `reason` | `gate == 'fail'`일 때 사람이 읽는 사유, 그 외에는 빈 문자열. |
| `critical-cve-count` | 평가된 스캔의 미해결 critical 발견 수. |
| `forbidden-license-count` | 금지 분류 라이선스를 가진 고유 컴포넌트 수. |

후속 스텝에서 사용:

```yaml
- name: TrustedOSS SCA scan
  id: sca
  uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'    # 수집만, 실패 안 함
- name: Branch on the gate verdict
  if: steps.sca.outputs.gate == 'fail'
  run: |
    echo "Critical CVEs: ${{ steps.sca.outputs.critical-cve-count }}"
    echo "Forbidden licenses: ${{ steps.sca.outputs.forbidden-license-count }}"
    exit 1
```

## 레시피

### Advisory 모드(실패시키지 않고 보고만)

정책을 시드하는 동안 PR을 차단하지 않으려는 경우에 유용합니다.

```yaml
- uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'
```

PR 코멘트는 그대로 게시되며 체크는 green으로 유지됩니다.

### 컨테이너 스캔

```yaml
- uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
```

스캔 대상 이미지는 포털의 프로젝트 메타데이터(`container_image` 필드)가 결정합니다. 컨테이너 스캔은 OS 레이어에 Trivy를 실행합니다.

### 소스와 컨테이너 둘 다

서로 다른 `id`로 두 스텝 실행:

```yaml
- name: SCA — source
  uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: source

- name: SCA — container
  uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
```

기본적으로 어느 한 스텝의 실패가 잡 전체를 실패시킵니다.

### 브랜치별 게이트

`main`에서만 게이트를 적용하고 PR에서는 advisory:

```yaml
- uses: trustedoss/trustedoss-portal/actions/scan@v2.0.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && 'true' || 'false' }}
```

### 태그 핀

`@v1` 태그는 떠 있습니다(floating). 재현성을 위해 특정 커밋에 핀:

```yaml
- uses: trustedoss/trustedoss-portal/actions/scan@a1b2c3d4e5f6     # v2.0.0
```

## PR 코멘트는 어떻게 게시되나

PR 코멘트는 워크플로가 아니라 **포털이 서버 측에서** 게시합니다. 액션이 SCA 결과를 업로드한 뒤, 포털이 빌드 게이트를 평가하고 코멘트 게시가 활성화되어 있다면 포털 환경에 저장된 GitHub PAT(`GITHUB_TOKEN` 또는 `TRUSTEDOSS_GITHUB_TOKEN`)를 사용해 `https://api.github.com`을 직접 호출합니다. 워크플로는 절대로 `secrets.GITHUB_TOKEN`을 포털로 전달하지 않습니다. 포털에 저장된 installation 토큰을 가진 정식 GitHub App은 로드맵에 있습니다.

코멘트는 **idempotent**합니다 — 같은 PR에서 워크플로를 재실행하면 기존 코멘트가 제자리에 갱신됩니다. 마커 `<!-- trustedoss-sca -->`로 식별합니다.

## 브랜치 보호

모든 PR에 SCA를 강제하려면:

1. **Settings → Branches → Branch protection rules → Add rule**.
2. Branch name pattern — `main`.
3. **Require status checks to pass before merging** 체크.
4. 위 워크플로의 잡 이름 `sca`를 검색해 체크.
5. 저장.

이제 SCA 체크가 pending이거나 실패 중이면 PR을 머지할 수 없습니다.

## 트러블슈팅

### "Polling scan status"에서 잡이 타임아웃

worker가 과부하이거나(`poll-timeout-seconds`를 늘려보세요) 스캔이 정말 멎어 있을 수 있습니다. 포털 UI에서 해당 스캔을 열어 라이브 로그를 확인하세요.

### action에서 `403 Forbidden`

호출 대상 프로젝트가 API Key 스코프에 포함되지 않습니다. 해당 프로젝트에 바인딩된 스코프 `project` (권장) 로 재발급하거나, 팀의 모든 프로젝트에 도달해야 한다면 스코프 `team` 로 발급. 프로젝트가 해당 스코프 팀에 속하는지 확인. [API keys](../admin-guide/api-keys.md) 참고.

### PR 코멘트가 표시되지 않음

세 가지 가능성:

- 워크플로가 `pull_request`가 아닌 `push`로 트리거됨 — PR 이벤트만 코멘트를 받음.
- 포털의 `GITHUB_TOKEN` / `TRUSTEDOSS_GITHUB_TOKEN` env가 미설정·만료이거나 대상 저장소에 대한 `pull-requests: write` 권한이 없음. 운영자가 포털 `.env`의 PAT를 회전·연장한 뒤 백엔드를 재기동.
- 포털이 head SHA에서 PR 번호를 해석하지 못함. action 로그의 `pull_request_number=` 출력을 확인 — 비어 있으면 lookup 실패.

### chore PR에서 건너뛰고 싶음

문서만 변경될 때 워크플로가 돌지 않도록 path 필터:

```yaml
on:
  pull_request:
    paths-ignore:
      - 'docs/**'
      - '*.md'
```

## 함께 보기

- [GitLab CI](./gitlab-ci.md)
- [Jenkins](./jenkins.md)
- [Webhooks](./webhooks.md) — Action 이외의 push 자동화
- [API keys](../admin-guide/api-keys.md)
