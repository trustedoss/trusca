---
id: scan-retention
title: 스캔 보존(retention)
description: 포털이 브랜치·PR별 최신 스캔을 live로 유지하고, superseded 스냅샷을 grace 후 회수하며, release 라벨 스캔을 영구 보존하는 방식과 스캔을 수동 삭제하는 법.
sidebar_label: 스캔 보존
sidebar_position: 8
---

# 스캔 보존(retention)

CI·웹훅 자동화는 push·pull request·merge request 마다 스캔을 트리거합니다. 제한이 없으면 프로젝트당 거의 동일한 스냅샷이 수천 개 쌓입니다. 포털은 이력을 유용하게, 디스크를 유한하게 유지하기 위해 **보존 모델**을 둡니다 — 타겟별 최신 성공 스캔만 *live*로 남고, 더 오래된 스냅샷은 *superseded* 되어 grace 윈도우 후 회수되며, release로 명시한 스캔은 영구 보존됩니다.

:::note 대상 독자
자동 스캔을 수신하는 포털을 운영하는 `super_admin` 및 `team_admin`. `.env` 편집과 `docker-compose restart`에 익숙해야 합니다. 스캔 단위 라이프사이클(queued → running → succeeded)은 [스캔](../user-guide/scans.md)을 참고하십시오.
:::

## 보존 모델 {#the-retention-model}

모든 스캔은 자신의 프로젝트와 실행 대상 브랜치·PR의 **정규화된 ref**에서 파생된 **보존 타겟**을 가집니다. 동일한 타겟에 더 새로운 성공 스캔이 도착하면 이전 스캔은 **superseded** 됩니다.

```
target = (project_id, normalized_ref)

scan #1 on main  ──► live
scan #2 on main  ──► live, scan #1 superseded ──► grace 후 회수
scan #3 on main  ──► live, scan #2 superseded ──► grace 후 회수
```

