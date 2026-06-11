# 운영 런북 — Hetzner(CAX31) 데모 SaaS

> English version: [`operator-runbook-hetzner.md`](./operator-runbook-hetzner.md)

이 런북은 **아무것도 없는 상태에서 공개 읽기 전용 데모까지** 한 번에 데려갑니다.
Hetzner ARM 서버 한 대 위에, 이미 만들어진 `docker-compose.yml`(Traefik + 자동
HTTPS)을 그대로 써서 TrustedOSS Portal 데모를 띄웁니다. **서비스를 한 번도 배포해
본 적 없는 사람**을 기준으로 썼습니다. 명령은 전부 복붙할 수 있고, 모든 선택에는
이유를 붙였습니다.

명령은 직접 실행합니다. 자동으로 대신 해주는 부분은 없으니, 한 절씩 차근차근
따라오세요.

---

## 0. 완성되면 어떤 모습인가

```
방문자 ──HTTPS──> Cloudflare DNS ──> Hetzner CAX31 (Ubuntu 24.04)
                                       └─ Traefik (TLS, Let's Encrypt)
                                          ├─ frontend  (React SPA)
                                          └─ backend   (FastAPI) + worker + beat
                                             └─ Postgres 17 + Redis 7
```

- **공개 읽기 전용 데모**: 누구나 데모 계정으로 로그인해 실제 스캔 데이터를 둘러볼 수
  있지만, 생성·수정·삭제는 전부 차단됩니다(HTTP 403).
- **스스로 깨끗해지는 데이터**: 매일 03:17 UTC에 타이머가 데모 데이터를 지우고 다시
  심어, 방문자가 남긴 흔적이 쌓이지 않습니다.
- **매일 로컬 백업**: 매일 02:30 UTC에 `pg_dump` + 워크스페이스 압축 백업이 돕니다.
- **비용**: 월 **약 $18** (CAX31 $15 + Hetzner 백업 $3). Cloudflare DNS는 무료.

### 이 런북에 미리 박아둔 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 리버스 프록시 / TLS | **Traefik** (기존 compose 재사용) | 이미 구성·검증됨, 추가 작업 0 |
| 백업 | **로컬만** (지금은) | 가장 단순한 시작. 오프사이트는 나중에 추가(§11) |
| 배포/업그레이드 | **수동** (SSH로 `upgrade.sh`) | 무슨 일이 일어나는지 직접 보면서 학습하기 좋음 |
| Compose 바이너리 | **V2 바이너리를 `docker-compose`로** | arm64 네이티브 + 스크립트가 부르는 하이픈 명령 유지 |

---

## 1. 시작 전 준비물

1. **내가 관리하는 도메인** (예: `demo.trustedoss.dev`). DNS `A` 레코드를 서버로
   가리킬 겁니다. (어느 등록기관이든 가능. 이 런북은 무료이고 단순해서 Cloudflare로
   DNS를 씁니다.)
2. **Hetzner Cloud 계정** — <https://console.hetzner.com>. 결제 수단 등록.
3. **내 노트북의 SSH 키 쌍.** 없다면:
   ```bash
   ssh-keygen -t ed25519 -C "trustedoss-demo"
   # 기본값으로 Enter, 원하면 passphrase 설정
   cat ~/.ssh/id_ed25519.pub      # <-- 이게 '공개' 키, 이걸 붙여넣습니다
   ```
   `cat`이 출력한 줄(`ssh-ed25519 ...`로 시작)이 cloud-init 파일에 들어갑니다.
   **개인 키**(`id_ed25519`, `.pub` 없는 쪽)는 **절대 공유하지 마세요.**
4. 설치 중 입력할 **강한 super-admin 비밀번호**(12자 이상).

---

## 2. 서버 생성 (cloud-init이 OS 준비를 해줌)

레포에 [`scripts/hetzner-cloud-init.yaml`](../scripts/hetzner-cloud-init.yaml)이
들어있습니다. Docker, `docker-compose` 바이너리, 방화벽을 설치하고, 내 SSH 키를 가진
`trustedoss` 로그인 유저를 만들고, 레포를 `/opt/trustedoss/portal`에 클론합니다.
비밀은 전혀 건드리지 않습니다. 앱 설치는 §5에서 직접 합니다.

1. `scripts/hetzner-cloud-init.yaml`을 열어 **`__SSH_PUBLIC_KEY__`**를
   `cat ~/.ssh/id_ed25519.pub` 출력 한 줄로 통째로 바꿉니다.
   - (선택) 파일 안의 Compose 버전을
     <https://github.com/docker/compose/releases> 최신으로 올려도 됩니다.
