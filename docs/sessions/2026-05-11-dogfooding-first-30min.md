# 새 세션 지시문 — Dogfooding "First 30 Minutes"

작성일: 2026-05-11
대상 모델: Claude Opus 4.7 (1M context)
선행 PR: #62, #63, #66, #68, #70, #72 (3 cycles + 3 strengthening rounds 문서 정합성 작업 완료)

---

## 왜 이 작업인가

지난 6개 PR로 문서의 **misinformation은 제거**되었다. 잘못된 라벨, 가짜 엔드포인트, 깨진 링크는 거의 0이고, on-call runbook · 글로서리 · 검색 · KO 라벨까지 갖춰졌다.

하지만 **새 사용자가 처음 install.sh 부터 1열로 따라갔을 때 wall-clock 30분 안에 첫 가치를 본다** 는 보장은 아직 없다. 우리는 코드 grep + persona 시뮬레이션만 했지 실제 깨끗한 머신에서 walk-through 한 적이 없다.

기본 기능에서 막히는 첫 인상이 가장 치명적이다 — product roadmap 항목보다 이게 더 시급하다.

---

## 목표 (Definition of Done)

다음 3개 golden-path task가 각각 **fresh 환경에서 명시된 wall-clock budget 안에 막힘 없이 완료**된다는 것을 증명한다. 각 task에 대해:

1. **wall-clock 측정** (분:초 단위)
2. **friction log** (어디서 멈췄나, 왜, 얼마나)
3. **PR-단위 수정 사항** (docs 정정 / UI 보강 / install 자동화)

### Task α — First Admin (예산: 30분)

**시나리오**: 회사가 새 droplet에 portal을 깔라고 함. Linux 만져봤지만 portal 처음.

```
- VM 0분 → install.sh 실행
- 5분 → wizard 완료, https://<host> 첫 로그인
- 10분 → /admin/teams 새 team 생성
- 15분 → /admin/users 가서 자기 super_admin 계정 확인 + 동료 1명 self-register용 안내 메시지 작성
- 20분 → /admin/dt 가서 DT 상태 CLOSED 확인 (또는 OPEN→CLOSED 전환 대기)
- 30분 → /admin/backup 가서 manual backup 1회 실행 + 성공 확인
```

성공 기준: 위 6개 milestone 모두 도달 + 외부 검색(StackOverflow 등) 0회 + Slack DM 0회.

### Task β — First Developer (예산: 30분)

**시나리오**: 회사가 portal 깔아놨다. 신규 입사 dev. 첫 프로젝트 등록 + 첫 스캔.

```
- 0분 → /register
- 3분 → admin이 team 추가 후 다시 로그인, /projects 진입
- 5분 → /projects/new — 작은 public repo (예: github.com/expressjs/express) 등록
- 10분 → 프로젝트 행 Scan 클릭 → progress drawer 끝까지 관찰
- 20분 → 결과 보기: /projects/:id → Components / Vulnerabilities / Licenses / SBOM 탭 각 1회
- 25분 → CVE 1개 골라 drawer 열고 VEX state Mark not affected 시도 (justification 입력)
- 30분 → SBOM 다운로드 (CycloneDX JSON 1개)
```

성공 기준: 위 7개 milestone + 막힘 없이 + 결과 화면이 docs 스크린샷과 일치.

### Task γ — First CI integration (예산: 30분)

**시나리오**: dev가 portal API key 받아서 GitHub Actions에 통합.

```
- 0분 → /integrations 가서 API key 발급 (scope=project)
- 5분 → ci-integration/github-actions.md 따라 별도 test repo (.github/workflows/sca.yml) 추가
- 15분 → PR push → workflow 실행 → 결과 확인
- 25분 → portal /scans 큐에서 해당 스캔 row 확인
- 30분 → PR 코멘트 게시 여부 확인 (TRUSTEDOSS_GITHUB_TOKEN 환경 설정 + 재시도)
```

성공 기준: workflow exit code 적절(0 또는 의도된 1) + portal에 스캔 record + (가능하면) PR comment.

---

## 작업 방법 (필수 준수)

### 1. 진짜 fresh 환경 사용

옵션 a — DigitalOcean droplet (권장): Ubuntu 22.04 LTS, 8GB shared CPU droplet 1개 spin-up. `oss.<your-tld>` 도메인 A record. 비용은 약 시간당 $0.06.

옵션 b — 로컬 Linux VM: VirtualBox / UTM 위 fresh Ubuntu 22.04. 도메인은 `/etc/hosts` 로 가짜.

**금지**: 현재 dev 스택 재사용, 기존 .env 재사용. 깨끗한 first-install 시뮬레이션이 핵심.

### 2. wall-clock 기록

각 milestone 도달 시점을 직접 기록 (`date +%s` 차이). 각 막힘 구간도 분 단위로 기록.

### 3. 막힘 카테고리

각 friction을 다음 중 하나로 분류:
- **D (docs)**: 문서 자체 결함 — 잘못된 명령, 빠진 단계, 모호한 안내
- **U (UI)**: UI 자체 결함 — 빈 화면, 잘못된 상태, 발견 불가능한 버튼
- **S (system)**: 시스템 버그 — exception, 빈 응답, 잘못된 로직
- **P (prerequisite)**: 외부 의존 미설치 / 설정 — DNS, PAT, 방화벽
- **C (cognitive)**: 도메인 지식 부재로 인한 막힘 — 용어, 워크플로우 이해

### 4. 막힘 발견 시

상세 기록 (재현 명령어 + 화면 / 로그) 후 **task 계속 진행**. 한 막힘에서 1시간 이상 머무르지 말 것 — 우회 방법 (sudo, raw SQL, 다른 브라우저 등)으로 다음 단계로 가서 측정 계속.

