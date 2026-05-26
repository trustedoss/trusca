# 배포 전 필수 수동 테스트 시나리오 (Pre-Release Manual Test Plan)

> 작성일: 2026-05-25 · 대상: 오픈소스 공개 전 직접 검증
> 우선순위: **P0** = 공개 전 반드시 통과 · **P1** = 권장

---

## 0. 테스트 환경 준비

### 방법 A — 개발 스택 (기능 검증용, 빠름)

```bash
# 전체 스택 기동 (PostgreSQL 17 + Redis + Celery + FastAPI + Vite HMR + DT)
make dev-up
# 또는: docker-compose -f docker-compose.dev.yml up -d

# 마이그레이션 적용 (dev-reset 직후엔 수동 필요)
docker-compose -f docker-compose.dev.yml exec backend alembic upgrade head

# 접속
#   Frontend  http://localhost:5173
#   API docs  http://localhost:8000/docs
#   DT        http://localhost:8080
```

### 방법 B — 프로덕션 설치 경로 (공개 사용자 첫 경험 재현, **P0**)

가장 중요한 테스트입니다. **새 OSS 사용자가 제일 먼저 하는 일**이 `install.sh`이기 때문입니다.
가능하면 **깨끗한 Linux VM**(또는 새 Colima/Docker 컨텍스트)에서 실행하세요.

```bash
# 대화형 설치 마법사
bash scripts/install.sh

# 또는 비대화형 (CI/자동화 재현)
INSTALL_HOST=http://localhost \
INSTALL_ADMIN_EMAIL=admin@trustedoss.local \
bash scripts/install.sh --no-prompt
```

> 합격 핵심: `.env`가 자동 생성되고 SECRET_KEY/DB/admin 비밀번호가 `openssl rand`로
> **랜덤 생성**되며, `alembic upgrade head`까지 자동 수행 후 super_admin이 생성되어
> 바로 로그인 가능해야 한다.

---

## 1. 설치 · 부팅 (P0)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| INS-1 | 클린 호스트 설치 | 방법 B 실행 | 에러 없이 전 컨테이너 healthy, 안내된 URL 접속 가능 |
| INS-2 | 시크릿 랜덤 생성 | 설치 후 `.env` 확인 | `SECRET_KEY`·`POSTGRES_PASSWORD`·admin 비밀번호가 기본값 아님(랜덤) |
| INS-3 | 약한 SECRET_KEY 거부 | `APP_ENV=production SECRET_KEY=short` 로 backend 기동 | 부팅 즉시 RuntimeError로 fail-fast (32자 미만 거부) |
| INS-4 | CORS 프로덕션 강제 | 프로덕션 모드에서 `CORS_ALLOWED_ORIGINS` 미설정 | dev 기본값(`*`)이 적용되지 않음 / 명시 강제 |
| INS-5 | 마이그레이션 멱등 | `alembic upgrade head` 재실행 | 이미 head면 변경 없음, 에러 없음 |

**확인 방법**: `docker-compose ps`로 health, `docker-compose logs backend | grep -i error`로 부팅 로그.

---

## 2. 인증 · 세션 (P0)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| AUTH-1 | 회원가입 | `/register` | 12자 미만 비밀번호 거부, 약한 비밀번호(NIST 차단 사전) 거부 |
| AUTH-2 | 로그인/로그아웃 | `/login` → 대시보드 → 로그아웃 | 로그인 성공, 로그아웃 후 보호 페이지 접근 시 `/login` 리다이렉트 |
| AUTH-3 | Access 토큰 만료 | 로그인 후 30분 대기(또는 토큰 조작) | 만료 후 refresh로 자동 갱신, 사용자 체감 끊김 없음 |
| AUTH-4 | Refresh 회전·재사용 탐지 | 같은 refresh 토큰 2회 사용 시도 | 2회차는 거부 + 세션 무효화(재사용 공격 탐지) |
| AUTH-5 | 로그인 레이트 리밋 | 같은 IP로 1분 내 6회 실패 | 6회차 `429` + `Retry-After` 헤더 반환 |
| AUTH-6 | 비밀번호 재설정 | `/forgot-password` 플로우 | 토큰 발급·검증 정상, 토큰 1회용 |

