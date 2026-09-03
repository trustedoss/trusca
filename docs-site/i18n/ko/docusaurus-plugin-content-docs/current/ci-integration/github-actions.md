---
id: github-actions
title: GitHub Actions
description: 모노레포의 actions/scan 컴포지트 액션으로 TRUSCA를 GitHub Actions 워크플로에 연결합니다 — 트리거·폴링·게이트·코멘트.
sidebar_label: GitHub Actions
sidebar_position: 1
---

# GitHub Actions

TRUSCA 컴포지트 액션은 TRUSCA 스캔을 트리거하고 종료를 기다린 다음 빌드 게이트를 평가하고 (pull request에서는) SCA 보고서를 PR로 다시 게시합니다. 게이트가 실패하면 non-zero로 종료해 PR 체크가 빨갛게 변하고 브랜치 보호 룰이 머지를 차단합니다.

:::note 대상 독자
GitHub Actions를 사용하는 GitHub 저장소를 운영하는 엔지니어. 포털용 API Key가 필요합니다 — [API keys](../admin-guide/api-keys.md) 참고.
:::

:::note 액션 출처
모노레포의 `actions/scan/action.yml` 컴포지트 액션을 `uses: trustedoss/trusca/actions/scan@v0.22.4`로 직접 참조하세요. 독립된 Marketplace 게시는 로드맵에 있습니다.
:::

## 시작 전 준비

아래 워크플로가 돌기 전에 세 가지가 갖춰져야 합니다.

- **러너가 접근할 수 있는 포털.** GitHub 호스트 러너는
  `http://localhost:5173`에 접근할 수 없습니다 —
  [Quickstart](../quickstart.md)의 노트북 데모 스택으로는 부족합니다.
  네트워크로 접근 가능한 URL을 가진 TRUSCA 배포가 필요하고
  ([Docker Compose 설치](../installation/docker-compose.md) 참고), 그 URL이
  `api-url`이 됩니다. 같은 네트워크 안의 셀프 호스트 러너라면 내부 URL도
  됩니다.
- **API Key.** 포털의 **/integrations → API keys**에서 발급합니다 — 아래
  설정 1단계에서 다룹니다.
- **프로젝트 id.**이 저장소가 매핑되는 포털 프로젝트의 id로, **Project
  Settings → CI/CD**에서 확인합니다 — 아래 설정 3단계에서 다룹니다.

## 빠른 시작

<!-- docs-uat: id=gha-quickstart-workflow kind=manual tier=manual -->
```yaml
# .github/workflows/sca.yml
name: TRUSCA SCA
on:
  pull_request:
  push:
    branches: [main]

jobs:
  sca:
    runs-on: ubuntu-latest
    permissions:
      contents: read          # action이 체크아웃 외에 필요로 하는 권한은 없습니다
    steps:
      - uses: actions/checkout@v4
      - name: TRUSCA SCA scan
        uses: trustedoss/trusca/actions/scan@v0.22.4
        with:
          api-url: https://trustedoss.example.com
          api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
          project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
```

이게 최소 구성입니다. action은 다음을 수행합니다.

1. `kind=source`로 `POST /v1/projects/{project-id}/scans`를 호출해 cdxgen + scancode + Trivy를 큐에 넣습니다.
2. 30초마다 `GET /v1/scans/{scan-id}`를 폴링해 최종 상태(`succeeded` / `failed` / `cancelled`)에 도달할 때까지 대기, 30분 타임아웃.
3. `GET /v1/projects/{project-id}/gate-result?ref=<github.ref>`를 호출해 verdict를 워크플로의 job summary에 기록.
4. `pull_request` 이벤트에서는 `POST /v1/scans/{scan-id}/post-pr-comment`를 호출해 SCA Markdown 보고서를 PR 코멘트로 게시.
5. 게이트 verdict가 `fail`이면 1로 종료.