### 5. 도구

- 시계: `date +"%T"` 명령 / iPhone stopwatch / iTerm timestamp
- 화면 캡처: 막힘 지점은 스크린샷 필수
- 로그: `docker-compose logs --tail=200 <svc>` 즉시 캡처
- DNS: `dig` / `nslookup`
- TLS: `curl -v` / `openssl s_client`

---

## 산출물 (PR 형식)

### PR-1 — Friction log + 우선순위

새 파일: `docs/sessions/2026-05-11-dogfooding-results.md`

```markdown
# Dogfooding Results — 2026-05-11

## Task α (First Admin) — actual wall-clock: NN분
- 0:00 ... → install.sh 시작
- 4:35 ... → wizard 완료 ✓
- 6:12 ... → 첫 로그인 막힘 (D — wizard가 admin password를 stdout으로 안 출력, .env grep 필요) — 4분 손실
- ...

### Friction by category
| # | 카테고리 | 위치 | 설명 | 손실 시간 | 우회 방법 | 권장 fix |
|---|---------|------|------|---------|----------|---------|

## Task β (First Developer) — wall-clock: NN분
...

## Task γ (First CI) — wall-clock: NN분
...

## Priority backlog
| 우선순위 | 작업 | 카테고리 | 예상 영향 |
|---------|------|---------|----------|
| P0 | ... | D / U / S | task α 5분 단축 |
```

### PR-2 ~ N — 각 fix

friction log의 P0/P1 항목별로 분리된 PR. docs fix는 1개 PR로 묶어도 OK, 코드 fix는 영역별 분리.

각 PR은:
- Before/after wall-clock 추정 명시
- 회귀 방지 테스트 (가능한 경우)
- EN + KO 미러 정합

---

## 환경 변수 / 사전 준비

```bash
# DigitalOcean droplet 예시
DROPLET_REGION=sgp1   # 또는 가까운 region
DROPLET_SIZE=s-2vcpu-4gb   # 최소 (ORT는 6GB peak이라 좀 빡빡함; 8GB 권장)
DROPLET_IMAGE=ubuntu-22-04-x64
SSH_KEY_FINGERPRINT=...

# 도메인
DOMAIN=oss.<your-tld>
# 사전: $DOMAIN A record → droplet public IP, TTL 5분 권장
```

dotfile / shell history 없는 깨끗한 user 계정 사용. `sudo` 1회 사용 OK, 2회 이상 사용 시 friction 기록.

---

## 진행 원칙

1. **외부 검색 금지** — 막히면 docs / git log / 코드 grep 만 사용. StackOverflow, ChatGPT, 동료 DM은 friction 카운트.
2. **한 task 안에서 우회 OK** — 한 milestone에서 1시간 막히면 다음으로 가서 측정 계속. 우회 자체가 friction 데이터.
3. **persona 톤 유지** — admin task 할 때는 "Linux 만져본 운영자" 톤으로, FastAPI 코드 깊이 보지 말 것. dev task 때는 "신규 입사 dev"가 backend internal 모르는 톤.
4. **wall-clock이 핵심 지표** — 어떤 friction은 시간 손실이 적고, 어떤 건 치명적. 분 단위 측정으로만 우선순위 정확.

---

## 작업 분할 (병렬 가능)

- **3 task 동시 진행 가능**: droplet 3개 띄워서 task α/β/γ 각 별도 머신에 (task β는 admin 1회 도움 받으면 단독 가능).
- **persona 톤 유지를 위해 다른 사람 / 다른 세션이 각 task 담당**도 가능.

또는 단일 머신 sequential:
- droplet 1개에서 task α (admin) 완료
- 동일 droplet에서 dev account 만들어 task β
- 동일 환경에서 외부 GitHub repo 만들어 task γ

순차 단점: 한 task의 setup 결과가 다음 task에 영향 → 진짜 first impression 측정 어려움. droplet 비용 미미하니 병렬 권장.

---

## 측정 결과 형식 권장

```markdown
## Task α timeline

- 00:00 — `bash scripts/install.sh` 시작
- 00:42 — wizard 첫 프롬프트 (DOMAIN)
- 01:15 — TLS_EMAIL 프롬프트, 잠깐 머뭇 (C — admin@<domain> 형식 안내 부족, 30초 손실)
- 02:30 — wizard `up -d` 시작
- 04:18 — backend healthy
- 04:55 — wizard 완료, admin password를 stdout 어디서 봐야 할지 막힘 (D — installation/docker-compose.md L60 부근, admin password 출력 위치 안내 부족)
- 09:30 — `.env` grep으로 admin password 발견 (~ 4.5분 손실, 우회 = D)
- 09:45 — 첫 로그인 성공 ✓
- ...

## Task α total: 27:14
- Successful milestones: 6/6
- Friction-induced loss: 7.5분
- Net flow time: 19.7분 (60% of budget)
```

---

## 작업 후 메타 결과

3개 task 모두 마친 후:
- 각 task budget 안에 들어왔는가? (목표: 3/3)
- 가장 큰 friction 카테고리는? (목표: D > P > C > U > S 순서. S(system) 발견 시 P0 product fix)
- 30분 안에 admin/dev/SRE가 "value 봤다"고 느끼는가?

위 결과를 가지고 다음 product 백로그 우선순위 설정.

---

## 핸드오프

이 세션 종료 시 `docs/sessions/2026-05-11-dogfooding-results.md` 가 작성되어 있어야 하고, PR-1 (friction log) 머지 후 PR-2 ~ N (개별 fix) 들이 backlog에 등재되어 있어야 한다.