**확인 방법**:
- 레이트 리밋: `for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/v1/auth/login -d '{"username":"x","password":"y"}' -H 'Content-Type: application/json'; done` → 마지막이 `429`
- 토큰: 브라우저 DevTools → Application → Cookies에서 `refresh_token`이 `HttpOnly`·`Secure`·`SameSite=Lax`인지 확인.

---

## 3. 권한 · RBAC (P0)

데모 계정 비밀번호는 모두 `DemoTest2026!`.

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| RBAC-1 | Developer 격리 | Developer로 로그인 | `/admin/*` 접근 시 **404**(존재 은닉), 다른 팀 프로젝트 비노출 |
| RBAC-2 | Team Admin 범위 | Team Admin으로 팀원 추가/삭제 | 자기 팀만 관리 가능, 타 팀 불가 |
| RBAC-3 | Super Admin 전권 | Super Admin으로 `/admin/*` | 전체 사용자·팀·DT·감사로그 접근 가능 |
| RBAC-4 | IDOR/BOLA | A팀 사용자가 B팀 프로젝트 ID로 직접 API 호출 | `403`/`404`, 데이터 누수 없음 |
| RBAC-5 | 프로젝트 가시성 | team-only ↔ org-wide 전환 | 설정대로 타 팀 노출/비노출 |

**확인 방법**: 두 브라우저(또는 시크릿 창)로 서로 다른 역할 동시 로그인 후 교차 접근. IDOR는 한쪽 토큰으로 다른 쪽 리소스 ID를 `curl`.

---

## 4. 스캔 파이프라인 (P0)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| SCAN-1 | 소스 스캔 E2E | 프로젝트 등록 → 업로드/경로 입력 → 스캔 실행 | cdxgen→ORT→DT 순서로 진행, **비동기**(즉시 응답 후 진행률) |
| SCAN-2 | 실시간 진행률 | 스캔 중 프로젝트 상세 관찰 | WebSocket 진행 바 갱신, 30분+ 스캔도 UI 멈춤 없음 |
| SCAN-3 | 컨테이너 스캔 | 이미지 지정 → Trivy 스캔 | OS 패키지 CVE 결과 표시 |
| SCAN-4 | 스캔 실패 처리 | 잘못된 입력/깨진 아카이브로 스캔 | 실패 상태 + 명확한 에러 메시지, 큐 막힘 없음 |
| SCAN-5 | 동시 스캔 | 2~3개 스캔 동시 실행 | 큐 정상 처리, 메모리 한계 시 graceful(로컬 Colima 8GiB 주의) |
| SCAN-6 | 강제 종료 | 실행 중 스캔 admin에서 종료 | 큐에서 제거, 좀비 프로세스 없음 |

**확인 방법**: 작은 npm/pip 프로젝트(예: `package.json` 하나)로 시작 → 빠른 회전. `/scans` 전역 큐와 `/admin/scans`에서 상태 교차 확인. 진행률은 DevTools → Network → WS 프레임.

---

## 5. 취약점 · 라이선스 · SBOM (P0)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| VLN-1 | CVE 표시 | 스캔 후 Vulnerabilities 탭 | Critical/High/Medium/Low 색상 분류, CVE ID·CVSS·수정버전 |
| VLN-2 | 취약점 상태 워크플로우 | CVE 상태 변경(triage) | VEX 7-state 전이, 감사 로그 기록 |
| LIC-1 | 라이선스 분류 | Licenses 탭 | 금지(AGPL/GPL/SSPL/BUSL)=ERROR, 조건부=WARNING, 허용=정상 |
| LIC-2 | 정책 게이트 | 금지 라이선스 포함 프로젝트 | 정책 위반 표시 + 승인 워크플로우 진입 |
| SBOM-1 | SBOM 다운로드 | SBOM 탭 → CycloneDX/SPDX, JSON/XML | 각 포맷 유효(스키마 통과), Excel/PDF 보고서 생성 |
| SBOM-2 | SBOM 서명 검증 | 서명 SBOM 다운로드 후 cosign 검증 | `cosign verify-blob`으로 서명 검증 통과(v2.3 기능) |

**확인 방법**:
- SBOM 유효성: 다운로드한 CycloneDX JSON을 `cyclonedx validate` 또는 온라인 검증기.
- 서명: `docs/`의 cosign 검증 가이드(v2.3-s3) 절차대로 `cosign verify-blob --key <pub> --signature <sig> <sbom>`.

---

## 6. 거버넌스 · 워크플로우 (P1)

