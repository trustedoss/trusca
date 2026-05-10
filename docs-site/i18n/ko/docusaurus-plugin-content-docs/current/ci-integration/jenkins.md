---
id: jenkins
title: Jenkins
description: 번들된 Jenkinsfile 스니펫을 사용해 TrustedOSS Portal을 Jenkins declarative pipeline에 연결합니다.
sidebar_label: Jenkins
sidebar_position: 3
---

# Jenkins

포털은 Jenkins 플러그인을 제공하지 않습니다. 대신, 작은 declarative-pipeline 스니펫이 포털의 REST API를 직접 호출합니다. 통합이 감사 가능하게 유지되고 특정 Jenkins 버전에 묶이지 않습니다.

:::note 대상 독자
Jenkins controller / agent를 운영하는 엔지니어. declarative pipeline과 Credentials 플러그인에 익숙해야 합니다.
:::

## 빠른 시작

> **Tip**: 자격 증명 와이어링, 데드라인 폴링, PR 코멘트 스테이지가 포함된 복사·붙여넣기용 5-스테이지 예시가 [`templates/Jenkinsfile.example`](https://github.com/trustedoss/trustedoss-portal/blob/main/templates/Jenkinsfile.example)에 있습니다 — 새 파이프라인의 권장 출발점입니다.

```groovy
// Jenkinsfile
pipeline {
  agent any

  environment {
    TRUSTEDOSS_API_URL    = 'https://trustedoss.example.com'
    TRUSTEDOSS_PROJECT_ID = '01H7XYZ…'
  }

  stages {
    stage('TrustedOSS SCA') {
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
              "${TRUSTEDOSS_API_URL}/api/v1/projects/${TRUSTEDOSS_PROJECT_ID}/scans" \
              | jq -r .id)
            echo "scan_id=${SCAN_ID}"

            # 최종 상태까지 폴링 (타임아웃 30분, 30초마다).
            for _ in $(seq 1 60); do
              STATUS=$(curl -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
                "${TRUSTEDOSS_API_URL}/api/v1/scans/${SCAN_ID}" | jq -r .status)
              echo "status=${STATUS}"
              case "${STATUS}" in
                succeeded|failed|cancelled) break ;;
              esac
              sleep 30
            done

            # 게이트 평가.
            GATE=$(curl -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
              "${TRUSTEDOSS_API_URL}/api/v1/projects/${TRUSTEDOSS_PROJECT_ID}/gate-result" \
              | jq -r .gate)
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

포털에서 **Project Settings → CI/CD → API keys → New API key**. `scope` 를 `project` 로 설정해 키가 이 프로젝트에만 묶이게 합니다. v2.0.0 에는 동작 단위 capability 목록이 없습니다 — Key 는 발급자의 역할을 상속하며 키별로 강제되는 것은 scope 뿐입니다. [API keys](../admin-guide/api-keys.md) 참고.

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
- SCM — Git → 레포 URL → 빌드 대상 브랜치.

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

Jenkins multibranch를 통한 PR 코멘트는 GitHub만 지원됩니다(GitLab과 동일한 백엔드 제약 — GitLab CI 페이지의 경고 참고). 포털이 자체 GitHub PAT로 서버 사이드에서 코멘트를 게시하며, Jenkins 자체는 어떤 토큰도 중계하지 않습니다.

### Advisory 모드(빌드를 실패시키지 않음)

마지막 `test "${GATE}" = "pass"` 라인을 다음으로 교체:

```bash
echo "::warning::TrustedOSS gate=${GATE}"
```

빌드는 green을 유지하며 게이트 verdict는 콘솔 로그에만 기록됩니다.

### SCA 보고서를 빌드 아티팩트로 게시

```groovy
sh '''
  curl -fsS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
    "${TRUSTEDOSS_API_URL}/api/v1/projects/${TRUSTEDOSS_PROJECT_ID}/sbom?format=cyclonedx-json"
'''
archiveArtifacts artifacts: '*.cyclonedx.json', fingerprint: true
```

SBOM이 빌드에 첨부되어 Jenkins UI에서 다운로드 가능합니다.

## 브랜치 보호 (GitHub / GitLab 없이)

순수 Jenkins는 Git 호스트의 PR / MR 체크 상태를 강제하지 않습니다 — 그것은 호스트의 일입니다. 다음 중 하나를 사용:

- **Multibranch 플러그인 + GitHub PR** — 상태는 GitHub Checks API로 보고. GitHub에서 Jenkins 체크를 요구하도록 브랜치 보호.
- **GitLab MR + Jenkins** — GitLab 플러그인을 설치해 빌드 상태를 게시. GitLab에서 파이프라인 통과를 요구하도록 브랜치 보호.
- **Bitbucket / Gitea** — 등가의 status-publisher 플러그인 설치.

TrustedOSS 게이트는 와이어링을 바꾸지 않습니다 — 빌드의 종료 상태만 바꿉니다.

## 멱등성

Jenkins 빌드를 재실행하면 새 스캔이 발급됩니다. 포털은 둘 다 저장합니다. 게이트가 최신 스캔만 보길 원한다면 — gate-result 엔드포인트는 이미 프로젝트의 **최신 스캔** verdict를 반환합니다. 이전 스캔은 프로젝트 이력에 남지만 게이트를 움직이지 않습니다.

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

```bash
for _ in $(seq 1 120); do … sleep 30; done   # 60분
```

Jenkins 빌드 타임아웃은 별도 설정입니다 — 잡의 "Abort the build if it's stuck"을 최악 시나리오 스캔보다 큰 값으로 설정.

## 함께 보기

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [Webhooks](./webhooks.md)
- [API overview](../reference/api-overview.md)
