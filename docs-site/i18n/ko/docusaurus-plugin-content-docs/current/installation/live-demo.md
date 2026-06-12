---
id: live-demo
title: 라이브 읽기 전용 데모
description: TRUSCA를 공개 읽기 전용 라이브 데모로 운영하고 매일 밤 데이터셋을 초기화합니다.
sidebar_label: 라이브 데모
sidebar_position: 4
---

# 라이브 읽기 전용 데모

포털을 **공개 라이브 데모**로 운영할 수 있습니다. 누구나 시드된 데모 계정으로
로그인해 실제 프로젝트·스캔·취약점·라이선스·SBOM·보고서를 둘러볼 수 있지만,
**모든 쓰기 작업은 비활성화**되며 데이터셋은 매일 밤 깨끗한 상태로 초기화됩니다.

두 가지 독립적인 구성 요소로 이루어집니다.

1. **`DEMO_READ_ONLY` 읽기 전용 모드** (모든 배포).
2. **일일 자동 리셋** (데모 호스트의 systemd 타이머가 리셋 스크립트를 백엔드
   컨테이너 안에서 실행).

## 1. 읽기 전용 모드 (`DEMO_READ_ONLY`)

백엔드 환경 변수를 설정합니다.

```bash
DEMO_READ_ONLY=true
```

(허용되는 truthy 값: `1`, `true`, `yes`, `on` — 대소문자 무관. 요청 시점에
읽으므로, 값을 바꿀 때 재빌드 없이 프로세스 재시작만 하면 됩니다.)

활성화되면 단일 미들웨어가 **API 전체**에 정책을 강제하므로, 개별 엔드포인트가
정책을 우회할 수 없습니다.

- **읽기는 항상 통과** — `GET`·`HEAD`·`OPTIONS` (마지막은 CORS preflight 유지용).
- **쓰기는 기본 차단** — 모든 `POST`·`PUT`·`PATCH`·`DELETE`(및 기타 메서드)는
  **허용 목록(allow-list)** 에 없으면 거부됩니다.
- 허용 목록은 데모에 꼭 필요한 인증 흐름뿐입니다: `POST /auth/login`,
  `POST /auth/refresh`, `POST /auth/logout`. 그 외 — 회원가입, 비밀번호 재설정·
  변경, 프로젝트 생성, 스캔 트리거, 승인, 설정, 웹훅, 파일 업로드 — 는 모두
  차단됩니다.

차단된 요청은 `Content-Type: application/problem+json` 의 **RFC 7807** `403` 을
받습니다.

```json
{
  "type": "urn:trustedoss:problem:demo-read-only",
  "title": "Read-only demo",
  "status": 403,
  "detail": "This is a read-only live demo. Creating, updating, or deleting data is disabled. …",
  "instance": "/v1/projects",
  "demo_read_only": true
}
```

### 우회 방지

이 가드는 **차단 목록이 아니라 허용 목록**입니다. 나중에 추가되는 변이
엔드포인트는 별도 수정 없이 자동으로 차단됩니다. 허용 목록 비교 전에 경로를
정규화(역슬래시 변환, `.`/`..` 세그먼트 해석, 끝 슬래시 제거)하므로
`/v1/projects/../auth/login` 같은 트래버설로 쓰기 경로를 허용 목록에 끼워 넣을 수
없습니다. HTTP 메서드는 대소문자 무관으로 비교하고 허용 목록은 `(메서드, 경로)`
쌍을 키로 쓰므로, 비정상 메서드가 허용된 경로에 편승할 수 없습니다.

### 프론트엔드 동작

SPA는 공개 `GET /health` 응답(`{"status":"ok","demo_read_only":true}`)에서
플래그를 읽어,

- 앱 상단에 슬림한 **"읽기 전용 데모"** 배너를 표시하고,
- 쓰기 액션(예: "스캔", "프로젝트 등록" 버튼)을 비활성화하고 이유를 툴팁으로
  안내합니다.