| ID | 시나리오 | 합격 기준 |
|----|----------|-----------|
| GOV-1 | 컴포넌트 승인 | Pending→Under Review→Approved/Rejected 전이, 권한별 가능 동작 구분 |
| GOV-2 | 의무사항 추적 | 조건부 라이선스의 의무사항(고지/소스공개) 표시 |
| GOV-3 | NOTICE 파일 생성 | Obligations 탭에서 NOTICE 자동 생성·다운로드, 내용 정확 |
| GOV-4 | 라이선스 정책 편집 | 정책 변경 후 재평가 시 게이트 결과 반영 |
| GOV-5 | 리스크 스코어 | 프로젝트별/전사 대시보드 스코어가 실제 findings 반영 |

---

## 7. CI/CD 연동 · 빌드 게이트 (P0 — 제품 핵심 가치)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| CI-1 | API Key 발급/폐기 | `/integrations` | 키 발급, 표시 1회성, 폐기 후 401 |
| CI-2 | 빌드 차단 게이트 | API Key로 스캔 → Critical CVE 또는 금지 라이선스 | **exit code 1** 반환(빌드 차단) |
| CI-3 | 게이트 통과 | 깨끗한 프로젝트 스캔 | exit code 0 |
| CI-4 | Webhook | GitHub/GitLab push/PR webhook 발사 | 스캔 트리거, PR/MR 자동 코멘트 게시 |
| CI-5 | 인증 없는 접근 | API Key 없이 보호 엔드포인트 호출 | 401, 명확한 RFC7807 에러 |

**확인 방법**: 발급한 API Key로 CLI 호출
`curl -H "Authorization: Bearer <api-key>" ...` 후 `echo $?`로 exit code. GitHub Actions action(`trustedoss/scan-action`)이 있으면 샘플 레포에서 실제 워크플로 1회.

---

## 8. 알림 (P1)

| ID | 시나리오 | 합격 기준 |
|----|----------|-----------|
| NOTI-1 | 이메일(SMTP) | 새 Critical CVE 발생 시 SMTP 메일 발송(테스트 SMTP: MailHog 등) |
| NOTI-2 | Slack Webhook | 테스트 Webhook URL로 메시지 수신 |
| NOTI-3 | Teams Webhook | 테스트 Webhook URL로 메시지 수신 |
| NOTI-4 | 새 CVE 재탐지 | DT NVD 동기화 후 기존 컴포넌트에 새 CVE 매칭 → 알림 발행 |

