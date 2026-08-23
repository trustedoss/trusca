---
id: data-retention
title: 데이터 보존
description: 포털이 refresh token, 비밀번호 재설정 토큰, 알림, 웹훅 수신 기록, 보고서 다운로드 기록, 감사 로그의 크기를 어떻게 제한하는지 — 무엇을 지우고 무엇을 남기는지, 그리고 감사 로그만 수동 삭제로 남긴 이유.
sidebar_label: 데이터 보존
sidebar_position: 8.5
---

# 데이터 보존

[스캔 보존](./scan-retention.md)은 `scans` 테이블과 그 결과를 제한합니다. 이 문서는 W9(concurrency-scaling-plan-2026-08-22.md §3.5) 이전까지 보존 정책이 없던 여섯 테이블을 다룹니다 — `refresh_tokens`, `password_reset_tokens`, `notifications`, `webhook_deliveries`, `report_downloads`, `audit_logs`. 이 중 다섯은 매일 도는 자동 정리 작업이 있습니다. 여섯 번째인 `audit_logs`는 의도적으로 자동 삭제가 없습니다. [감사 로그 보존만 수동으로 남긴 이유](#audit-log-retention-why-this-one-stays-manual)를 봅니다.

:::note 대상 독자
이 테이블들이 문제가 될 만큼 오래 운영해 온 포털을 다루는 `super_admin`. `.env` 편집과 `docker-compose restart`에 익숙해야 합니다.
:::

## 테이블마다 지우는 것과 남기는 것

| 테이블 | 지우는 것 | 남기는 것 | 주기 |
|---|---|---|---|
| `refresh_tokens` | `expires_at + REFRESH_TOKEN_RETENTION_GRACE_DAYS`(기본값은 7일 TTL에 1일을 더한 시점)를 지난 행. | 아직 살아 있거나 최근에 만료된 토큰 전부. 회전·폐기된 행도 원래의 만료 시각까지는 남아 있으므로, 재사용 탐지가 비교할 대상을 그대로 갖습니다. | 매일 03:15 UTC |
| `password_reset_tokens` | `expires_at + PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS`(기본값은 1시간 TTL에 1일을 더한 시점)를 지난 행. | 사용 여부와 무관하게 아직 만료되지 않은 토큰 전부. | 매일 03:15 UTC |
| `notifications` | `NOTIFICATION_RETENTION_DAYS`(기본 180일)보다 오래된 행. 읽음·안 읽음 무관. | 6개월 치 앱 내 알림 이력. | 매일 03:30 UTC |
| `webhook_deliveries` | `WEBHOOK_DELIVERY_RETENTION_DAYS`(기본 90일)보다 오래된 행. | CI 디버깅용으로 3개월 치 GitHub·GitLab 웹훅 수신 이력. | 매일 03:30 UTC |
| `report_downloads` | `REPORT_DOWNLOAD_RETENTION_DAYS`(기본 365일)보다 오래된 행. | SBOM·NOTICE·보고서를 누가 언제 내려받았는지 1년 치 이력. | 매일 03:30 UTC |
| `audit_logs` | **자동으로는 아무것도 지우지 않습니다.** 매일 도는 리포트가 이미 반출되었고 `AUDIT_LOG_RETENTION_DAYS`(기본 90일)보다 오래된 행의 수만 셉니다. 실제 삭제는 운영자가 직접 손으로 진행합니다. | 운영자가 문서화된 절차를 직접 실행하기 전까지는 모든 감사 로그 행. | 리포트만, 매일 03:45 UTC |

다섯 자동 정리 작업 모두 스키마 변경이 필요 없습니다. 위 조건은 전부 W9 이전부터 그 테이블에 있던 인덱스(`ix_refresh_tokens_expires_at`, `ix_password_reset_tokens_expires_at`, `ix_webhook_deliveries_received_at`)로 동작합니다. `notifications`와 `report_downloads`는 `user_id`·`project_id`·`team_id`로 시작하는 복합 인덱스만 있어서, 이 정리 작업의 `created_at` 단독 조건은 순차 스캔으로 처리됩니다. 하루 한 번이라면 지금 규모에서는 받아들일 만합니다. 배포 규모가 커져 이 스캔이 부담될 때는 [`tasks/operational_retention.py`](https://github.com/trustedoss/trusca) 모듈 설명을 참고해 인덱스 추가를 검토합니다.

## 설정 {#configuration}

모든 키는 런타임에 `os.getenv`로 읽힙니다 — `.env`를 편집하고 Celery beat 서비스를 재시작하면 값이 적용됩니다(03:15·03:30·03:45 UTC라는 일정 자체는 이 코드베이스의 다른 beat 항목들과 같은 관례로 코드에 고정돼 있습니다). 정식 레퍼런스는 [환경변수 → 운영 데이터 보존](../reference/env-variables.md#operational-data-retention)을 참고합니다.

<!-- docs-uat: id=data-retention-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 포털의 .env
REFRESH_TOKEN_RETENTION_GRACE_DAYS=1
PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS=1
NOTIFICATION_RETENTION_DAYS=180
WEBHOOK_DELIVERY_RETENTION_DAYS=90
REPORT_DOWNLOAD_RETENTION_DAYS=365
AUDIT_LOG_RETENTION_DAYS=90
```

:::caution 값을 낮추면 더 일찍 회수됩니다
다섯 자동 정리 작업은 되돌릴 수 없습니다 — 회수된 행은 사라집니다. [스캔 보존](./scan-retention.md#retention-policy-variables)이 그 세 키에 주는 안내와 같습니다. 먼저 값을 올려 며칠 동안 디스크를 관찰한 뒤 낮춥니다.
:::

## 감사 로그 보존만 수동으로 남긴 이유 {#audit-log-retention-why-this-one-stays-manual}

`audit_logs`는 **데이터베이스 계층에서 append-only로 강제**됩니다. 마이그레이션 `0012`가 이 테이블에 대한 `UPDATE`·`DELETE`·`TRUNCATE` 전부에서 오류를 내는 트리거를 붙였습니다 — [감사 로그 → 스키마](./audit-log.md#schema)를 봅니다. 이것은 의도적인 컴플라이언스 통제입니다. 사람이 지켜보지 않는 작업이 컴플라이언스 이력을 조용히 대량 삭제할 수 있다면, 이 트리거가 보장하려는 "사람이 책임지고 결정한다"는 성질 자체가 사라집니다. W9은 이 통제를 약화시키지 않습니다.

대신 매일 도는 beat(`trustedoss.audit_log_retention_report`)가 [문서화된 수동 삭제](./audit-log.md#retention) 세션을 열기 전에 운영자가 알아야 할 질문에 답합니다. **지금 실제로 지워도 안전한 행이 몇 건인가**입니다. 다음 두 조건을 모두 만족하는 행만 셉니다.

1. 이미 로그 수집기로 전달된 행 — [연속 반출](./audit-log.md#continuous-export)의 커서 위치이거나 그보다 앞선 행.
2. `AUDIT_LOG_RETENTION_DAYS`보다 오래된 행.

아직 반출되지 않은 행은 나이와 무관하게 절대 세지 않습니다. 그 행은 해당 컴플라이언스 기록의 유일한 사본이고, 이 리포트는 그 사본이 밖으로 나가기 전에 누군가 지우는 일이 없도록 만들어졌습니다. `AUDIT_EXPORT_URL`을 설정하지 않았다면 리포트는 항상 0을 반환합니다. 아무것도 반출한 적이 없는 조직은, 이 신호를 기준으로는 지워도 되는 행 자체가 없기 때문입니다.

<!-- docs-uat: id=data-retention-audit-log-report kind=shell ctx=host tier=manual waiver=log-grep-illustrative-no-deterministic-assertion -->
```bash
docker-compose -f docker-compose.yml logs --tail=50 beat \
  | grep audit_log_retention_report_done
```

이 로그 줄에는 `ready_to_purge`와 `retention_days`가 담깁니다. `ready_to_purge`가 유지보수 시간을 잡을 만큼 크다면, 실제 삭제는 [감사 로그 → 보존](./audit-log.md#retention)에 적힌 2인 운영자 절차를 따릅니다.

## 정상 동작 확인

<!-- docs-uat: id=data-retention-verify-expired-token kind=manual tier=manual -->
1. 만료된 refresh token을 하나 만듭니다. 로그인한 뒤 `psql`로 그 행의 `expires_at`을 과거로 바꿉니다(테스트 전용 단계이고, 이를 위한 API는 없습니다). 다음 `auth-token-retention-daily` 틱(또는 `celery -A tasks.celery_app call trustedoss.auth_token_retention` 수동 호출) 뒤에는 그 행이 사라지고, `SELECT count(*) FROM refresh_tokens WHERE id = '<id>'`는 `0`을 반환합니다.
<!-- docs-uat: id=data-retention-verify-fresh-notification kind=manual tier=manual -->
2. 알림을 하나 트리거한 뒤(어떤 스캔이든 완료되면 발생합니다) `/notifications`에 여전히 보이는지 확인합니다. 정리 작업은 `NOTIFICATION_RETENTION_DAYS`보다 오래된 행만 건드리므로 방금 만든 알림은 영향받지 않습니다.
<!-- docs-uat: id=data-retention-verify-report-skipped kind=manual tier=manual -->
3. `AUDIT_EXPORT_URL`을 설정하지 않은 상태에서 `celery -A tasks.celery_app call trustedoss.audit_log_retention_report`를 직접 실행하고, 로그 줄이 `status=skipped`와 `ready_to_purge=0`을 보이는지 확인합니다.

## 문제 해결

:::info 먼저 확인할 로그
`docker-compose -f docker-compose.yml logs --tail=200 beat | grep -E 'auth_token_retention_done|operational_retention_done|audit_log_retention_report_done'` — 각 정리 작업의 테이블별 삭제 건수, 또는 감사 리포트의 `ready_to_purge`. dev compose에서는 서비스명이 `beat`가 아니라 `celery-beat`입니다.
:::

### 회수될 것으로 기대한 행이 아직 남아 있다

`refresh_tokens`·`password_reset_tokens`라면 `expires_at`을 직접 확인합니다. 정리 작업은 사용 여부나 폐기 여부를 보지 않고 자신의 만료 시각과 유예 기간만 봅니다. 발생 시각 기준인 나머지 세 테이블이라면 그 행의 `created_at`(`webhook_deliveries`는 `received_at`)이 실제로 설정된 기간을 지났는지 확인합니다. 한 시간 전에 생긴 행은 90일 정책으로 회수되지 않습니다.

### 테이블이 큰데도 `ready_to_purge`가 계속 `0`이다

`AUDIT_EXPORT_URL`을 설정하지 않았거나([위](#audit-log-retention-why-this-one-stays-manual) 참고, 이 경우 리포트는 항상 0을 반환합니다), 반출 커서가 아직 따라잡지 못한 상태입니다. [감사 로그 → 연속 반출](./audit-log.md#continuous-export)에서 설명하는 반출 커서의 `rows_exported`와 `last_run_at`을 확인합니다.

## 함께 보기

- [스캔 보존](./scan-retention.md) — 같은 모델을 `scans` 테이블에 적용한 문서
- [감사 로그](./audit-log.md) — 스키마, 불변성 트리거, 연속 반출, 수동 삭제 절차
- [디스크·상태](./disk-and-health.md) — 워크스페이스 산출물 정리
- [환경변수 → 운영 데이터 보존](../reference/env-variables.md#operational-data-retention)