실제 경계는 미들웨어이며, UI 게이팅은 막다른 클릭을 줄이는 보조 수단입니다.

## 2. 일일 자동 리셋 (systemd 타이머)

Hetzner 데모 호스트는 리셋을 **systemd 타이머**로 실행합니다. 유닛 파일은
`deploy/hetzner/` 에 있습니다.

- `trustedoss-demo-reset.service` — 리셋 스크립트를 실행 중인 백엔드 컨테이너
  안에서 실행하는 `oneshot` 유닛입니다.

  ```
  ExecStart=/usr/local/bin/docker-compose -f docker-compose.yml \
    exec -T -e APP_ENV=demo backend python -m scripts.reset_demo
  ```

  컨테이너 안에서 실행하면 HTTP `DEMO_READ_ONLY` 가드를 우회합니다(스크립트가
  Postgres에 직접 접근). 스크립트 자체의 `APP_ENV` 허용 목록이 안전 경계입니다.
- `trustedoss-demo-reset.timer` — 서비스를 매일 **03:17 UTC** 에 실행합니다
  (`OnCalendar=*-*-* 03:17:00 UTC`, `Persistent=true` 이므로 호스트가 내려가 있어
  놓친 리셋은 다음 부팅 때 한 번 실행됩니다).

리셋(`apps/backend/scripts/reset_demo.py`)은,

- **데모 데이터셋만 삭제** — `demo-org` 조직(FK cascade로 팀 → 프로젝트 → 스캔 →
  finding 제거)과 데모 사용자(**demo-org 멤버십 기준** — demo-org 에만 속한
  사용자만 삭제; cascade로 멤버십·알림 제거). 전체 truncate가 아니므로 다른
  조직에도 속한 다른 테넌트의 데이터는 절대 건드리지 않습니다.
- 멱등한 `seed_demo._seed` 로 **재시드** 하므로 데이터셋 형태가 일반 시드와 단일
  소스로 일치합니다.
- `APP_ENV` 가 `dev` 또는 `demo` 가 아니면 **실행을 거부**합니다(프로덕션
  데이터베이스에는 절대 실행되지 않음).

호스트에서 타이머를 설치하고 활성화합니다.

```bash
sudo cp deploy/hetzner/trustedoss-demo-reset.service /etc/systemd/system/
sudo cp deploy/hetzner/trustedoss-demo-reset.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trustedoss-demo-reset.timer
```

:::note 데모 비밀번호 고정
공개 데모 자격 증명이 매일 밤 바뀌지 않도록 호스트 `.env` 의
`DEMO_SUPER_ADMIN_PASSWORD` 를 알려진 값으로 설정하세요. 미설정 시 재시드가 매일
밤 무작위 비밀번호를 생성하지만 평문을 로그에 출력하지 **않으므로** 새 자격 증명을
알 수 없습니다.
:::

전체 배포 런북은 [GCP 데모 SaaS 배포](./gcp-deploy.md) 를 참고하세요.

:::note 수동 리셋
타이머를 기다리지 않고 즉시 리셋을 실행합니다.

```bash
sudo systemctl start trustedoss-demo-reset.service
# 또는 내부 명령을 직접 실행:
docker-compose -f docker-compose.yml exec -T -e APP_ENV=demo backend python -m scripts.reset_demo
```
:::

## 로컬 읽기 전용 데모 (Docker Compose)

읽기 전용 모드는 모든 배포에서 동작합니다. 로컬 읽기 전용 인스턴스는 `.env` 에
`DEMO_READ_ONLY=true` 를 추가하고 백엔드를 재시작하면 됩니다. systemd 타이머는
데모 호스트용이며, 로컬에서는 깨끗한 데이터셋이 필요할 때마다 백엔드 컨테이너
안에서 리셋을 다시 실행하세요.

```bash
docker-compose -f docker-compose.dev.yml exec -e APP_ENV=demo backend \
  python -m scripts.reset_demo
```