**확인 방법**: SMTP는 [MailHog](https://github.com/mailhog/MailHog) 컨테이너 띄워 `SMTP_HOST=mailhog` 설정. Slack/Teams는 임시 incoming webhook.

---

## 9. DT 안정성 (P0 — v1 핵심 문제)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| DT-1 | Circuit Breaker | DT 컨테이너 강제 중지(`docker-compose stop dtrack-api`) 후 취약점 조회 | OPEN 상태 → **PostgreSQL 캐시** 반환, 포털 정상 동작 |
| DT-2 | Health 모니터 | DT 중지 상태 60초+ 유지 | health unhealthy 감지, 자동 복구 시도 |
| DT-3 | 복구 | DT 재시작 | Circuit CLOSED 복귀, 실시간 조회 재개 |
| DT-4 | 고아 정리 | DT에만 있고 포털엔 없는 프로젝트 생성 | Celery Beat(6h) 또는 Admin UI에서 감지·정리 |
| DT-5 | 동기화 상태 | `/admin/dt` 대시보드 | sync 상태·last heartbeat·circuit 상태 표시 |

**확인 방법**: DT를 `docker-compose stop`으로 내린 상태에서 이미 스캔된 프로젝트의 Vulnerabilities 탭이 **여전히 보이는지**가 핵심. 에러 페이지가 뜨면 실패.

---

## 10. 관리자 기능 (P1)

| ID | 영역 | 합격 기준 |
|----|------|-----------|
| ADM-1 | `/admin/users` | 사용자 생성/비활성/역할 변경 |
| ADM-2 | `/admin/teams` | 팀 생성/삭제, 멤버 관리 |
| ADM-3 | `/admin/scans` | 실행중/대기/실패/강제종료 큐 모니터 |
| ADM-4 | `/admin/disk` | 디스크 사용량 표시, 임계치 알림 |
| ADM-5 | `/admin/audit` | 모든 쓰기 작업 기록, 검색/필터/CSV 내보내기 |
| ADM-6 | `/admin/health` | 전 컴포넌트(DB/Redis/DT/Celery) 상태 |

**확인 방법**: 감사 로그는 임의 쓰기 작업(프로젝트 생성 등) 후 `/admin/audit`에 즉시 기록되는지, PII(비밀번호/토큰/이메일)가 **마스킹**되어 있는지 확인.

---

## 11. 백업 · 복원 · 업그레이드 (P0 — 데이터 안전)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| BAK-1 | 수동 백업 | `bash scripts/backup.sh` 또는 Admin UI | 백업 아카이브 생성(DB + 워크스페이스) |
| BAK-2 | 복원 | 데이터 일부 삭제 → `bash scripts/restore.sh <backup>` | 삭제 데이터 복구, 무결성 정상 |
| BAK-3 | 자동 백업 | Celery Beat 자정 스케줄 확인 | 스케줄 등록·실행 로그 |
| UPG-1 | 업그레이드 | `bash scripts/upgrade.sh` (이전→현재 이미지 태그) | 마이그레이션 자동 적용, 다운타임 후 데이터 보존 |

**확인 방법**: BAK-2가 가장 중요 — 반드시 **실제 데이터 삭제 후 복원**으로 round-trip 검증. 운영자가 가장 두려워하는 시나리오.

---

## 12. i18n · UX 마감 (P1)

| ID | 시나리오 | 합격 기준 |
|----|----------|-----------|
| I18N-1 | 언어 토글 | EN ↔ KO 전환 시 전 화면 번역, 누락 키(raw key 노출) 없음 |
| I18N-2 | 복수형 | `{{count}}` 형태만 사용(`_one`/`_other` 금지 정책) 정상 표시 |
| UX-1 | 로딩 상태 | 스캔/조회 시 스켈레톤 UI, 빈 상태 메시지 |
| UX-2 | 에러 표면화 | API 실패 시 사용자에게 명확한 토스트/메시지(스택트레이스 노출 금지) |

**확인 방법**: KO로 전환 후 모든 주요 화면 1회 순회하며 영문 잔존/깨진 키 육안 확인.

---

## 13. 보안·에러 규약 (P0)

| ID | 시나리오 | 절차 | 합격 기준 |
|----|----------|------|-----------|
| SEC-1 | RFC7807 | 임의 4xx/5xx 유발 | `Content-Type: application/problem+json`, `type/title/status/detail/instance` 필드 |
| SEC-2 | 보안 헤더 | 응답 헤더 검사 | HSTS(프로덕션)·X-Content-Type-Options·적절한 CSP |
| SEC-3 | 스택트레이스 비노출 | 500 에러 유발 | 사용자 응답에 내부 경로/트레이스 없음(로그에만) |
| SEC-4 | 인증 누락 점검 | 전 엔드포인트 무토큰 호출 | 명시 예외 외 전부 401 |

**확인 방법**: `curl -i`로 헤더 직접 확인. SEC-4는 `/docs`의 엔드포인트 목록을 보며 인증 표시 없는 것 골라 무토큰 호출.

---

## 14. 데모 SaaS read-only (해당 시, P1)

| ID | 시나리오 | 합격 기준 |
|----|----------|-----------|
| DEMO-1 | read-only 모드 | 데모 모드에서 쓰기 작업 차단(읽기만) |
| DEMO-2 | 일일 리셋 | 데모 데이터 일일 리셋 동작 |
| DEMO-3 | OAuth | GitHub/Google 로그인(데모 전용) 정상 |

---

## 우선 실행 권장 순서

깨끗한 환경에서 **위→아래** 순으로:

1. **INS (설치)** — 첫 사용자 경험. 여기서 막히면 나머지 무의미.
2. **AUTH + RBAC** — 보안 게이트. 공개 후 가장 먼저 공격받는 표면.
3. **SCAN + VLN/LIC/SBOM** — 제품 핵심 가치. 실제로 동작하는가.
4. **CI 빌드 게이트** — 차별화 기능. exit code 1 검증.
5. **DT 안정성** — v1의 핵심 문제 해결 검증.
6. **백업/복원 round-trip** — 데이터 안전.
7. 나머지(거버넌스/알림/관리자/i18n/보안헤더) 순회.

> 발견한 버그는 `.github/ISSUE_TEMPLATE/bug_report.yml`로 이슈화하면 공개 후 추적 가능.