3단계의 `ref`가 풀 리퀘스트의 판정을 그 브랜치의 것으로 지켜 줍니다. ref 없이 프로젝트의 게이트 결과를 물으면 포털은 메인 라인에서 가장 최근에 성공한 스캔으로 답합니다. 그래서 자기 스캔이 끝나기를 기다린 PR 빌드가 정작 `main` 기준으로 판정됩니다. 자기가 넣지도 않은 심각한 취약점 때문에 막히거나, 반대로 자기가 들여온 취약점을 안은 채 통과합니다. action은 스캔을 요청할 때 보낸 `github.ref`를 그대로 다시 보내고, 포털은 양쪽을 같은 규칙으로 정규화하므로(`refs/pull/12/merge`와 `pr-12`는 같은 키입니다) 판정은 언제나 실제로 스캔한 코드를 가리킵니다.

성공한 스캔이 없는 ref는 다른 브랜치의 결과 대신 신호 없음(pass)을 돌려줍니다. 의도된 동작입니다. 브랜치를 지정했다면 그 브랜치이거나 아무것도 아니어야 합니다.

## 셋업

### 1. API Key 생성

포털에서 **/integrations → API keys → Create API key**. 스코프는 `project`를 선택하고 CI가 스캔할 프로젝트에 바인딩합니다(한 팀의 모든 프로젝트를 커버해야 한다면 `team`). "이 키가 할 수 있는 일"은 읽기 및 쓰기로 설정합니다. 스캔을 실행하는 동작이라 기본값인 읽기 전용 키는 거부됩니다. 그 외에는 발급한 사용자의 역할을 그대로 물려받습니다. 스코프와 권한 범위 모델은 [API keys](../admin-guide/api-keys.md) 참고.

### 2. GitHub에 Key 저장

저장소에서 **Settings → Secrets and variables → Actions → New repository secret**.

- Name — `TRUSTEDOSS_API_KEY`
- Value — 전체 Key(`tos_<prefix>_<secret>`)

### 3. 프로젝트 ID를 변수로 저장

같은 화면에서 **Variables**로 전환 후 추가.

- Name — `TRUSTEDOSS_PROJECT_ID`
- Value — 프로젝트의 UUID. 포털 주소 `/projects/<uuid>`의 마지막 경로 조각입니다.

(시크릿이 아니라) 변수에 두면 워크플로 로그에서 프로젝트 ID가 그대로 보입니다 — 민감 정보가 아니므로 무방합니다.

### 4. 워크플로 추가

위 `.github/workflows/sca.yml`을 저장소에 두세요. 다음 PR부터 SCA 체크가 PR 상태로 나타납니다.

## 입력

