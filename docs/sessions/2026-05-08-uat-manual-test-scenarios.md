# TrustedOSS Portal — 수동 UAT 시나리오

> 작성일: 2026-05-08  
> 대상 버전: Phase 0~4 완료 (PR #1 ~ PR #14 + chore PR #1~#8 머지 기준)  
> 브라우저: `http://localhost:5173`  
> API: `http://localhost:8000`
>
> **v2.0.0 GA 추가 시나리오 (PR #28~#33, 시나리오 16~27)**: [`2026-05-09-uat-v2.0.0-scenarios.md`](./2026-05-09-uat-v2.0.0-scenarios.md) 참조.

---

## 테스트 계정

| 구분 | 이메일 | 비밀번호 | 권한 |
|------|--------|----------|------|
| **Super Admin** | `admin@trustedoss.dev` | `TrustedAdmin2026!` | 시스템 전체 관리자 (Admin Panel 접근 가능) |
| **Developer** | `dev@trustedoss.dev` | `TrustedDev2026!` | 팀 멤버 (일반 사용자) |

### Developer 계정 시드 데이터

| 프로젝트 | 컴포넌트 | 취약점 | 라이선스 |
|---------|---------|--------|---------|
| **my-nodejs-app** | 80개 | 64개 (critical 10, high 12, medium 15, low 22, info 3, unknown 2) | 50개 (forbidden 13, conditional 13, allowed 12, unknown 12) |
| my-python-lib | 0개 | — | — |
| empty-project | 0개 | — | — |

> `my-nodejs-app`이 모든 기능 테스트의 주 대상입니다.

---

## 시나리오 1: 인증 (Authentication)

### 1-1. 정상 로그인 — Developer

1. `http://localhost:5173/login` 접속
2. Email: `dev@trustedoss.dev`, Password: `TrustedDev2026!` 입력
3. **Login** 클릭

**기대 결과**
- `/` (대시보드)로 리다이렉트
- 상단 헤더에 사용자 이름 또는 이메일 표시
- 사이드바에 Projects, Scans 메뉴 보임
- `/admin/*` 메뉴는 **보이지 않아야 함**

---

### 1-2. 잘못된 비밀번호

1. `/login` 에서 Email: `dev@trustedoss.dev`, Password: `WrongPassword!` 입력
2. **Login** 클릭

**기대 결과**
- 에러 메시지 표시 ("Invalid credentials" 또는 유사 문구)
- 비밀번호 힌트 없음 (어느 필드가 틀렸는지 알 수 없어야 함)
- 페이지 이동 없음

---

### 1-3. 로그아웃

1. Developer로 로그인된 상태
2. 우측 상단 사용자 메뉴 → **Logout**

**기대 결과**
- `/login` 으로 리다이렉트
- 다시 `/` 접속 시 로그인 페이지로 리다이렉트

---

### 1-4. Super Admin 로그인

1. `/login` 에서 `admin@trustedoss.dev` / `TrustedAdmin2026!` 로 로그인

**기대 결과**
- 사이드바에 **Admin** 섹션 메뉴 보임 (Users, Teams, DT, Scans, Disk, Audit, Health)

---

## 시나리오 2: 프로젝트 목록 (/projects)

> **계정**: Developer (`dev@trustedoss.dev`)로 로그인

### 2-1. 프로젝트 목록 조회

1. 사이드바 **Projects** 클릭 또는 `/projects` 직접 접속

**기대 결과**
- 3개 프로젝트 카드 표시: `my-nodejs-app`, `my-python-lib`, `empty-project`
- `my-nodejs-app` 카드에 컴포넌트 수·취약점 수 요약 표시
- `empty-project` 카드에 "No scan yet" 또는 빈 상태 표시

---

### 2-2. 새 프로젝트 생성

> ⚠️ **미구현** — "New Project" 버튼과 `/projects/new` 라우트가 Phase 5 이전에 구현 예정입니다. 현재 스킵합니다.

---

## 시나리오 3: 프로젝트 상세 — Overview

> **프로젝트**: `my-nodejs-app` 클릭

### 3-1. Overview 탭 확인

**기대 결과**
- **리스크 게이지**: Critical/High 취약점 반영한 전체 리스크 점수 표시
- **취약점 분포 차트**: critical(10), high(12), medium(15), low(22), info(3) 막대 또는 도넛 차트
- **라이선스 분포 차트**: forbidden(13), conditional(13), allowed(12), unknown(12)
- **스캔 이력**: 1회 `succeeded` 스캔 표시 (timestamp, 상태, 컴포넌트 수)
- **최근 스캔 일시**: 오늘 날짜

---

### 3-2. empty-project Overview

1. 프로젝트 목록 → `empty-project` 클릭

**기대 결과**
- "No scans yet" 또는 스캔 없음 상태 UI
- 취약점/라이선스 차트 없음 (빈 상태 처리)

---

## 시나리오 4: 프로젝트 상세 — Components 탭

> **프로젝트**: `my-nodejs-app` → **Components** 탭

### 4-1. 컴포넌트 목록 조회

**기대 결과**
- 80개 컴포넌트 표시 (페이지네이션 또는 무한 스크롤)
- 각 행: 패키지명, 버전, severity 배지, 라이선스 카테고리
- Severity 배지 색상: Critical(빨강), High(주황), Medium(노랑), Low(파랑)

---

### 4-2. 컴포넌트 검색

1. 검색창에 `uat-dev-0` 입력

**기대 결과**
- `uat-dev-00000` ~ `uat-dev-00009` 등 매칭 결과만 표시
- 결과 없는 검색어(`xyz-nonexistent`) 입력 시 "No results" 상태 표시

---

### 4-3. Severity 필터

1. 필터에서 **Critical** 선택

**기대 결과**
- Critical severity 컴포넌트만 표시 (10개 내외)

2. 추가로 **High** 선택 (멀티 필터)

**기대 결과**
- Critical + High 모두 표시 (22개 내외)

---

### 4-4. 컴포넌트 드로어

1. 임의의 컴포넌트 행 클릭

**기대 결과**
- 오른쪽에서 드로어(슬라이드) 열림
- 드로어 내용: PURL, 버전, 라이선스 정보, 해당 컴포넌트의 취약점 목록
- 페이지 이동 없이 드로어 내에서 상세 확인 가능
- 드로어 외부 클릭 또는 X 버튼으로 닫힘

---

## 시나리오 5: 프로젝트 상세 — Vulnerabilities 탭

> **프로젝트**: `my-nodejs-app` → **Vulnerabilities** 탭

### 5-1. 취약점 목록 조회

**기대 결과**
- 64개 취약점 표시
- 각 행: CVE ID, severity 배지, 상태(new/analyzing/not_affected), 컴포넌트명
- Critical이 상단에 정렬 (기본 severity desc 정렬)

---

### 5-2. Severity 필터

1. **Critical** 필터 클릭

**기대 결과**
- Critical 취약점만 표시 (10개)

---

### 5-3. Status 필터

1. 필터에서 **analyzing** 선택

**기대 결과**
- status=analyzing인 항목만 표시 (전체의 약 15%)

---

### 5-4. 취약점 상태 변경 (있는 경우)

1. 임의의 취약점 행 클릭 → 드로어 열기
2. Status 변경 드롭다운에서 `analyzing` → `not_affected` 변경 시도

**기대 결과**
- 상태 변경 성공 시 배지 색/텍스트 즉시 업데이트
- 또는 권한 없음 안내 (Developer 역할 제한)

---

## 시나리오 6: 프로젝트 상세 — Licenses 탭

> **프로젝트**: `my-nodejs-app` → **Licenses** 탭

### 6-1. 라이선스 분포

**기대 결과**
- 도넛 차트: forbidden(13), conditional(13), allowed(12), unknown(12) 비율 표시
- 범례에 각 카테고리별 색상과 수량

---

### 6-2. 라이선스 카테고리별 목록

1. 도넛 차트의 **forbidden** 영역 또는 필터 클릭

**기대 결과**
- Forbidden 라이선스 컴포넌트 목록 필터링
- 각 항목: SPDX ID, 라이선스명, 컴포넌트 수

---

### 6-3. Conditional 라이선스

**기대 결과**
- Conditional 항목에 "법무 검토 필요" 또는 Warning 아이콘 표시

---

## 시나리오 7: 프로젝트 상세 — Obligations 탭

> **프로젝트**: `my-nodejs-app` → **Obligations** 탭

### 7-1. 의무사항 목록

**기대 결과**
- 시드된 7개 의무사항 표시
- 각 항목: 종류(attribution, copyleft, modifications 등), 설명 텍스트, 관련 라이선스
- 종류별 아이콘 또는 배지

---

### 7-2. NOTICE 파일 다운로드

1. **Download NOTICE** 또는 **Export** 버튼 클릭

**기대 결과**
- `NOTICE.txt` 또는 `NOTICE` 파일 다운로드
- 파일 내용에 라이선스 원문 또는 요약 포함

---

## 시나리오 8: 전역 스캔 큐 (/scans)

> 사이드바 **Scans** 클릭

### 8-1. 스캔 목록 조회

**기대 결과**
- dev 계정 팀의 스캔 목록 표시 (succeeded 상태 3개 이상)
- 각 행: 프로젝트명, 스캔 종류(source), 상태, 완료 시각

---

### 8-2. 빈 프로젝트 스캔 트리거 (UI에 버튼 있는 경우)

1. `empty-project` 상세 → **Trigger Scan** 버튼 클릭

**기대 결과**
- 스캔이 큐에 추가 (`queued` 또는 `running` 상태)
- WebSocket 진행 바 또는 progress 표시 (실제 스캔 실행은 외부 도구 없이 성공하지 않을 수 있음)

---

## 시나리오 9: Admin Panel — 사용자 관리

> **계정**: `admin@trustedoss.dev` 로 로그인

### 9-1. 사용자 목록 (/admin/users)

1. 사이드바 **Admin → Users** 클릭

**기대 결과**
- 사용자 목록 표시 (이메일, 역할, 활성 여부)
- `dev@trustedoss.dev`, `admin@trustedoss.dev` 등 확인 가능
- Super Admin 배지 또는 표시

---

### 9-2. Developer 계정으로 /admin/users 직접 접속 (권한 차단 확인)

1. Developer 계정(`dev@trustedoss.dev`)으로 로그인
2. `http://localhost:5173/admin/users` 직접 입력

**기대 결과**
- **404** 또는 접근 거부 화면 (존재-숨김 패턴 — 403이 아닌 404)
- Admin 메뉴 자체가 사이드바에 없어야 함

---

### 9-3. 사용자 활성화/비활성화 (있는 경우)

1. `/admin/users` 에서 임의의 e2e 테스트 계정 클릭
2. **Deactivate** 토글 또는 버튼 클릭

**기대 결과**
- 상태 즉시 반영 (활성 → 비활성)
- 감사 로그에 해당 액션 기록 확인 가능 (`/admin/audit`)

---

## 시나리오 10: Admin Panel — 팀 관리

> **계정**: `admin@trustedoss.dev`

### 10-1. 팀 목록 (/admin/teams)

1. **Admin → Teams** 클릭

**기대 결과**
- 팀 목록 표시 (팀명, 멤버 수, 조직)
- `E2E Team 40ff735d75` 팀 포함

---

### 10-2. 팀 상세 — 멤버 목록

1. 팀 클릭 → 상세 드로어 또는 페이지

**기대 결과**
- 3명 멤버: `admin@trustedoss.dev`(developer), `e2e-extra-0-...`(team_admin), `e2e-extra-1-...`(developer)
- 각 멤버의 역할 배지 표시

---

## 시나리오 11: Admin Panel — DT 모니터

> **계정**: `admin@trustedoss.dev` → **Admin → DT**

### 11-1. DT 연결 상태

**기대 결과**
- Dependency-Track API 연결 상태: **Healthy** 또는 **Degraded** 표시
- DT 버전 정보 (4.13.2)
- Circuit Breaker 상태: CLOSED (정상)
- 마지막 동기화 시각

---

### 11-2. DT 연결이 끊긴 경우 (선택 테스트)

> DT 컨테이너를 일시 중지한 뒤 확인

```bash
docker-compose -f docker-compose.dev.yml pause dtrack-api
```

1. `/admin/dt` 새로고침

**기대 결과**
- 상태: **Unhealthy** 또는 **Degraded**
- Circuit Breaker: OPEN 또는 HALF_OPEN
- 포털 기타 화면은 정상 동작 (캐시 반환)

복구:
```bash
docker-compose -f docker-compose.dev.yml unpause dtrack-api
```

---

## 시나리오 12: Admin Panel — 스캔 모니터

> **Admin → Scans**

### 12-1. 스캔 큐 현황

**기대 결과**
- 전체 스캔 목록 (모든 팀의 스캔 포함)
- succeeded 상태 스캔 다수 표시
- 필터: running / queued / succeeded / failed

---

## 시나리오 13: Admin Panel — 디스크 사용량

> **Admin → Disk**

### 13-1. 디스크 대시보드

**기대 결과**
- 현재 디스크 사용량 표시 (총량 / 사용 / 가용)
- 워크스페이스 경로별 사용량 분류 (스캔 결과, 임시 파일 등)

---

## 시나리오 14: Admin Panel — 감사 로그

> **Admin → Audit**

### 14-1. 감사 로그 목록

**기대 결과**
- 최근 쓰기 작업 기록 표시
- 각 항목: 액터(이메일), 액션, 대상, 일시
- 시나리오 9-3 에서 수행한 비활성화 액션 확인 가능

---

### 14-2. 감사 로그 필터

1. Actor 필터에 `admin@trustedoss.dev` 입력

**기대 결과**
- 해당 계정의 액션만 필터링

---

## 시나리오 15: Admin Panel — 시스템 헬스

> **Admin → Health**

### 15-1. 헬스 대시보드

**기대 결과**
- 서비스별 상태: Backend (healthy), PostgreSQL (healthy), Redis (healthy), Celery Worker (healthy), DT (healthy)
- 응답 시간 또는 업타임 표시

---

## 체크리스트

시나리오별 결과를 아래에 기록하세요.

| # | 시나리오 | 결과 | 메모 |
|---|---------|------|------|
| 1-1 | Developer 로그인 | ⬜ 통과 / ⬜ 실패 | |
| 1-2 | 잘못된 비밀번호 | ⬜ 통과 / ⬜ 실패 | |
| 1-3 | 로그아웃 | ⬜ 통과 / ⬜ 실패 | |
| 1-4 | Super Admin 로그인 | ⬜ 통과 / ⬜ 실패 | |
| 2-1 | 프로젝트 목록 | ⬜ 통과 / ⬜ 실패 | |
| 2-2 | 프로젝트 생성 | ⏭️ 스킵 (미구현) | Phase 5 이후 |
| 3-1 | Overview — 데이터 있는 프로젝트 | ⬜ 통과 / ⬜ 실패 | |
| 3-2 | Overview — 빈 프로젝트 | ⬜ 통과 / ⬜ 실패 | |
| 4-1 | 컴포넌트 목록 | ⬜ 통과 / ⬜ 실패 | |
| 4-2 | 컴포넌트 검색 | ⬜ 통과 / ⬜ 실패 | |
| 4-3 | Severity 필터 | ⬜ 통과 / ⬜ 실패 | |
| 4-4 | 컴포넌트 드로어 | ⬜ 통과 / ⬜ 실패 | |
| 5-1 | 취약점 목록 | ⬜ 통과 / ⬜ 실패 | |
| 5-2 | Severity 필터 | ⬜ 통과 / ⬜ 실패 | |
| 5-3 | Status 필터 | ⬜ 통과 / ⬜ 실패 | |
| 5-4 | 취약점 상태 변경 | ⬜ 통과 / ⬜ 실패 | |
| 6-1 | 라이선스 분포 도넛 | ⬜ 통과 / ⬜ 실패 | |
| 6-2 | 라이선스 카테고리 필터 | ⬜ 통과 / ⬜ 실패 | |
| 6-3 | Conditional 라이선스 표시 | ⬜ 통과 / ⬜ 실패 | |
| 7-1 | 의무사항 목록 | ⬜ 통과 / ⬜ 실패 | |
| 7-2 | NOTICE 파일 다운로드 | ⬜ 통과 / ⬜ 실패 | |
| 8-1 | 스캔 큐 목록 | ⬜ 통과 / ⬜ 실패 | |
| 9-1 | Admin — 사용자 목록 | ⬜ 통과 / ⬜ 실패 | |
| 9-2 | Developer의 /admin 접근 차단 | ⬜ 통과 / ⬜ 실패 | |
| 9-3 | 사용자 활성화/비활성화 | ⬜ 통과 / ⬜ 실패 | |
| 10-1 | Admin — 팀 목록 | ⬜ 통과 / ⬜ 실패 | |
| 10-2 | 팀 멤버 목록 | ⬜ 통과 / ⬜ 실패 | |
| 11-1 | DT 모니터 — 정상 | ⬜ 통과 / ⬜ 실패 | |
| 12-1 | 스캔 모니터 | ⬜ 통과 / ⬜ 실패 | |
| 13-1 | 디스크 사용량 | ⬜ 통과 / ⬜ 실패 | |
| 14-1 | 감사 로그 목록 | ⬜ 통과 / ⬜ 실패 | |
| 14-2 | 감사 로그 필터 | ⬜ 통과 / ⬜ 실패 | |
| 15-1 | 시스템 헬스 | ⬜ 통과 / ⬜ 실패 | |

---

## 환경 복구 명령

```bash
# 전체 재시작
docker-compose -f docker-compose.dev.yml restart

# 로그 확인
docker-compose -f docker-compose.dev.yml logs -f backend
docker-compose -f docker-compose.dev.yml logs -f celery-worker

# DT 일시 중지 / 복구
docker-compose -f docker-compose.dev.yml pause dtrack-api
docker-compose -f docker-compose.dev.yml unpause dtrack-api
```

## 알려진 제한 사항

- **실제 스캔 실행**: Git 저장소 클론 후 cdxgen/ORT/Trivy 파이프라인은 worker 이미지에 언어 도구(mvn, cargo, go 등)가 설치되어야 실제 컴포넌트 탐지가 가능합니다. 현재 시드 데이터는 직접 DB 삽입 방식이므로 "Trigger Scan" 버튼을 눌러도 결과가 달라지지 않을 수 있습니다.
- **Obligations NOTICE 다운로드**: SPDX 라이선스 원문 API 연동 여부에 따라 내용이 다를 수 있습니다.
- **Admin acme-backend/acme-frontend**: admin 계정의 두 프로젝트는 컴포넌트 없이 스캔만 완료된 상태입니다. Overview의 빈 상태 확인에 활용하세요.
