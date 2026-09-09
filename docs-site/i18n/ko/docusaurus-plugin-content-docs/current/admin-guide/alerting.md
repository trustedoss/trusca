---
id: alerting
title: 알림
description: 이 배포가 이미 발행하는 /metrics 계열을 다루는 예시 Prometheus 알림 규칙과, 각 규칙이 온콜 런북의 어느 시나리오에 대응하는지 정리합니다.
sidebar_label: 알림
sidebar_position: 3.6
---

# 알림

이 저장소는 운영 [지표 엔드포인트](./disk-and-health.md#metrics)(`/metrics`, 기본
꺼짐)를 제공하지만 Prometheus 서버, Alertmanager, 페이징 연동은 포함하지
않습니다 - 이건 직접 운영하고 설정해야 합니다. 이 페이지는 그 빈틈을 메우는
한 가지를 제공합니다: `/metrics`가 이미 발행하는 계열을 다루는 예시 규칙
파일입니다. 이렇게 하면 [온콜 런북](./oncall-runbook.md)에 나오는 알림 이름
뒤에 페이지가 알아서 도착했다는 가정이 아니라 실제 근거가 생깁니다.

다운로드: [`trusca-alerts.rules.yml`](/prometheus/trusca-alerts.rules.yml)

## 불러오기

Prometheus 서버의 `rule_files` 글롭이 찾는 위치에 파일을 두고 reload합니다.

```yaml
# prometheus.yml
rule_files:
  - /etc/prometheus/rules/trusca-alerts.rules.yml
scrape_configs:
  - job_name: trusca
    metrics_path: /metrics
    # bearer_token: "<METRICS_TOKEN을 설정했다면 그 값>"
    static_configs:
      - targets: ["<your-host>:443"]
```

발화한 알림을 실제 페이지(Alertmanager 라우트, PagerDuty/Slack 리시버 등)에
연결하는 건 이 파일이 다루지 않는 별도 단계입니다. 배포마다 페이징 구성이
다르고, 런북은 특정 리시버가 아니라 알림 *이름*만 참조하면 되기 때문입니다.

## 각 규칙이 다루는 것

| 규칙 | 계열 | 런북 시나리오 |
|---|---|---|
| `TrustedOSSVulnDbStale` | `trusca_vuln_db_last_update_timestamp_seconds`, `trusca_vuln_db_refresh_interval_hours` | [시나리오 1](./oncall-runbook.md) |
| `TrustedOSSTaskRunRecorderStalled` | `trusca_task_runs_last_recorded_timestamp_seconds` | (번호 붙은 시나리오는 없음 - disk-and-health.md에서 이 계열에 대해 직접 언급하는 대목 참고) |
| `TrustedOSSAutoBackupNotSucceeding` | `trusca_task_runs_24h{task="trustedoss.backup.run"}` | [시나리오 2](./oncall-runbook.md) |
| `TrustedOSSWorkspaceDiskCritical` | `trusca_workspace_disk_used_ratio` | [시나리오 4](./oncall-runbook.md) |
| `TrustedOSSScanQueueBacklogHigh` / `TrustedOSSDefaultQueueBacklogHigh` | `trusca_broker_queue_backlog`(옵트인, `QUEUE_BACKLOG_METRICS_ENABLED`) | [시나리오 5](./oncall-runbook.md) |

각 임계값은 문서화된 앱 기본값과 정확히 맞거나(디스크 규칙, 큐 적체 규칙)
규칙 파일 자체 주석이 재조정 방법을 설명하는 출발점입니다(취약점 DB
신선도, 태스크 기록기 정지). 실제 프로덕션 환경에서 검증된 값은
아니므로, `expr:` 옆 주석을 먼저 읽고 신뢰하세요.

## 여기서 다루지 않는 것과 그 이유 {#what-is-not-covered-here-and-why}

런북 시나리오 셋은 위에 규칙이 없습니다. `/metrics`가 알림을 걸 만한 걸
아무것도 발행하지 않기 때문입니다.

- **시나리오 3**(스캔이 몇 시간째 running으로 멈춤): 가장 오래된 실행 중
  스캔이 얼마나 오래됐는지 추적하는 계열이 없습니다.
  `trusca_scans_total{status=...}`는 개수이지 경과 시간이 아닙니다.
- **시나리오 6**(워커가 부팅하다 크래시 루프에 빠짐): 이건 오케스트레이터가
  이미 갖고 있는 컨테이너 재시작 신호(Kubernetes 자체 재시작 횟수,
  `cAdvisor`, `kube-state-metrics`)이지, 부팅을 끝내지도 못한 프로세스
  안에 있는 이 애플리케이션의 `/metrics`가 볼 수 있는 것이 아닙니다.
- **시나리오 7**(`/health/ready`의 Redis `degraded`): 내부적으로는
  추적되고(`core.redis_degradation`) `/health/ready`의 `redis_fail_open`
  필드로 읽을 수 있지만, 아직 `/metrics`에 별도 계열로 그대로 옮겨지진
  않았습니다.

여기서 빈틈을 밝히는 쪽이, 시나리오를 다루는 것처럼 보이지만 조용히
아무것도 안 하는 규칙보다 낫습니다.

## 함께 보기

- [디스크·health - 지표 스크레이핑](./disk-and-health.md#metrics): 전체 계열 목록, 그리고 그중 둘(취약점 DB 신선도, 태스크 기록기)이 알림을 걸 만하다고 따로 언급되는 이유.
- [온콜 런북](./oncall-runbook.md): 이 알림들이 울렸을 때 할 일.
- [환경변수](../reference/env-variables.md): `METRICS_ENABLED`, `METRICS_TOKEN`, `QUEUE_BACKLOG_METRICS_ENABLED`, 그리고 두 큐 적체 규칙이 그대로 따르는 임계·지속·쿨다운 값.