| 이름 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `api-url` | yes | — | 포털 base URL, 예: `https://trustedoss.example.com`. 끝의 슬래시는 무방. |
| `api-key` | yes | — | API Key. **항상** `${{ secrets.* }}`로 공급. |
| `project-id` | yes | — | 프로젝트 UUID. |
| `scan-kind` | no | `source` | `source`(cdxgen + scancode + Trivy) 또는 `container`(Trivy 이미지 스캔). |
| `image-ref` | `scan-kind: container`일 때 필수 | — | 포털이 받아 올 이미지. 예: `ghcr.io/acme/api:1.4.0`. 이 스텝 전에 푸시해 두세요. |
| `fail-on-gate` | no | `true` | `true`이면 게이트 verdict가 `fail`일 때 잡이 1로 종료. |
| `post-pr-comment` | no | `true` | `true`이고 `pull_request` 이벤트로 트리거되면 SCA 보고서를 PR 코멘트로 게시. |
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
| `epss-gate-count` | EPSS score가 구성된 EPSS 임계 이상인 미해결 결과 수. EPSS 게이트가 비활성(기본)이면 `0`. [EPSS로 빌드 게이팅](#epss로-빌드-게이팅-선택) 참고. |
| `malicious-component-count` | 악성 패키지 스냅샷이 지목한 컴포넌트 수. 심각도와 무관하게 빌드를 막습니다. 패키지를 제거하고 이 빌드가 닿을 수 있던 자격 증명을 교체하세요. |
| `epss-outcome` | 게이트의 EPSS 축이 무엇을 판정할 수 있었는지입니다. `not_configured`는 임계가 설정되지 않아 축이 꺼져 있었다는 뜻이고, `evaluated`는 미해결 결과에 모두 EPSS 점수가 있어 `epss-gate-count`가 완전한 답이라는 뜻입니다. `partial`은 점수가 없는 미해결 결과가 섞여 있어 `0`이 곧 임계를 넘는 것이 없었다는 증거가 되지 못한다는 뜻이고, `no_data`는 점수를 가진 결과가 하나도 없어 축이 아무것도 판정하지 못했다는 뜻입니다. 이 값을 보고하지 못하는 구버전 포털에서는 비어 있습니다. |
| `component-outcome` | 스캔의 SBOM에 무엇이 담겼는지입니다. `components_found`가 통상적인 경우입니다. `empty_no_manifests`와 `empty_with_manifests`는 둘 다 컴포넌트가 하나도 나오지 않았다는 뜻이라, 게이트 통과는 깨끗하다는 판정이 아니라 판단할 대상이 없었다는 뜻입니다. 앞은 TRUSCA가 읽지 못하는 빌드 시스템에서 정상적으로 나오는 값이고, 뒤는 스캔이 실패했다는 신호입니다. 이 값을 보고하지 못하는 구버전 포털에서는 비어 있습니다. |

후속 스텝에서 사용:

```yaml
- name: TRUSCA SCA scan
  id: sca
  uses: trustedoss/trusca/actions/scan@v0.22.4
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
- uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'
```

PR 코멘트는 그대로 게시되며 체크는 green으로 유지됩니다.

### 컨테이너 스캔

```yaml
- uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
    image-ref: ghcr.io/acme/api:${{ github.sha }}
```

컨테이너 스캔은 이미지의 OS 패키지에 Trivy를 실행합니다. `image-ref`는 필수입니다. 포털에는 프로젝트별 기본 이미지가 없고, 이 값이 없는 컨테이너 스캔은 워커에서 실패하는 대신 트리거 시점에 거부됩니다.

이미지는 포털이 직접 받아 오므로 이 스텝 전에 푸시해 두세요. 러너의 로컬 Docker 데몬에만 있는 태그는 닿지 않고, 비공개 레지스트리라면 러너가 아니라 포털에 자격 증명을 설정해야 합니다.

### 소스와 컨테이너 둘 다

서로 다른 `id`로 두 스텝 실행:

```yaml
- name: SCA — source
  uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: source

- name: SCA — container
  uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
    image-ref: ghcr.io/acme/api:${{ github.sha }}
```

기본적으로 어느 한 스텝의 실패가 잡 전체를 실패시킵니다.

두 스텝은 같은 포털 프로젝트를 스캔하고 포털은 `(project, ref)` 하나당 진행 중인 스캔을 하나만 허용하므로, 위처럼 순서대로 두세요. 병렬 잡으로 나누면 뒤엣것이 자기 스캔을 시작하는 대신 앞엣것에 붙습니다.

### 브랜치별 게이트

`main`에서만 게이트를 적용하고 PR에서는 advisory:

```yaml
- uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && 'true' || 'false' }}
```

### EPSS로 빌드 게이팅 (선택)

빌드 게이트는 기본적으로 Critical CVE와 금지 라이선스를 평가합니다. 여기에 EPSS 차원을 더하면 악용 예측 확률이 높은 CVE가 **Critical이 아니어도** 빌드를 실패시킬 수 있습니다 — 가장 공격받기 쉬운 소수의 결과를 잡는 데 유용합니다.

이는 워크플로 입력이 아니라 **운영자 측, 조직 단위** 스위치입니다. 포털(`.env`)에 `GATE_EPSS_THRESHOLD` 환경변수를 설정한 뒤 백엔드를 재기동하세요. **기본은 비활성**입니다 — 미설정으로 두면 기존 Critical-CVE / 금지-라이선스 게이트가 그대로 보존됩니다.

<!-- docs-uat: id=gha-epss-threshold-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 포털의 .env(CI 워크플로가 아님), 0~1 사이 값:
GATE_EPSS_THRESHOLD=0.5
```

임계가 설정되면 미해결 결과 중 `epss_score >= GATE_EPSS_THRESHOLD`인 것이 있을 때도 게이트가 실패합니다. 게이트 결과에는 추가 필드 `epss_gate_count`(위반 결과 수)와 `epss_threshold`(구성된 값)가 실리며, action은 `epss-gate-count`를 [출력](#출력)으로 노출합니다. EPSS 값이 없는 결과는 게이트를 트리거하지 않습니다(누락된 score는 `>=`를 만족할 수 없음). 전체 레퍼런스는 [`GATE_EPSS_THRESHOLD`](../reference/env-variables.md#빌드--정책-게이트), 개념은 [EPSS — 악용 확률](../user-guide/vulnerabilities.md#epss--악용-확률) 참고.

#### 임계를 평가할 수 없을 때

`epss_gate_count`는 임계를 넘는 것이 없을 때도 `0`이고 점수가 매겨진 것이 하나도
없을 때도 `0`인데, 둘은 같은 결과가 아닙니다. EPSS 값은 **기본값이 꺼짐**인 일간
동기화(`EPSS_REFRESH_ENABLED`)가 채웁니다. 그래서 한 번도 켠 적이 없는 배포나
미러에 닿지 못하는 배포에서는 모든 미해결 결과의 점수가 NULL이고, 설정한 임계가
아무것도 판정하지 못합니다. 이 사실을 보고하기 전에는 그 상태가 빌드 통과로
보였습니다.

어느 경우인지는 `epss-outcome` [출력](#출력)이 알려 주고, action은 `no_data`와
`partial`에 대해 작업 요약 행과 경고 어노테이션을 남깁니다. 판정을 어떻게 할지는
포털의 `GATE_EPSS_ON_MISSING_DATA`가 정합니다.

<!-- docs-uat: id=gha-epss-on-missing-data kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 기본값. 판정하지 못한 EPSS 축은 빌드를 통과시킵니다. 이 옵션이 생기기 전에
# 모든 배포가 하던 동작입니다.
GATE_EPSS_ON_MISSING_DATA=allow

# 대신 빌드를 실패시킵니다. 설정한 임계가 조용히 무시되지 않습니다.
GATE_EPSS_ON_MISSING_DATA=block
```

`block`은 `no_data`에만 적용됩니다. `partial`에는 의도적으로 적용하지 않습니다.
EPSS는 모든 CVE에 점수를 매기지 않아 동기화가 정상이어도 빈 자리가 생기는데, 흔한
상태에서 발화하는 옵션은 아무도 켜 둘 수 없고, 꺼 둔 안전장치는 아무것도 지키지
않기 때문입니다.

### 태그 핀

릴리스 태그는 지금은 커밋 하나를 가리키지만 태그는 옮기거나 지울 수 있습니다.
재현성이 필요하면 커밋 자체에 고정하세요.

```yaml
- uses: trustedoss/trusca/actions/scan@176bc3f0632bf0cf209c443da308e3d863dfde44  # v0.22.4
```

## ref가 하는 일 {#how-the-ref-becomes-a-retention-key}

액션은 워크플로의 ref를 스캔 metadata로 자동 전달합니다. push에서는 `github.ref`(`refs/heads/<branch>`), `pull_request` 이벤트에서는 PR 번호(`refs/pull/<n>/merge`)입니다. 이 값은 두 가지 일을 합니다.

포털이 이 ref를 체크아웃합니다. 워커가 보낸 ref를 가져와 그 트리를 스캔하므로, 풀 리퀘스트의 스캔은 풀 리퀘스트의 의존성을 봅니다. 워커가 스캔을 집어들 시점에 그 ref가 사라졌다면(대기 중에 머지되거나 force-push된 경우) 기본 브랜치로 넘어가고 `metadata.ref_fallback`에 그 사실을 기록합니다.

보존 키이기도 합니다. 포털은 ref를 정규화하고(`refs/heads/main` → `main`, `refs/pull/12/merge` → `pr-12`) `(project, 정규화된 ref)`로 스캔을 묶습니다. 키별로 가장 최근 성공 스캔이 살아 있고 이전 것을 대체합니다.

이를 위해 설정할 것은 없습니다 — `push`·`pull_request`에서 액션을 실행하면 브랜치별·PR별 그룹화가 즉시 올바르게 동작합니다. 스캔을 영구 보존하려면(태그 릴리스용) `metadata.release` 라벨과 함께 트리거하십시오. 전체 모델과 release 면제는 [스캔 보존](../admin-guide/scan-retention.md) 페이지에서 다룹니다.

## PR 코멘트는 어떻게 게시되나

PR 코멘트는 워크플로가 아니라 **포털이 서버 측에서** 게시합니다. 액션이 SCA 결과를 업로드한 뒤, 포털이 빌드 게이트를 평가하고 코멘트 게시가 활성화되어 있다면 포털 환경에 저장된 GitHub PAT(`GITHUB_TOKEN` 또는 `TRUSTEDOSS_GITHUB_TOKEN`)를 사용해 `https://api.github.com`을 직접 호출합니다. 워크플로는 절대로 `secrets.GITHUB_TOKEN`을 포털로 전달하지 않습니다. 포털에 저장된 installation 토큰을 가진 정식 GitHub App은 로드맵에 있습니다.

코멘트는 멱등합니다. 같은 PR에서 워크플로를 다시 실행하면 기존 코멘트가 그 자리에서 갱신됩니다. 포털은 코멘트 본문의 마커 `<!-- trustedoss-sca-bot -->`로 이를 찾습니다. 찾는 범위가 PR의 최근 코멘트 500개까지라, 그보다 긴 스레드에서는 갱신 대신 새 코멘트를 답니다.

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

타임아웃은 기다리기를 멈출 뿐 스캔을 멈추지 않습니다. 포털은 계속 돌리고 있습니다. 그래서 워크플로를 바로 다시 실행하면 새 스캔을 만드는 대신 그 스캔에 붙어 기다리고, 끝나는 즉시 완료됩니다.

### "Attaching to the scan already running for this ref"

오류가 아닙니다. 포털은 `(project, ref)` 하나당 진행 중인 스캔을 하나만 허용하므로, 실행이 자기 ref가 이미 바쁜 것을 보면 실패하는 대신 기존 스캔을 기다립니다.

흔한 원인은 이 워크플로의 이전 실행입니다. `cancel-in-progress: true`는 러너를 취소하지만 그 러너가 띄운 스캔은 서버에서 계속 돌기 때문에, 새로 뜬 실행이 아무도 지켜보지 않는 스캔과 부딪힙니다. 붙어서 기다리면 이 상황이 평범한 대기로 바뀝니다.

붙은 스캔이 같은 브랜치의 다른 커밋에서 시작된 것이라면 그 판정은 그 커밋을 설명합니다. 더 새 커밋을 채점하려면 스캔이 끝난 뒤 다시 실행하세요.

### 트리거가 레이트 리밋에 걸림 (`429`)

action은 `Retry-After`를 존중해 최대 네 번까지 다시 시도한 뒤 실패합니다. 보통은 API Key 하나를 여러 저장소에서 함께 쓰는 것이 원인입니다. 제한은 Key 단위이므로 저장소마다 Key를 따로 발급하세요.

### action에서 `403 Forbidden`

호출 대상 프로젝트가 API Key 스코프에 포함되지 않습니다. 해당 프로젝트에 바인딩된 스코프 `project` (권장)로 재발급하거나, 팀의 모든 프로젝트에 도달해야 한다면 스코프 `team`로 발급. 프로젝트가 해당 스코프 팀에 속하는지 확인. [API keys](../admin-guide/api-keys.md) 참고.

### PR 코멘트가 표시되지 않음

세 가지 가능성:

- 워크플로가 `pull_request`가 아닌 `push`로 트리거됨 — PR 이벤트만 코멘트를 받음.
- 포털의 `GITHUB_TOKEN` / `TRUSTEDOSS_GITHUB_TOKEN` 환경변수가 없거나 만료됐거나, 대상 저장소의 풀 리퀘스트에 쓰기 권한이 없습니다. 코멘트는 워크플로의 `GITHUB_TOKEN`이 아니라 포털이 자기 자격 증명으로 게시하므로, 잡에 권한을 더 줘도 달라지지 않습니다. 운영자가 포털 `.env`의 토큰을 교체하거나 기간을 늘린 뒤 백엔드를 재기동하세요.
- 포털에 전달된 PR 번호가 잘못됐거나 비어 있습니다. action은 이 값을 `github.event.pull_request.number`에서 읽는데, `pull_request` 이벤트가 아니면 비어 있습니다.

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
- [스캔 보존](../admin-guide/scan-retention.md) — 브랜치별·PR별 스캔이 보존·회수되는 방식
