---
id: gcp-deploy
title: 데모 SaaS 호스팅
description: 공개 데모 SaaS가 현재 어디서 운영되는지(Hetzner Cloud, docker-compose)와 이전 GCP/Terraform 런북을 폐기한 이유.
sidebar_label: 데모 SaaS 호스팅
sidebar_position: 3
---

# 데모 SaaS 호스팅

공개 데모 SaaS는 **Hetzner Cloud ARM 서버**(CAX31, 8 vCPU / 16 GB) 한 대에서 운영됩니다. 자체 호스팅 운영 설치와 동일한 `docker-compose.yml` 스택을 그대로 사용하며, 호스트의 TLS 종단 리버스 프록시가 앞단을 맡습니다.

:::info 이 페이지는 GCP 런북을 대체했습니다
이 페이지의 이전 버전은 `terraform/` 모듈로 구동하는 GCP 배포(Cloud Run + Cloud SQL + Memorystore)를 설명했습니다. 이 계획은 출시 전에 폐기되었습니다 — 워커의 장시간 스캔 프로세스와 공유 워크스페이스 볼륨이 서버리스 런타임에 맞지 않고, 예상 비용도 몇 배 높았기 때문입니다. Terraform 모듈은 출하된 적이 없으며, 이 저장소에 `terraform/` 디렉토리는 존재하지 않습니다.
:::

## 구성 요약

| 항목 | 데모 SaaS 선택 |
| --- | --- |
| 컴퓨팅 | Hetzner CAX31(ARM64) VPS 1대 |
| 오케스트레이션 | `docker-compose.yml` — [설치 가이드](docker-compose.md)에 문서화된 운영 번들과 동일 |
| TLS / 인그레스 | 호스트의 리버스 프록시(80/443 포트) |
| 백업 | systemd 타이머로 매일 `scripts/backup.sh` 실행 후 외부 저장소로 전송 |
| 데모 데이터 초기화 | systemd 타이머로 야간 데모 재시드 |

데모 SaaS는 표준 docker-compose 설치이므로 클라우드 전용 런북이 따로 필요하지 않습니다. [설치 가이드](docker-compose.md), [업그레이드 가이드](upgrade.md), [백업/복원 가이드](../admin-guide/backup-and-restore.md)가 그대로 적용됩니다. Hetzner 특화 항목(프로비저닝, cloud-init, systemd 타이머, 외부 백업)을 다루는 전용 운영자 런북은 준비 중이며, 완성되면 여기서 링크합니다.

## 데모 계정

시드된 데모 자격 증명과 야간 초기화 동작은 [라이브 데모](live-demo.md)에 문서화되어 있습니다. 데모 조직은 매일 밤 처음부터 다시 생성됩니다 — 데모에서 만든 것은 모두 삭제됩니다.

## 운영 환경 배포

데모 SaaS는 제품을 보여주기 위한 환경입니다. 실제 데이터는 [docker-compose](docker-compose.md)로 온프레미스에 배포하거나 [Helm 차트](helm.md)로 Kubernetes에 배포하세요.
