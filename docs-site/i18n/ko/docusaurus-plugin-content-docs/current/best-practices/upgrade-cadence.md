---
id: upgrade-cadence
title: 업그레이드 주기
description: TRUSCA와 Trivy DB를 최신으로 유지합니다 — 릴리스 노트 읽기, forward-only 마이그레이션, 그리고 롤백을 가능하게 하는 업그레이드 전 백업 순서.
sidebar_label: 업그레이드 주기
sidebar_position: 4
---

# 업그레이드 주기

두 가지가 서로 다른 시계로 낡습니다. **TRUSCA 자체**(기능·수정·마이그레이션)와 그것이 스캔에 대조하는 **Trivy 취약점 데이터베이스**입니다. 둘은 다른 메커니즘으로 업그레이드되고 다른 주기를 원합니다. 이 페이지는 각각을 얼마나 자주 옮길지, 그리고 나쁜 릴리스를 항상 복구 가능하도록 업그레이드를 어떻게 순서 짓는지 결정하도록 돕습니다.

:::note 대상 독자
배포를 운영하는 `super_admin`. `docker-compose`, [업그레이드 래퍼](../installation/upgrade.md), [백업 및 복원](../admin-guide/backup-and-restore.md)에 익숙하다고 가정합니다. 이 문서는 결정 가이드입니다 — 단계별 업그레이드 명령은 [업그레이드 페이지](../installation/upgrade.md)를, 실패한 업그레이드 복구는 그 [롤백 절](../installation/upgrade.md#롤백)을 따르십시오.
:::

## 두 시계, 두 메커니즘 {#two-clocks}

| 무엇 | 어떻게 움직이는가 | 목표 주기 | 운영자 행동 |
|---|---|---|---|
| **Trivy DB** (알려진 CVE) | 자동 주간 갱신 + 재매칭 beat | 계속 최신 유지 | 없음 — 최신인지 관찰만 |
| **TRUSCA 릴리스** (제품) | `scripts/upgrade.sh` | 패치: 즉시 · 마이너: 월 1회 정도 · 메이저: 계획적 | 노트 읽기, 백업, 래퍼 실행 |

데이터베이스는 손으로 예약하지 **않는** 쪽입니다 — 스스로 갱신하고 기존 SBOM을 재매칭합니다. 그쪽에서 할 일은 갱신이 멈췄을 때 알아차리는 것이지 트리거하는 것이 아닙니다. 사람이 정한 주기가 필요한 쪽은 제품 릴리스입니다.

### Trivy DB를 최신으로 유지 {#trivy-db}

Trivy DB(NVD, OSV, GHSA, EPSS, KEV의 묶음)는 첫 부팅에 내려받고 주간으로 갱신되며, 재매칭 beat가 기존 스캔을 여기에 다시 대조합니다. 운영 측면은 [취약점 데이터](../admin-guide/vulnerability-data.md)에서 모두 다룹니다. 업그레이드 관점에서는 두 습관이 중요합니다.

- **최신성을 관찰하십시오.** `/admin/health`의 **Vulnerability data** 카드가 마지막 갱신과 `fresh` / `stale` / `very_stale` 상태를 보여 줍니다. 오래된 DB는 새 CVE가 들어오지 않음을 뜻합니다 — [on-call 런북 시나리오 1](../admin-guide/oncall-runbook.md#시나리오-1--trivy-db-stale-또는-누락)로 복구하십시오. 제품 업그레이드와 무관합니다.
- **업그레이드에 따른 태그 변동을 유의하십시오.** DB는 스키마 태그에 고정된 OCI 아티팩트입니다. TRUSCA 업그레이드가 기대 태그를 올리면, air-gapped 미러를 맞춰 갱신하지 않는 한 스캔이 빈 결과를 반환합니다 — 미러 업데이트를 업그레이드와 함께 조율하십시오. [air-gapped 운영](../admin-guide/vulnerability-data.md#air-gapped)을 참고하십시오.

## 릴리스 노트를 먼저 읽으십시오 {#release-notes}

`scripts/upgrade.sh`를 무턱대고 실행하지 마십시오. 각 릴리스는 동작 변경, 새 admin 화면, 수동 마이그레이션 단계를 짚는 노트를 함께 제공합니다. 최근 예로 [v0.14.0](../release-notes/v0.14.0.md)은 스캔 *결과*를 의도적으로 바꿨습니다(런타임 스코프 SBOM 필터링, 기본 켜짐) — 노트를 건너뛴 운영자는 컴포넌트 수가 줄어드는 것을 보고 뭔가 망가졌다고 여길 것입니다. 사이드바의 **릴리스 노트** 섹션이나 전체 변경 이력은 [GitHub 릴리스](https://github.com/trustedoss/trusca/releases)를 살펴보십시오.

순서대로 읽으십시오: 헤드라인(결과를 바꾸는가, 버그만 고치는가?), 업그레이드·마이그레이션 단계, 그리고 풀 전후로 설정해야 할 새 환경변수.

## Forward-only 마이그레이션 — 순서가 중요한 이유 {#forward-only}

TRUSCA의 Alembic 마이그레이션은 **forward-only**입니다. `alembic downgrade`가 없습니다. 나쁜 마이그레이션에서 돌아오는 유일한 길은 **업그레이드 전 백업을 복원**하는 것입니다. 이 사실 하나가 업그레이드 순서 전체를 좌우합니다.

1. **먼저 백업 — 항상.** `scripts/upgrade.sh`는 무엇을 건드리기 전에 필수 업그레이드 전 백업을 뜨며, 건너뛰는 플래그가 없습니다. 단계를 손으로 실행한다면 백업을 먼저 직접 뜨십시오. [forward-only 마이그레이션과 복원](../admin-guide/backup-and-restore.md#forward-only-마이그레이션과-복원)을 참고하십시오.
2. **풀과 재생성** — 이미지 해시가 바뀐 서비스만.
3. **마이그레이션 적용**(`alembic upgrade head`).
4. **헬스 프로브** 후 검증.

업그레이드가 잘못되면 "다운그레이드"하지 않습니다 — [업그레이드 전 백업을 복원](../installation/upgrade.md#롤백)하며, 이는 데이터베이스와 작업 공간을 마이그레이션 이전 상태로 되돌립니다. 나쁜 마이그레이션 *뒤*에 뜬 백업은 롤백에 쓸모가 없으며, 래퍼가 먼저 백업하는 이유가 바로 그것입니다.

:::warning 복원은 라이브 데이터를 대체합니다
`restore.sh`는 라이브 데이터베이스와 작업 공간을 덮어씁니다 — 되돌리기가 없고, *업그레이드 이전* 스키마로 복원합니다. 업그레이드가 방금 뜬 백업을 가리키십시오(`ls -td backups/*`가 최신을 먼저 출력), 더 오래된 것이 아니라. 의존하기 전에 백업 매니페스트의 Alembic head가 맞는지 확인하십시오.
:::

## 주기 고르기 {#cadence}

긴급도를 릴리스 유형에 맞추십시오.

| 릴리스 유형 | 권장 주기 | 근거 |
|---|---|---|
| **패치**(`x.y.Z`) | 즉시 — 며칠 | 버그·보안 수정, 마이그레이션 리스크 낮음. 같은 메이저 내 마이너·패치 업그레이드는 항상 in-place로 지원됩니다. |
| **마이너**(`x.Y.0`) | 월 1회 정도, 피크 외 | 새 기능, 새 admin 화면이나 환경변수 가능. 노트를 읽고 조용한 시간대에 단계화하십시오. |
| **메이저**(`X.0.0`) | 계획적, 신중히 | breaking 마이그레이션을 지닐 수 있음. 메이저를 넘어 래퍼를 무턱대고 실행하지 **마십시오** — 릴리스 전용 마이그레이션 단계를 따르십시오. |

마이그레이션 체인이 CI에서 끝까지 검증되므로 한 번의 래퍼 실행으로 여러 패치·마이너 버전을 건너뛸 수 있습니다. 그래서 패치 몇 개 뒤처져도 한 단계로 따라잡기 안전합니다 — [버전 건너뛰기](../installation/upgrade.md#버전-점프) 참고. 메이저 버전 이동만 예외이며, 각 릴리스 노트를 따라 한 경계씩 진행하십시오.

:::tip 조용한 시간대에 업그레이드하십시오
스캔이 실행 중이지 않은 때를 고르십시오 — 바뀐 서비스가 재생성되는 동안 포털이 잠시(보통 30초 미만) 사용 불가이고, 실행 중인 스캔은 롤백 상황을 복잡하게 합니다. 전역 `/scans` 큐가 비었는지 알려 줍니다. [호환성·정책](../installation/upgrade.md#호환성-및-정책)을 참고하십시오.
:::

## 검증

<!-- docs-uat: id=bp-upgrade-cadence-review kind=manual tier=manual -->
업그레이드 규율이 지켜지는지 확인하십시오.

<!-- docs-uat: id=bp-upgrade-cadence-1 kind=manual tier=manual -->
1. 모든 업그레이드에 앞서 새 백업이 있습니다 — 래퍼의 필수 업그레이드 전 백업에 의존하거나, 수동 마이그레이션 전에 직접 하나 뜹니다.
<!-- docs-uat: id=bp-upgrade-cadence-2 kind=manual tier=manual -->
2. 결과가 놀라워 보인 뒤가 아니라 풀하기 **전에** 대상 릴리스 노트에서 동작 변경과 새 환경변수를 읽습니다.
<!-- docs-uat: id=bp-upgrade-cadence-3 kind=manual tier=manual -->
3. `/admin/health`의 **Vulnerability data** 카드가, 제품을 마지막으로 업그레이드한 시점과 무관하게 최근 Trivy DB 갱신을 보고합니다.
<!-- docs-uat: id=bp-upgrade-cadence-4 kind=manual tier=manual -->
4. 가장 최근 업그레이드를 롤백할 때 복원할 정확한 백업을 지목할 수 있고, 그 Alembic head가 업그레이드 이전 스키마와 맞습니다.
<!-- docs-uat: id=bp-upgrade-cadence-5 kind=manual tier=manual -->
5. 패치는 최신 상태(길어야 며칠)이며, 다음 마이너·메이저에 즉흥이 아닌 의도적 계획이 있습니다.

## 함께 보기

- [업그레이드](../installation/upgrade.md) — 래퍼 단계별 안내와 롤백
- [백업 및 복원](../admin-guide/backup-and-restore.md#forward-only-마이그레이션과-복원) — 백업이 유일한 복귀 경로인 이유
- [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) — 갱신 수명 주기와 air-gapped 미러
- [릴리스 노트 — v0.14.0](../release-notes/v0.14.0.md) — 결과를 바꾸는 릴리스의 예
- [on-call 런북 — 시나리오 1](../admin-guide/oncall-runbook.md#시나리오-1--trivy-db-stale-또는-누락) — 오래된 Trivy DB 복구
