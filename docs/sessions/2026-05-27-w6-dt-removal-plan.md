# 핸드오프 — W6 DT 제거 + Trivy 교체 계획 v2 수립 (2026-05-27)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 W6 절. 이 문서는 그 세션 스냅샷이다.
> 직전 세션: [`2026-05-27-w4-complete-and-vuln-list-schema.md`](./2026-05-27-w4-complete-and-vuln-list-schema.md) — W4 전체 완료 후 인테이크 모드. 본 세션은 W5 DT 견고화 사고를 계기로 **DT를 통째로 제거**하는 W6 계획을 v2로 확정.
> 결정 메모리: [[project_dt_removal_decision]] (방향) + 본 핸드오프 (실행 계획).

---

## 이번 세션 결과

코드 변경 0건. **계획 수립 + 트래커 재작성**만 수행.

| 산출물 | 내용 |
|---|---|
| 트래커 §0.5 W6 절 v2 | 4 PR → **8 PR + 선택 1** 로 재분해. prereq(v2.3.1 tag/Release) + shadow 7d 게이트 + cdxgen 유지 명시 + 시간 추정 정정(3~5d → 코드 8d + 캘린더 7d). |
| 트래커 §0 대시보드 | W6 row 추가, 현재 상태 라인 갱신(2026-05-27, W4 완료 + W6 백로그). |
| 트래커 §6 의존성 그래프 | W6 흐름 13단계 추가(prereq→#45→#40→#41→shadow→#42→#43a~e→#44/#43f). |
| 본 핸드오프 | 다음 세션 자립 가능 조건([[feedback-handoff-next-session-must-be-self-sufficient]]) 만족하도록 풀어 씀. |

main HEAD 무변경(`faa0053`). `.env.example`·트래커는 working tree dirty 상태 — 트래커는 본 세션 결과, `.env.example`은 직전 세션 잔여.

---

## 결정 요약 (계획 v2)

### 방향 (재확인)
- DT 통째 제거. Trivy로 **CVE 매칭 단일 기능** 교체. 정책엔진/VEX/UI/승인은 이미 자체 구현.
- **cdxgen은 유지** — Trivy fs 통합 시 dependency graph(`dependsOn`)·`scope`/usage·evidence 손실, W4-D 자산 폐기 발생. 검토 후 명시 기각.
- 자동 재매칭(Celery beat)은 DT가 우리 코드 기준 못 하던 신규 기능으로 획득.

### v2.3.1 = DT 시대 동결 marker
- 태그: `v2.3.1` (SemVer 정식 — `v2.3.1-dt-final`은 pre-release 식별자 충돌로 기각)
- 의미 별칭: Docker `:v2.3-dt`, GitHub Release 제목 "Final Dependency-Track Release"
- 자산: 멀티아치 이미지 3종(BE/Worker/FE) + Helm 차트 0.2.1 + Release body(ADR-0001 링크)
- 통합: 운영 레인 **O1**(첫 이미지 게시) + **O3**(차트 ArtifactHub)을 prereq에 흡수
- **동결 브랜치 안 만듦** — 외부 사용자 0, tag→branch는 사후 무료. `SECURITY.md`에 backport 정책 한 줄로 갈음.

### 비가역 안전장치
- #41 머지 후 **shadow 운영 7일** + 일일 일치율 admin 대시보드 노출 → 평균 ≥95%면 #43a 진행 승인.
- 미달 시 Plan B(Trivy+OSV-Scanner 하이브리드) 또는 Plan C(DT 유지+Trivy 보조) 회의 트리거.
- #43a 머지 순간이 비가역 지점. 그 전엔 언제든 회귀 가능.

### 작업량 정정
| 단계 | 추정 |
|---|---|
| prereq(v2.3.1 tag/Release) | 0.5d |
| #45 ADR + CLAUDE.md/post-ga-roadmap 동기화 | 0.5d |
| #40 Trivy 어댑터 | 1d |
| #41 persist + 벤치 코호트 + security-reviewer | 1.5d |
| **shadow 7일** | 캘린더 7d (코드 0) |
| #42 자동 재매칭 beat | 1d |
| #43a BE 제거 + security-reviewer | 0.5d |
| #43b FE 제거 | 0.5d |
| #43c 사용자 문서 EN/KO | 1d |
| #43d 배포·Helm·upgrade.sh | 0.5d |
| #43e admin/health Trivy 패널 | 0.5d |
| #44 Trivy DB 라이프사이클 (필수 승격) | 0.5d |
| #43f stage rename (선택, v2.4.1) | — |
| **합계** | **코드 8d + 캘린더 7d ≈ 2~2.5주** |

---

## 다음 세션이 해야 할 일 (자립 가능 형식)

### 1) 사용자 확인 4건 — 첫 메시지에서 받기

| 질문 | 영향 | 디폴트 |
|---|---|---|
| (a) 벤치 코호트 GitHub repos 5~10개 제공 가능? | #41 종료조건(매칭 일치율 ≥95%) 측정 기반 | 없으면 OSS 인기 repo 자체 구성 |
| (b) air-gapped 사용자 존재? | #44 우선순위·#43c air-gapped 절 깊이 | 있다고 가정 → 필수 강도 유지 |
| (c) shadow 7일 캘린더 수용? | 일정 vs 안전 트레이드오프 | 안전 우선 → 7일 수용 |
| (d) Plan B/C 일치율 미달 시 어느 쪽 선호? | 미달 시 회의 시간 단축 | 미정 → 그때 결정 |

### 2) W6-prereq 착수 — 코드 변경 0, ADR + 태그만

**파일 신규**: `docs/decisions/0001-replace-dt-with-trivy.md`
- 양식: Context · Decision · Consequences · Alternatives Considered (cdxgen 유지 부록 포함)
- 본문 기반: 메모리 `~/.claude/projects/-Users-1112821-projects-trustedoss-portal/memory/project_dt_removal_decision.md`
- 메모리 본문에는 없는 추가 항목: (a) 8 PR 분해 요약 (b) shadow 7d 게이트 (c) cdxgen 유지 결정 (d) v2.3.1 동결 태그 설계

**파일 수정**: 
- `CLAUDE.md` — 규칙 4(DT Circuit Breaker) 삭제, "DT(Dependency-Track) 연동 전략" 절(L124-141) → "취약점 매칭(Trivy)" 절로 재작성, 환경변수 §·디렉토리 §·에이전트 § 11곳 DT 멘션 정리.
- `docs/post-ga-roadmap.md` — v2.1 회고("DT 종속 완화" → "완전 제거"), v2.4 W6 절 신설, 비범위 문구 정정. 멘션 14곳.

**검증**:
- `grep -nE "Dependency.?Track|DT |dt_" CLAUDE.md docs/post-ga-roadmap.md` = 0 (또는 보존 의도 본문만)
- `docusaurus build` green
- markdown lint clean

### 3) v2.3.1 tag + GitHub Release — prereq 후반

**선결 조건**: 
- working tree 깨끗(현재 dirty인 `.env.example`·트래커 변경 처리)
- main CI green 재확인
- #45 머지 완료

**수행**:
```bash
# main 최신화 후
git tag -a v2.3.1 -m "Final Dependency-Track-based release. See ADR-0001."
git push origin v2.3.1
# GitHub Actions release workflow가 멀티아치 이미지 + 차트 build·publish
# 또는 수동: gh release create v2.3.1 --title "v2.3.1 — Final Dependency-Track Release" --notes-file ...
```

**release body 초안** (별도 파일 `docs/release-bodies/v2.3.1.md`로 보관):
```
This is the last release where TrustedOSS Portal bundles Dependency-Track 
as the vulnerability matching engine. Starting v2.4.0, DT is replaced 
with Trivy (ADR-0001). 

For organizations that need to stay on DT for now:
- Pin Docker images at :v2.3.1 (or :v2.3-dt alias)
- Pin Helm chart at 0.2.x
- Security backports: see SECURITY.md "Backport policy"

Migration to v2.4.0: docs-site/docs/release-notes/v2.4.0.md
```

**자산 검증**:
- `docker pull ghcr.io/trustedoss/backend:v2.3.1` 성공 (멀티아치 manifest)
- `docker pull ghcr.io/trustedoss/backend:v2.3-dt` 별칭 성공
- Helm 차트 0.2.1 ArtifactHub 등록 (O3: `chart-release.yml` + `artifacthub-repo.yml` repositoryID 기입 + `docs/static/img/logo.png` 추가)
- `SECURITY.md` backport 정책 한 줄 추가

### 4) #40 착수 — prereq 통과 후

**출발 파일**:
- `apps/backend/integrations/trivy.py` (현재 `run_trivy_image`만 있음, `run_trivy_sbom` 추가)
- `apps/backend/integrations/trivy.py`의 SecretEncryptionError·timeout·subprocess 패턴 그대로 재사용
- 단위 cov ≥85% — adversarial parametrize 필수: 비정상 severity 값, 중첩 깊이, 인코딩 깨짐, oversized JSON, NULL byte, CRLF, javascript:/file: scheme

**API 설계**:
```python
def run_trivy_sbom(sbom_path: Path, output_dir: Path) -> TrivyResult:
    """trivy sbom --format json --output ... <sbom_path>"""
```

**테스트 위치**: `apps/backend/tests/unit/integrations/test_trivy_sbom.py` 신규.

### 5) 핸드오프 후속 작성

매 PR 머지 후 본 핸드오프와 트래커를 갱신. PR이 8개라 별도 세션 끝마다 트래커 한 줄 업데이트로 충분 — 마일스톤 종료 시 본 핸드오프 끝에 "결과" 절 추가 또는 후속 핸드오프 별도 작성.

---

## 핵심 참조 파일

- **계획 SoT**: `docs/post-ga-execution-tracker.md` §0.5 W6 (방금 갱신)
- **결정 ADR**: `docs/decisions/0001-replace-dt-with-trivy.md` (W6-#45에서 신규)
- **방향 메모리**: `[[project_dt_removal_decision]]`
- **출발 코드**: `apps/backend/integrations/trivy.py`·`apps/backend/tasks/scan_source.py:535` (`dt_findings` 스테이지)
- **인벤토리(이번 세션 실측)**:
  - BE: `integrations/dt/` 4파일 · `tasks/dt_*.py` 4파일 · `api/v1/admin/dt.py` · `core/config.py:434-465` 8 getter
  - FE: `features/admin/dt/` 3파일 · `router.tsx` · `AppShell.tsx`
  - 인프라: `docker-compose.yml`(코멘트 6곳) · `docker-compose.dt.yml` · `scripts/install.sh:400` · `scripts/ci/provision-dt.sh` · `charts/trustedoss/` 6파일
  - 문서: Docusaurus 15페이지 + `CLAUDE.md` 11곳 + `docs/post-ga-roadmap.md` 14곳
  - 테스트: 8개 파일

---

## 컨벤션 알림 (DT 제거 작업 특화)

- **stage 이름 유지**: `dt_upload`/`dt_findings`는 WS frame·E2E 하네스가 의존. #41은 이름 유지, #43f(선택·v2.4.1)에서 별도 PR.
- **데이터 손실 0**: DT 전용 테이블 없음. `vulnerability_findings`는 캐시였고 Trivy 재매칭으로 새로 채워짐. 마이그레이션 불필요.
- **audit_log 보존**: DT 액션 타입은 역사 사실로 보존. admin UI 필터는 deprecated 표기.
- **release-notes/v2.0.0.md 보존**: 역사 사실(v2.0 시점은 DT 포함).
- **security-reviewer 필수**: #40·#41(untrusted Trivy JSON 파싱)·#43a(권한 우회/잔여 endpoint).
- **adversarial parametrize**: Trivy JSON 파서는 [[feedback-adversarial-input-parametrize]] 규칙 강제.
- **EN/KO 동시**: 모든 문서/UI 변경. `i18n:check`·복수형 금지 [[feedback-frontend-i18n-no-plural-check]].
- **docker-compose V1**: `docker-compose` (하이픈) — `docker compose` 금지 [[feedback-docker-compose]].
