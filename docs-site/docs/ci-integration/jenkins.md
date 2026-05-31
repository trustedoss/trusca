---
id: jenkins
title: Jenkins
description: Wire TrustedOSS Portal into a Jenkins declarative pipeline using the bundled Jenkinsfile snippet.
sidebar_label: Jenkins
sidebar_position: 3
---

# Jenkins

The portal does not ship a Jenkins plugin. Instead, a small declarative-pipeline snippet calls the portal's REST API directly. This keeps the integration auditable and avoids tying you to a specific Jenkins version.

:::note Audience
Engineers maintaining a Jenkins controller / agent. Familiarity with declarative pipelines and the Credentials plugin.
:::

## Quick start

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
    stage('TrustedOSS SCA') {
      steps {
        withCredentials([string(credentialsId: 'trustedoss-api-key',
                                variable: 'TRUSTEDOSS_API_KEY')]) {
          sh '''
            set -eu
            curl --version >/dev/null
            jq --version  >/dev/null

            # scan-retention: forward the branch as metadata.ref so the portal
            # groups this branch's scans under one retention key (the latest
            # supersedes the prior). BRANCH_NAME is set by Multibranch / PR jobs;
            # GIT_BRANCH (often "origin/<branch>") is the fallback — adjust to
            # your job's variables. Empty ref is omitted.
            SCAN_REF="${BRANCH_NAME:-${GIT_BRANCH:-}}"
            BODY=$(jq -nc --arg ref "${SCAN_REF}" \
              '{kind: "source", metadata: ({source: "jenkins"} + (if $ref == "" then {} else {ref: $ref} end))}')
            SCAN_ID=$(curl -fsS -X POST \
              -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
              -H "Content-Type: application/json" \
              -d "${BODY}" \
              "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/scans" \
              | jq -r .id)
            echo "scan_id=${SCAN_ID}"

            # Poll until terminal (timeout 30 min, every 30 s).
            for _ in $(seq 1 60); do
              STATUS=$(curl -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
                "${TRUSTEDOSS_API_URL}/v1/scans/${SCAN_ID}" | jq -r .status)
              echo "status=${STATUS}"
              case "${STATUS}" in
                succeeded|failed|cancelled) break ;;
              esac
              sleep 30
            done

            # Evaluate the gate.
            GATE=$(curl -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
              "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/gate-result" \
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

Save as `Jenkinsfile` at the repo root. Make sure the agent has `bash`, `curl`, and `jq` installed.

## Setup

### 1. Generate an API key

In the portal: **Project Settings → CI/CD → API keys → New API key** with `scan:trigger`, `scan:read`, `report:download`. See [API keys](../admin-guide/api-keys.md).

### 2. Add the key as a Jenkins credential

1. **Jenkins → Manage Jenkins → Credentials**.
2. Choose a domain (typically Global) → **Add Credentials**.
3. Kind: **Secret text**.
4. Secret: the API key.
5. ID: `trustedoss-api-key` (matches the `withCredentials` block).

The credential's value is masked in console output.

### 3. Create the pipeline job

- New item → **Pipeline** (or **Multibranch Pipeline** for repos with feature branches).
- Pipeline definition: **Pipeline script from SCM**.
- SCM: Git → your repo URL → branches to build.

The pipeline runs the SCA stage on every build.

## Recipes

### Use a shared library

If you maintain a Jenkins shared library, wrap the SCA call into a step:

```groovy
// vars/trustedossSCA.groovy in your shared library
def call(Map config = [:]) {
  withCredentials([string(credentialsId: config.credentialsId ?: 'trustedoss-api-key',
                          variable: 'TRUSTEDOSS_API_KEY')]) {
    sh """
      set -eu
      # …same body as the quick-start…
    """
  }
}
```

Then in `Jenkinsfile`:

```groovy
@Library('shared') _

pipeline {
  agent any
  stages {
    stage('SCA') { steps { trustedossSCA() } }
  }
}
```

### PR (multibranch) decoration

For Multibranch Pipelines, the change request status (`CHANGE_ID`, `CHANGE_BRANCH`) lets you skip non-PR builds:

```groovy
when {
  anyOf {
    branch 'main'
    expression { env.CHANGE_ID != null }
  }
}
```

### Advisory mode (don't fail the build)

Replace the final `test "${GATE}" = "pass"` line with:

<!-- docs-uat: id=jenkins-warn-gate-snippet kind=shell ctx=host tier=manual waiver=jenkins-pipeline-snippet-not-standalone -->
```bash
echo "::warning::TrustedOSS gate=${GATE}"
```

The build stays green; the gate verdict is recorded in the console log only.

### Post the SCA report as a build artifact

```groovy
sh '''
  curl -fsS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
    "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/sbom?format=cyclonedx-json"
'''
archiveArtifacts artifacts: '*.cyclonedx.json', fingerprint: true
```

The SBOM is attached to the build and downloadable from the Jenkins UI.

## Branch protection (without GitHub / GitLab)

Native Jenkins does not enforce check status on a Git host's PR / MR — that is the host's job. If you use:

- **GitHub PRs** with Jenkins via the Multibranch plugin: status is reported via the GitHub Checks API. Branch-protect on GitHub by requiring the Jenkins check.
- **GitLab MRs** with Jenkins: install the GitLab plugin to publish the build status. Branch-protect on GitLab by requiring the pipeline to pass.
- **Bitbucket / Gitea**: install the equivalent status-publisher plugin.

The TrustedOSS gate does not change the wiring — it only changes the build's exit status.

## Idempotency

Re-running a Jenkins build re-issues a new scan. The portal stores both. If you want only the latest scan to inform the gate, the gate-result endpoint already returns the verdict for the **latest scan** of the project — older scans live in the project history but do not move the gate.

## Troubleshooting

### `curl: command not found` on the agent

The agent image is too minimal. Either add `curl` and `jq` to the image, or use a `docker` agent:

```groovy
agent {
  docker { image 'alpine:3.20'; args '-u root' }
}
options { skipDefaultCheckout(false) }
```

### Pipeline silently passes despite a `fail` gate

The `set -eu` at the top of the shell block is essential — without it, a non-zero `test` does not propagate to Jenkins. Confirm the shebang and `set -eu` are present.

### Credential is leaked in the log

Confirm the credential is wrapped in `withCredentials` and that the shell expands `${TRUSTEDOSS_API_KEY}` only inside the wrapped block. Jenkins masks the value in stdout/stderr only when sourced from `withCredentials`.

### Network timeouts on long scans

Real ORT scans can take 30–60 minutes. Bump the polling loop bound:

<!-- docs-uat: id=jenkins-poll-loop-snippet kind=shell ctx=host tier=manual waiver=illustrative-loop-with-ellipsis-not-runnable -->
```bash
for _ in $(seq 1 120); do … sleep 30; done   # 60 minutes
```

The Jenkins build timeout itself is a separate setting — set the job's "Abort the build if it's stuck" to a value larger than your worst-case scan.

## See also

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [Webhooks](./webhooks.md)
- [API overview](../reference/api-overview.md)
