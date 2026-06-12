---
id: quickstart
title: Quickstart
description: dev Docker Compose 스택과 데모 데이터셋으로 노트북에서 5분 만에 TRUSCA를 띄웁니다.
sidebar_label: Quickstart
sidebar_position: 1
slug: /quickstart
---

# Quickstart

노트북에서 약 5분 만에 TRUSCA를 실행합니다. 본 페이지는 데이터가 채워진
대시보드를 곧바로 보여주는 데 초점을 둡니다. 실제 배포는
[Docker Compose 설치](./installation/docker-compose.md) 또는
[Helm 차트](./installation/helm.md)를 참고하십시오.

## 사전 조건

- Docker + `docker-compose`(V1, 하이픈). V2 플러그인도 동작합니다.
- 여유 자원 4 vCPU / 8 GB RAM, 디스크 10 GB.

## 1. 스택 기동

레포지토리를 클론하고 env 파일을 생성합니다.

<!-- docs-uat: id=qs-bootstrap kind=shell ctx=host tier=gate waiver=ci-uses-checkout-tree -->
```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca
cp .env.example .env
```

dev 이미지는 `uvicorn --reload`를 직접 실행하므로 — 프로덕션 이미지와 달리 — 부팅
시 마이그레이션을 자동 적용하지 않습니다. 스키마를 먼저 생성해야 backend가 기동
즉시 healthy가 됩니다(아니면 health 게이트가 걸린 `celery-worker`가 `up`을 막습니다).

<!-- docs-uat: id=qs-migrate kind=shell ctx=host expect=exit:0 retry=20x3s tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml run --rm backend alembic upgrade head
```

이어서 전체 스택을 기동합니다.

<!-- docs-uat: id=qs-up kind=shell ctx=host expect=exit:0 tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml up -d
```

<!-- docs-uat: id=qs-health kind=api ctx=host url=/health/ready expect=status:200 retry=40x6s tier=gate -->
스키마가 이미 적용돼 있어 약 30초 안에 `postgres`, `redis`, `backend`,
`celery-worker`, `frontend` 컨테이너가 모두 healthy 상태가 됩니다 (`docker-compose -f docker-compose.dev.yml ps`).

## 2. 데모 데이터 시드

<!-- docs-uat: id=qs-seed kind=shell ctx=host expect=exit:0 fixture=seed_demo tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml exec backend \
  python -m scripts.seed_demo
```

조직 1개, 팀 3개, 사용자 5명, 프로젝트 5개, 그리고 현실적인 CVE·라이선스
finding·의무사항 묶음이 약 10초 안에 생성됩니다.

## 3. 로그인

<!-- docs-uat: id=qs-login kind=ui harness=login(admin@demo.trustedoss.dev,DemoTest2026!) tier=gate -->
브라우저에서 `http://localhost:5173` 을 열고 다음 계정으로 로그인합니다.

| 계정 | 이메일 | 비밀번호 |
|---|---|---|
| Super admin | `admin@demo.trustedoss.dev` | `DemoTest2026!` |
| Team admin | `frontend-admin@demo.trustedoss.dev` | `DemoTest2026!` |
| Developer | `dev@demo.trustedoss.dev` | `DemoTest2026!` |

데모 비밀번호는 `.env.example`에 정의되어 있으며 의도적으로 약하게 잡혀 있습니다 —
외부 노출 가능한 호스트에서는 절대 그대로 쓰지 마십시오.

## 4. 둘러보기

<!-- docs-uat: id=qs-dashboard kind=ui harness=expectMounted tier=gate -->
- **Dashboard** (`/`) — 조직 전체 심각도 타일과 최근 스캔.
<!-- docs-uat: id=qs-projects kind=ui harness=expectVisibleProjectCount(5) tier=gate -->
- **Projects → frontend-admin의 프로젝트** — 데이터가 가장 풍부한 케이스. **Vulnerabilities**
  탭을 열어 7단계 VEX 트리아지 흐름을 확인해 보세요.
- **Components & licenses** — 허용 / 조건부 / 금지 라이선스 비중을 도넛으로 표시합니다.
- **SBOM** — CycloneDX 또는 SPDX로 다운로드합니다.

![Project list — 시드된 5개 프로젝트의 심각도 롤업](/img/screenshots/user-projects-list.png)

## 다음 단계

- CI에 연결 → [GitHub Actions](./ci-integration/github-actions.md), [GitLab CI](./ci-integration/gitlab-ci.md), [Jenkins](./ci-integration/jenkins.md).
- 자체 스캔 실행 → [스캔](./user-guide/scans.md).
- 팀 단위 운영 → [사용자·팀](./admin-guide/users-and-teams.md), [백업·복원](./admin-guide/backup-and-restore.md).
- 프로덕션으로 이행 → [Docker Compose 설치](./installation/docker-compose.md).

## 스택 종료

<!-- docs-uat: id=qs-down kind=shell ctx=host expect=exit:0 tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml down
```

`-v` 옵션을 붙이면 데이터베이스 볼륨까지 함께 제거합니다.