2. Hetzner Console → **Add Server**:
   - **Location**: Helsinki(`hel1`) 또는 Falkenstein(`fsn1`).
   - **Image**: Ubuntu 24.04.
   - **Type**: **CAX31** (Arm64, 8 vCPU / 16 GB).
   - **Backups**: 켜기 ($3/월 옵션 — 우리 pg_dump 백업과는 별개인 Hetzner 쪽 VM
     스냅샷. 둘 다 저렴한 보험).
   - **Cloud config**: 편집한 `hetzner-cloud-init.yaml` **전체**를 붙여넣기.
   - 서버를 만들고 **공인 IPv4**를 적어둡니다.
3. 첫 부팅에 2~3분 기다립니다. cloud-init은 한 번만 돕니다. 궁금하면 Hetzner 웹
   콘솔(VM 시리얼 콘솔)로 진행을 볼 수 있습니다.

> 클론은 이 배포 파일들을 포함하도록 `main`을 따라갑니다. **앱 컨테이너 버전**은
> `.env`의 `IMAGE_TAG`(기본 `0.10.0`)로 따로 고정됩니다 — git 체크아웃은 compose
> 파일·스크립트·systemd 유닛만 제공합니다.

---

## 3. DNS — 도메인을 서버로 연결

DNS 제공자(예: Cloudflare 대시보드 → 내 zone → DNS):

- **`A` 레코드** 추가: 이름 `demo`(→ `demo.trustedoss.dev`), 값 = 서버 **공인 IPv4**.
- **"DNS only"(회색 구름)로 두세요. 프록시(주황 구름) 금지.**
  이게 중요합니다: Traefik은 **80 포트의 HTTP-01 챌린지**로 Let's Encrypt에서 TLS
  인증서를 받는데, 그러려면 서버가 직접 닿아야 합니다. 프록시(주황) 레코드는
  Cloudflare 자체 인증서로 사이트를 감싸서 최초 발급을 깨뜨릴 수 있습니다. 인증서가
  안정되면 나중에 프록시로 바꿔도 되지만, 처음엔 회색으로 시작하세요.

전파 확인(서버 IP가 나올 때까지 기다림):
```bash
dig +short demo.trustedoss.dev
```

---

## 4. 첫 SSH 접속

내 노트북에서:
```bash
ssh trustedoss@demo.trustedoss.dev
# 또는: ssh trustedoss@<서버-IPv4>
```
다음 단계를 알려주는 안내 문구(MOTD)와 함께 셸에 들어갑니다.
`Permission denied (publickey)`가 나오면 `__SSH_PUBLIC_KEY__`가 틀렸거나 빠진
겁니다 — §1.3, §2.1을 다시 확인하세요.

기본 확인:
```bash
docker --version
docker-compose version          # Compose v2.x가 찍혀야 함
ls /opt/trustedoss/portal       # 클론된 레포
```

---

## 5. 앱 설치 (대화형 — 여기서 비밀을 입력)

```bash
cd /opt/trustedoss/portal
bash scripts/install.sh
```

프롬프트 답변:

| 프롬프트 | 입력 |
|----------|------|
| `Public URL` | `https://demo.trustedoss.dev` (실제 HTTPS 도메인) |
| `Let's Encrypt contact email` | 실제 이메일 (인증서 만료 알림이 옴) |
| `Super admin email` | 관리자 로그인, 예: `admin@trustedoss.dev` |
| `Password (12+ chars)` | 기억할 수 있는 강한 비밀번호 |

스크립트가 나머지 비밀(JWT 키, DB 비밀번호)을 생성하고, `:0.10.0` 이미지(arm64)를
받고, 스택을 띄우고, DB 마이그레이션을 돌리고, super-admin을 만듭니다. Traefik이 TLS
인증서를 자동 요청하는데, 발급되는 동안 첫 요청은 10~30초 걸릴 수 있습니다.

> **Trivy 취약점 DB**: 워커가 첫 부팅에 약 600MB를 받습니다. 스택이 뜬 뒤 1~3분 안에
> 취약점 결과가 채워집니다.

---

## 6. 데모 모드 켜기

`install.sh`는 평범한(쓰기 가능) 배포를 만듭니다. **공개 읽기 전용 데모**로 만들려면
`.env`에서 스위치 두 개를 바꾸고, 컨테이너를 다시 띄워 반영합니다.

