---
id: jenkins
title: Jenkins
description: 번들된 Jenkinsfile 스니펫을 사용해 TRUSCA를 Jenkins declarative pipeline에 연결합니다.
sidebar_label: Jenkins
sidebar_position: 3
---

# Jenkins

포털은 Jenkins 플러그인을 제공하지 않습니다. 대신, 작은 declarative-pipeline 스니펫이 포털의 REST API를 직접 호출합니다. 통합이 감사 가능하게 유지되고 특정 Jenkins 버전에 묶이지 않습니다.

:::note 대상 독자
Jenkins controller / agent를 운영하는 엔지니어. declarative pipeline과 Credentials 플러그인에 익숙해야 합니다.
:::

## 빠른 시작 {#quick-start}

<!-- docs-uat: id=jenkins-quickstart-pipeline kind=manual tier=manual -->
```groovy
// Jenkinsfile
pipeline {
  agent any

  environment {
    TRUSTEDOSS_API_URL    = 'https://trustedoss.example.com'
    TRUSTEDOSS_PROJECT_ID = '01H7XYZ…'
  }

  stages {
    stage('TRUSCA SCA') {
      steps {
        withCredentials([string(credentialsId: 'trustedoss-api-key',
                                variable: 'TRUSTEDOSS_API_KEY')]) {
          sh '''
            set -eu
            curl --version >/dev/null
            jq --version  >/dev/null

            SCAN_ID=$(curl -fsS -X POST \
              -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
              -H "Content-Type: application/json" \
              -d '{"kind": "source"}' \
              "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/scans" \
              | jq -r .id)
            echo "scan_id=${SCAN_ID}"

            # 최종 상태까지 폴링 (타임아웃 30분, 30초마다).
            # 스캔이 실패했거나 끝내 끝나지 않았다면 여기서 빌드를 세워야
            # 합니다. 게이트는 마지막으로 성공한 스캔을 읽으므로, 그냥
            # 넘어가면 이번 빌드가 예전 스캔으로 채점되어 아무도 받지 않은
            # 통과가 나옵니다.
            FINISHED=""
            for _ in $(seq 1 60); do
              STATUS=$(curl -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
                "${TRUSTEDOSS_API_URL}/v1/scans/${SCAN_ID}" | jq -r .status)
              echo "status=${STATUS}"
              case "${STATUS}" in
                succeeded) FINISHED=yes; break ;;
                failed|cancelled)
                  echo "스캔이 ${STATUS} 로 끝남; 오래된 스캔으로 채점하지 않습니다" >&2
                  exit 1 ;;
              esac
              sleep 30
            done
            if [ -z "${FINISHED}" ]; then
              echo "스캔이 30분 안에 끝나지 않음 (포털에서는 계속 실행 중)" >&2
              exit 1
            fi

            # 방금 스캔한 ref 기준으로 게이트를 평가합니다. ref= 가 없으면
            # 포털은 메인 라인에서 가장 최근에 성공한 스캔으로 답합니다.
            set -- -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
              --get "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/gate-result"
            if [ -n "${SCAN_REF}" ]; then
              set -- "$@" --data-urlencode "ref=${SCAN_REF}"
            fi
            GATE=$(curl "$@" | jq -r .gate)
            echo "gate=${GATE}"
            test "${GATE}" = "pass"
          '''
        }
      }
    }
  }
}
```

레포 루트에 `Jenkinsfile`로 저장. agent에 `bash`, `curl`, `jq`가 설치되어 있어야 합니다.

## 셋업

### 1. API Key 생성

포털에서 **/integrations → API keys → Create API key**. 이 잡이 스캔할 프로젝트에 `project` scope로 묶거나, 팀이 소유한 모든 프로젝트를 덮으려면 `team`을 고르세요. "이 키가 할 수 있는 일"은 읽기 및 쓰기로 설정하세요. 스캔을 실행하는 동작이라 기본값인 읽기 전용 키는 거부됩니다. 그 외에는 발급자의 역할을 그대로 물려받습니다. [API keys](../admin-guide/api-keys.md) 참고.

### 2. Jenkins credential로 Key 추가

1. **Jenkins → Manage Jenkins → Credentials**.
2. 도메인 선택(보통 Global) → **Add Credentials**.
3. Kind — **Secret text**.
4. Secret — API Key.
5. ID — `trustedoss-api-key`(`withCredentials` 블록과 매칭).

credential 값은 콘솔 출력에서 마스킹됩니다.

### 3. 파이프라인 잡 생성

- New item → **Pipeline**(피처 브랜치가 있는 레포라면 **Multibranch Pipeline**).
- Pipeline definition — **Pipeline script from SCM**.
- SCM — **Git**을 선택하고 레포 URL과 빌드 대상 브랜치를 입력합니다.

파이프라인은 매 빌드마다 SCA 스테이지를 실행합니다.

## 레시피

### Shared library 사용

Jenkins shared library를 운영한다면 SCA 호출을 step으로 감싸세요.

```groovy
// shared library의 vars/trustedossSCA.groovy
def call(Map config = [:]) {
  withCredentials([string(credentialsId: config.credentialsId ?: 'trustedoss-api-key',
                          variable: 'TRUSTEDOSS_API_KEY')]) {
    sh """
      set -eu
      # …빠른 시작과 같은 본문…
    """
  }
}
```

`Jenkinsfile`에서:

```groovy
@Library('shared') _

pipeline {
  agent any
  stages {
    stage('SCA') { steps { trustedossSCA() } }
  }
}
```

### PR(multibranch) 데코레이션