- **Live** — 타겟의 가장 최근 성공 스캔. 항상 조회 가능하며 나이만으로는 회수되지 않습니다.
- **Superseded** — 동일 타겟의 더 새로운 성공 스캔으로 대체된, 이전에 live였던 스캔. diff·롤백을 위해 grace 윈도우(`SCAN_RETENTION_SUPERSEDED_GRACE_DAYS`, 기본 7일) 동안 보존된 뒤 sweep이 회수합니다.
- **Release** — `metadata.release` 라벨이 설정된 스캔. **불변이며 영구**입니다 — 나이나 supersession과 무관하게 sweep이 절대 건드리지 않습니다. [스캔을 영구 보존하는 법](#keep-a-scan-forever-release-label)을 참고하십시오.
- **Ref-less / 실패** — ref 타겟이 없는 스캔(ad-hoc UI 스캔)과 실패 스캔은 supersession 체인에 속하지 않습니다. 프로젝트당 하한(`SCAN_RETENTION_KEEP_LAST`, 기본 30)과 age 상한(`SCAN_RETENTION_MAX_AGE_DAYS`, 기본 180)으로 보호됩니다.

### ref 정규화 {#ref-normalization}

보존 타겟은 ref의 **정규화된** 형태를 사용하므로, CI가 어떻게 표기하든 동일한 논리적 브랜치·PR이 함께 묶입니다. 포털은 `metadata.ref`로 수신한 ref를 다음과 같이 정규화합니다.

| 수신 ref | 정규화 | 비고 |
|---|---|---|
| `refs/heads/main` | `main` | 브랜치 ref는 `refs/heads/` prefix를 제거합니다. |
| `refs/pull/12/merge` | `pr-12` | GitHub PR merge ref는 `pr-<number>`가 됩니다. |
| `refs/merge-requests/7/head` | `mr-7` | GitLab MR ref는 `mr-<iid>`가 됩니다. |
| `main`, `release/2.0` | `main`, `release/2.0` | bare 브랜치명은 그대로 유지합니다. |

[GitHub Action](../ci-integration/github-actions.md#how-the-ref-becomes-a-retention-key)은 `github.ref`(또는 PR 번호)를, [GitLab CI 템플릿](../ci-integration/gitlab-ci.md#how-the-ref-becomes-a-retention-key)은 `CI_COMMIT_REF_NAME` / MR IID를 전달하므로 설정 없이 올바르게 그룹화됩니다. [Jenkinsfile 스니펫](../ci-integration/jenkins.md#quick-start)도 동일하게 `BRANCH_NAME`을 전달합니다.

## 보존 정책 변수 {#retention-policy-variables}

네 키 모두 런타임에 `os.getenv`로 읽힙니다 — `.env`를 편집하고 `worker`·`beat`를 재시작하면 적용됩니다. 정식 레퍼런스는 [환경변수 → 스캔 보존](../reference/env-variables.md#scan-retention)을 참고하십시오.

<!-- docs-uat: id=scan-retention-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 포털의 .env
SCAN_RETENTION_SUPERSEDED_GRACE_DAYS=7    # superseded 스냅샷을 회수 전 이만큼 보존
SCAN_RETENTION_KEEP_LAST=30               # ref-less / 실패 스캔의 프로젝트당 하한
SCAN_RETENTION_MAX_AGE_DAYS=180           # ref-less / 실패 스캔의 age 상한
```

| 키 | 기본값 | 효과 |
|---|---|---|
| `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` | `7` | superseded 스냅샷이 sweep에 회수되기 전 생존하는 일수. 브랜치별 롤백 이력을 더 길게 유지하려면 높이십시오. |
| `SCAN_RETENTION_KEEP_LAST` | `30` | 나이와 무관하게 **프로젝트당** 보존되는 ref-less·실패 스캔의 최소 개수. sweep은 이 하한 아래로 트림하지 않습니다. |
| `SCAN_RETENTION_MAX_AGE_DAYS` | `180` | **ref 없는 성공 스캔과 실패/취소 스캔** 중 이보다 오래된 것(그리고 keep-last 하한을 넘은 것)이 회수됩니다. **ref의 live 스냅샷**과 **release 라벨 스캔**은 예외입니다 — 이들은 나이가 아니라 retire가 관리합니다. |

:::caution 값을 낮추면 더 일찍 회수됩니다
`SCAN_RETENTION_SUPERSEDED_GRACE_DAYS`나 `SCAN_RETENTION_MAX_AGE_DAYS`를 낮추면 다음 sweep이 더 많은 스냅샷을 회수합니다. sweep은 되돌릴 수 없습니다 — 회수된 스캔과 그 결과는 사라집니다. 먼저 올려서 디스크를 관찰한 뒤 낮추십시오.
:::

## 보존 sweep {#the-retention-sweep}

회수는 스캔 완료 시 동기적으로가 아니라 **6시간 주기 Celery beat 태스크**로 실행됩니다. superseded 마킹은 더 새로운 성공이 도착하는 즉시 일어나며, 디스크·DB 행은 grace 윈도우를 지난 스냅샷을 다음 sweep이 발견할 때 회수됩니다.

각 sweep은:

1. `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS`보다 오래된 **superseded** 스냅샷을 회수합니다.
2. **ref 없는 성공 스캔과 모든 실패/취소 스캔** 중 프로젝트당 최신 `SCAN_RETENTION_KEEP_LAST`개를 보존하고, 나머지 중 `SCAN_RETENTION_MAX_AGE_DAYS`보다 오래된 것을 회수합니다.
3. **ref의 live 스냅샷**(sweep이 아니라 retire가 관리)과 `metadata.release` 라벨이 붙은 스캔은 절대 건드리지 않습니다.

스캔 회수는 그 workspace 아티팩트(소스 클론, cdxgen SBOM, scancode 출력)와 DB 행(컴포넌트·라이선스·결과)을 삭제합니다. 감사 로그는 회수된 스캔마다 사유 — `superseded`(1단계) 또는 `aged`(2단계) — 와 함께 `scans` `delete` 이벤트를 기록합니다.

## 스캔을 영구 보존하는 법 (release 라벨) {#keep-a-scan-forever-release-label}

보존이 절대 회수하지 않도록 스캔을 고정하려면 — 예를 들어 태그 릴리스를 뒷받침하는 스캔 — 트리거 시 `metadata.release` 라벨을 설정하십시오. release 라벨 스캔은 **불변**입니다 — grace 윈도우·age 상한·keep-last 트림에서 모두 면제됩니다.

<!-- docs-uat: id=scan-retention-release-label kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source", "metadata": {"ref": "refs/tags/v2.0.0", "release": "v2.0.0"}}' | jq .
```

`release` 값은 자유 형식 라벨입니다(버전 문자열이 관례). CI에서는 태그 push에서 실행되는 워크플로에만 설정하여, 일상적 브랜치·PR 스캔은 회수 가능한 상태로 두고 release 스캔만 영구 컴플라이언스 기록으로 누적되게 하십시오.

:::note release 스캔은 superseded 되지 않습니다
release 스캔은 supersession 체인 밖에 있으므로, 같은 브랜치의 두 릴리스가 모두 live로 남습니다. 이는 의도된 동작입니다 — 출시된 모든 버전의 SBOM을 기록으로 남기길 원합니다.
:::

## 스캔 수동 삭제 {#delete-a-scan-by-hand}

sweep을 기다리지 않고 단일 스캔을 즉시 회수하려면 `DELETE /v1/scans/{scan_id}`를 사용하십시오 — 예를 들어 잘못된 프로젝트에 트리거한 스캔이나, 이력에 두고 싶지 않은 노이즈 스냅샷.

<!-- docs-uat: id=scan-retention-delete kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X DELETE \
  "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .
```

`metadata.release` 라벨이 붙은 스캔을 삭제하려면 `?force=true`를 추가하십시오.

<!-- docs-uat: id=scan-retention-delete-force kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X DELETE \
  "https://trustedoss.example.com/v1/scans/${SCAN_ID}?force=true" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .
```

### 권한 및 응답 {#authorization-and-responses}

| 조건 | 요구 / 응답 |
|---|---|
| 호출자 역할 | **소유** 팀의 `developer` 이상. `force=true`는 `team_admin` 이상 필요. |
| 타 팀의 스캔 | `404 Not Found` — 타 팀 스캔은 존재 은닉됩니다. `404`가 스캔 존재를 확인해 주지 않습니다. |
| 스캔이 `queued`·`running` | `409 Conflict` — 활성 스캔은 삭제할 수 없습니다. [먼저 취소](../user-guide/scans.md#스캔-취소)한 뒤 삭제하십시오. |
| `release` 라벨 보유, `force` 없음 | `409 Conflict` — RFC 7807 본문에 `scan_release_protected: true`가 담깁니다. `team_admin`으로 `?force=true`를 붙여 재요청하십시오. |
| 비-`team_admin`의 `force=true` | `403 Forbidden` — release 보호 스캔의 강제 삭제는 `team_admin`이 필요합니다. |
| 삭제됨 | `204 No Content`. 아티팩트·행이 사라지며 감사 로그가 `scans` `delete` 이벤트를 기록합니다. |

## 정상 동작 확인 {#verify-it-worked}

<!-- docs-uat: id=scan-retention-verify-live kind=manual tier=manual -->
1. 같은 브랜치에 소스 스캔 두 번을 트리거합니다. 더 오래된 스캔을 `GET /v1/scans/{id}`로 조회하면 `superseded_at`이 설정돼 있습니다. 프로젝트 **Releases** 목록(`GET /v1/projects/{id}/releases`)에는 더 새로운 스냅샷만 보이고, superseded된 것은 거기서 숨겨집니다.
<!-- docs-uat: id=scan-retention-verify-release kind=manual tier=manual -->
2. `release` 라벨을 단 스캔과 같은 브랜치의 두 번째 스캔을 트리거합니다. release 스캔의 `superseded_at`은 `null`로 남고 Releases 목록에 그대로 유지됩니다 — superseded되지 **않습니다**.
<!-- docs-uat: id=scan-retention-verify-sweep kind=manual tier=manual -->
3. superseded 스캔이 grace 윈도우를 지난 뒤, 다음 6시간 주기 sweep이 그것을 제거합니다. 감사 로그로 확인하십시오 — 사유 `superseded`의 `scans` `delete` 이벤트.
<!-- docs-uat: id=scan-retention-verify-delete kind=manual tier=manual -->
4. release가 아닌 terminal 스캔에 대한 `DELETE /v1/scans/{scan_id}`는 `204`를 반환하고 스캔이 이력에서 사라집니다.

## 트러블슈팅 {#troubleshooting}

:::info 먼저 확인할 로그
- `docker-compose logs --tail=200 beat | grep scan_retention_sweep` — 마지막 sweep의 판정과 사유별 카운트.
- `scans` `delete`로 필터링한 감사 로그 — 무엇이 왜 회수되었는지.
:::

### 같은 브랜치의 두 스캔이 모두 live로 남음 {#two-scans-same-branch-live}

같은 정규화 타겟에 있지 않은 것입니다. 둘 다 **동일한** `metadata.ref`를 전달했는지 확인하십시오. bare 브랜치명(`main`)과 fully-qualified ref(`refs/heads/main`)는 같은 타겟으로 정규화되지만, PR merge ref(`refs/pull/12/merge` → `pr-12`)는 base 브랜치와 별개 타겟입니다 — 이는 의도된 동작입니다.

### 회수될 것이라 기대한 스캔이 그대로 있음 {#expected-reclaim-still-here}

순서대로 확인하십시오.

- 타겟의 **live** 스냅샷입니다 — live 스캔은 superseded 되거나 `SCAN_RETENTION_MAX_AGE_DAYS`를 초과하기 전까지 나이로 회수되지 않습니다.
- `metadata.release` 라벨이 붙어 있습니다 — release 스캔은 영구입니다. UI나 API에서 스캔 metadata를 확인하십시오.
- `SCAN_RETENTION_KEEP_LAST` 하한 안에 있습니다 — 프로젝트당 최신 N개에 든 ref-less·실패 스캔은 나이와 무관하게 보호됩니다.
- grace 윈도우가 아직 지나지 않았습니다 — superseded 스캔은 sweep이 가져가기 전 `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` 동안 생존합니다.

### sweep 회수보다 디스크가 더 빨리 참 {#disk-fills-faster-than-sweep}

sweep은 6시간 주기로 실행되므로 CI 부하가 큰 날에는 추월당할 수 있습니다. `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS`를 낮춰 superseded 스냅샷을 더 일찍 회수하거나, 최악의 항목을 수동 삭제하십시오. workspace 수준 아티팩트 정리는 [디스크·health → 디스크가 찼을 때](./disk-and-health.md#디스크가-가득-찼을-때-할-일)를 참고하십시오.

### 스캔 삭제 시 `409` {#409-on-delete}

스캔이 아직 `queued`·`running`이거나(먼저 취소 — [스캔 취소](../user-guide/scans.md#스캔-취소)) `release` 라벨이 있는데 `?force=true`를 전달하지 않은 것입니다. RFC 7807 본문의 확장 필드가 어느 쪽인지 알려 줍니다 — `scan_active: true` 대 `scan_release_protected: true`.

## 함께 보기 {#see-also}

- [스캔](../user-guide/scans.md) — 스캔 단위 라이프사이클과 취소 흐름
- [GitHub Actions](../ci-integration/github-actions.md) — 보존 키로서 ref 전달
- [GitLab CI](../ci-integration/gitlab-ci.md) — 보존 키로서 ref 전달
- [디스크·health](./disk-and-health.md) — workspace 아티팩트 정리
- [감사 로그](./audit-log.md) — 회수·삭제 이벤트
- [환경변수 → 스캔 보존](../reference/env-variables.md#scan-retention)