```bash
cd /opt/trustedoss/portal
nano .env
```
다음 줄을 설정/추가:
```ini
APP_ENV=demo
DEMO_READ_ONLY=true
```
- `APP_ENV=demo`는 시드/리셋 스크립트를 풀어줍니다(이 스크립트들은 `dev`/`demo` 밖에선
  실행을 거부). 또 prod 전용 강제 조건 몇 개를 완화합니다.
- `DEMO_READ_ONLY=true`는 백엔드가 HTTP로 들어오는 모든 쓰기를 거부하게 합니다(로그인/
  갱신/로그아웃 인증 흐름만 예외). 친절한 403을 돌려줍니다. 읽기는 영향 없음. 이게
  공개 안전 경계입니다.

`DOMAIN`, `TLS_EMAIL`, `CORS_ALLOWED_ORIGINS`가 설치 시 HTTPS 도메인으로 잘
들어갔는지 확인하고, 다시 띄웁니다:
```bash
docker-compose -f docker-compose.yml up -d
```

---

## 7. 데모 데이터 심기

`.env`의 `DEMO_SUPER_ADMIN_PASSWORD` 기본값은 `DemoTest2026!`입니다 — 이게 **시드된
모든 데모 계정**의 비밀번호입니다. 다른 값을 쓰고 싶으면 `.env`에서 먼저 바꾸세요(그
값이 시드 유저에 적용됨).

```bash
docker-compose -f docker-compose.yml exec -T backend python -m scripts.seed_demo
```
`demo-org`, 3개 팀, 5명 유저(`*@demo.trustedoss.dev`), 5개 프로젝트, 현실적인 CVE /
라이선스 / 알림 데이터를 만듭니다. **멱등**합니다 — `demo-org`가 이미 있으면 다시
돌려도 안전하게 아무 일도 안 합니다.

데모 로그인(데모 랜딩 페이지에 안내):
- `frontend-admin@demo.trustedoss.dev` / `DemoTest2026!` (CVE·라이선스 데이터가 가장 풍부)
- 다른 시드 유저도 같은 `@demo.trustedoss.dev` / `DemoTest2026!` 패턴

---

## 8. 동작 확인

- 브라우저에서 `https://demo.trustedoss.dev` 열기 → 로그인 페이지가 **유효한 HTTPS**로
  뜸(자물쇠, 경고 없음).
- 데모 계정으로 로그인 → 프로젝트·컴포넌트·CVE를 둘러볼 수 있음.
- 무언가 생성/수정 시도 → **"Read-only live demo"** 403이 나옴. `DEMO_READ_ONLY`가
  작동한다는 뜻.
- 서버에서 헬스 체크:
  ```bash
  curl -fsS https://demo.trustedoss.dev/health && echo OK
  ```

---

## 9. 매일 도는 타이머 켜기 (리셋 + 백업)

[`deploy/hetzner/`](../deploy/hetzner/)에 systemd 유닛 2종이 있습니다:

```bash
cd /opt/trustedoss/portal
sudo cp deploy/hetzner/trustedoss-demo-reset.service \
        deploy/hetzner/trustedoss-demo-reset.timer \
        deploy/hetzner/trustedoss-backup.service \
        deploy/hetzner/trustedoss-backup.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trustedoss-demo-reset.timer trustedoss-backup.timer
```

예약됐는지 확인:
```bash
systemctl list-timers 'trustedoss-*'
```
다음 실행 시각이 보여야 합니다(02:30 백업, 03:17 리셋, 둘 다 UTC).

리셋을 한 번 수동 테스트(선택 — 지금 데모 데이터를 지우고 다시 심음):
```bash
sudo systemctl start trustedoss-demo-reset.service
journalctl -u trustedoss-demo-reset.service -n 30 --no-pager
```

> 유닛 파일은 레포가 `/opt/trustedoss/portal`, 유저가 `trustedoss`, compose
> 바이너리가 `/usr/local/bin/docker-compose`라고 가정합니다 — cloud-init을 썼다면 전부
> 맞습니다. 바꿨다면 복사 전에 유닛 파일을 수정하세요.

---

## 10. 운영 (Day-2)

**로그 보기**
```bash
cd /opt/trustedoss/portal
docker-compose -f docker-compose.yml logs -f backend     # 또는: traefik, worker, frontend
```

**서비스 재시작**
```bash
docker-compose -f docker-compose.yml restart backend
```

**지금 수동 백업**
```bash
bash scripts/backup.sh          # backups/<UTC-스탬프>/ 에 저장
ls -lh backups/
```