Multibranch Pipelines에서 change request 상태(`CHANGE_ID`, `CHANGE_BRANCH`)로 PR이 아닌 빌드를 건너뛸 수 있습니다.

```groovy
when {
  anyOf {
    branch 'main'
    expression { env.CHANGE_ID != null }
  }
}
```

### Advisory 모드(빌드를 실패시키지 않음)

마지막 `test "${GATE}" = "pass"` 라인을 다음으로 교체:

<!-- docs-uat: id=jenkins-warn-gate-snippet kind=shell ctx=host tier=manual waiver=jenkins-pipeline-snippet-not-standalone -->
```bash
echo "::warning::TRUSCA gate=${GATE}"
```

빌드는 green을 유지하며 게이트 verdict는 콘솔 로그에만 기록됩니다.

### SBOM을 빌드 아티팩트로 게시

SBOM 내보내기 엔드포인트는 이번 릴리스에서 API Key를 받지 않습니다. 사용자
access token이 필요해서(`require_role("developer")`) API Key로 부르면 401이
납니다. 그래서 CI 잡이 SBOM을 보관하려면 JWT를 넘겨야 하고, 이는 파이프라인이
사용자 자격 증명을 다루게 된다는 뜻입니다. 이 점을 감안해 채택하세요. 포털 UI에서
SBOM을 내려받으면 이 문제 자체가 없습니다.

`TRUSTEDOSS_JWT`로 토큰을 쓸 수 있다면:

```groovy
sh '''
  curl -fsS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_JWT}" \
    "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/sbom?format=cyclonedx-json"
'''
archiveArtifacts artifacts: 'sbom-*.cdx.json', fingerprint: true
```

`-OJ`는 응답이 알려 주는 파일명을 씁니다. 포털이 보내는 이름은
`sbom-<프로젝트-slug>.cdx.json`이므로 `*.cyclonedx.json`이 아니라 이 형태로
보관 패턴을 맞추세요.

## 브랜치 보호 (GitHub / GitLab 없이)

순수 Jenkins는 Git 호스트의 PR / MR 체크 상태를 강제하지 않습니다 — 그것은 호스트의 일입니다. 다음 중 하나를 사용:

- **Multibranch 플러그인 + GitHub PR** — 상태는 GitHub Checks API로 보고. GitHub에서 Jenkins 체크를 요구하도록 브랜치 보호.
- **GitLab MR + Jenkins** — GitLab 플러그인을 설치해 빌드 상태를 게시. GitLab에서 파이프라인 통과를 요구하도록 브랜치 보호.
- **Bitbucket / Gitea** — 등가의 status-publisher 플러그인 설치.

TRUSCA 게이트는 와이어링을 바꾸지 않습니다 — 빌드의 종료 상태만 바꿉니다.

## 멱등성

Jenkins 빌드를 재실행하면 새 스캔이 발급되고, 포털은 둘 다 프로젝트 이력에 남깁니다. 게이트는 빌드가 보낸 ref에서 가장 최근에 성공한 스캔만 읽으므로, 이전 스캔은 이력에 보이되 판정을 움직이지 않습니다.

ref는 다른 이유로도 중요합니다. 포털은 `(project, ref)` 하나당 진행 중인 스캔을 하나만 허용합니다. 그래서 이전 스캔이 아직 대기 중이거나 실행 중인 빌드는 같은 대상에 스캔을 하나 더 만드는 대신 409를 받습니다. 함께 제공하는 `Jenkinsfile.example`은 `CHANGE_ID`(멀티브랜치 PR 빌드), `TAG_NAME`, `BRANCH_NAME`, `GIT_BRANCH` 순으로 ref를 정합니다. ref를 보내지 않는 잡은 모든 빌드가 하나의 임시 묶음에 들어가고, 두 브랜치가 동시에 빌드되면 충돌합니다. `disableConcurrentBuilds()`는 브랜치별 잡 단위라 이를 막지 못합니다.

## 트러블슈팅

### agent에서 `curl: command not found`

agent 이미지가 너무 미니멀입니다. 이미지에 `curl`과 `jq`를 추가하거나 `docker` agent를 사용하세요.

```groovy
agent {
  docker { image 'alpine:3.20'; args '-u root' }
}
options { skipDefaultCheckout(false) }
```

### `fail` 게이트인데 파이프라인이 조용히 통과

셸 블록 상단의 `set -eu`가 필수입니다 — 이게 없으면 non-zero `test`가 Jenkins로 전파되지 않습니다. shebang과 `set -eu`가 있는지 확인.

### 로그에 credential이 노출됨

credential이 `withCredentials`로 감싸져 있고 `${TRUSTEDOSS_API_KEY}`가 그 블록 내부에서만 확장되는지 확인. Jenkins는 `withCredentials`에서 비롯된 값만 stdout/stderr에서 마스킹합니다.

### 긴 스캔에서 네트워크 타임아웃

실제 ORT 스캔은 30~60분이 걸릴 수 있습니다. 폴링 루프 한도를 늘리세요.

<!-- docs-uat: id=jenkins-poll-loop-snippet kind=shell ctx=host tier=manual waiver=illustrative-loop-with-ellipsis-not-runnable -->
```bash
for _ in $(seq 1 120); do … sleep 30; done   # 60분
```

Jenkins 빌드 타임아웃은 별도 설정입니다 — 잡의 "Abort the build if it's stuck"을 최악 시나리오 스캔보다 큰 값으로 설정.

## 함께 보기

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [Webhooks](./webhooks.md)
- [API overview](../reference/api-overview.md)