**백업에서 복원** (파괴적 — 현재 데이터를 덮어씀)
```bash
bash scripts/restore.sh backups/<스탬프-디렉터리>
```

**새 릴리스로 업그레이드** (예: `v0.11.0` 출시 시)
```bash
cd /opt/trustedoss/portal
git fetch --tags
git checkout v0.11.0            # 새 태그
# IMAGE_TAG가 템플릿이 아니면 .env에서 맞춰줌:
nano .env                       # IMAGE_TAG=0.11.0
bash scripts/upgrade.sh         # 백업 → pull → 재시작 → 마이그레이션
```

**디스크 확인** (Trivy DB + 스캔 워크스페이스가 시간이 지나며 커짐)
```bash
df -h /
du -sh /opt/trustedoss/workspace backups/
```

---

## 11. 오프사이트로 가기 (나중에)

지금 백업은 **로컬만** 있습니다(운영자 선택). 서버가 죽어도 데이터를 잃지 않으려면
오프사이트 사본을 두세요:

1. 무료 **Cloudflare R2**(10GB) 또는 **Backblaze B2** 버킷 + API 키 생성.
2. 서버에 `rclone` 설치·설정(`rclone config`, 버킷을 가리키는 S3 호환 remote).
3. `trustedoss-backup.service`에 최신 `backups/<스탬프>/`를 remote로 올리는
   `ExecStartPost=`를 추가, 예:
   ```ini
   ExecStartPost=/usr/bin/rclone copy /opt/trustedoss/portal/backups r2:trustedoss-backups --max-age 25h
   ```
4. `sudo systemctl daemon-reload`.

앱 변경은 필요 없습니다 — 순수하게 백업 파이프라인 추가입니다.

---

## 12. 철거

데모를 완전히 내리려면:
```bash
cd /opt/trustedoss/portal
docker-compose -f docker-compose.yml down          # 데이터 볼륨까지 지우려면 -v 추가
```
그다음 Hetzner Console에서 서버를 삭제하고 DNS `A` 레코드를 제거합니다.

---

## 문제 해결

| 증상 | 유력한 원인 | 해결 |
|------|-------------|------|
| 브라우저가 TLS 경고 / "안전하지 않음" | 인증서 미발급, 또는 DNS가 아직 프록시 | DNS가 **회색 구름**인지 확인; 30초 대기; `docker-compose logs traefik`로 ACME 오류 확인 |
| SSH에서 `Permission denied (publickey)` | `__SSH_PUBLIC_KEY__` 틀림/누락 | §1.3 재확인; Hetzner 웹 콘솔 + `~/.ssh/authorized_keys`로 키 추가 가능 |
| 설치 중 백엔드가 healthy 안 됨 | 이미지 pull 실패 또는 마이그레이션 오류 | `docker-compose logs backend`; `:0.10.0` arm64 이미지가 받혔는지 확인 |
| 막아야 할 쓰기가 성공함 | `DEMO_READ_ONLY` 미반영 | 런타임 env임 — `.env` 수정 후 `docker-compose up -d` 했나요?(§6) |
| `seed_demo`가 "APP_ENV not allowed" exit 1 | 컨테이너의 `APP_ENV`가 아직 `dev`/미설정 | `.env`에 `APP_ENV=demo` 설정, `up -d`, 재시도(§6) |
| 리셋 타이머가 안 돔 | 타이머 미활성 또는 컨테이너 down | `systemctl list-timers`; `journalctl -u trustedoss-demo-reset` |

---

## 참고 — 이 배포의 파일들

| 파일 | 역할 |
|------|------|
| `scripts/hetzner-cloud-init.yaml` | 첫 부팅 OS 프로비저닝(Docker, 유저, 방화벽, 레포 클론) |
| `docker-compose.yml` | 7서비스 스택(Traefik + Postgres + Redis + backend/worker/beat + frontend) |
| `scripts/install.sh` | 대화형 최초 설치(비밀, up, 마이그레이션, super-admin) |
| `scripts/upgrade.sh` / `backup.sh` / `restore.sh` | Day-2 라이프사이클 |
| `deploy/hetzner/trustedoss-demo-reset.{service,timer}` | 매일 03:17 UTC 데모 초기화 + 재시드 |
| `deploy/hetzner/trustedoss-backup.{service,timer}` | 매일 02:30 UTC 로컬 백업 |
| `.env` | 모든 런타임 설정 + 비밀(절대 커밋 금지) |
