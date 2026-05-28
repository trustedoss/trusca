# TrustedOSS Portal — QA 자산 인수인계(이식) 가이드

> **목적**: 외부 독립 QA가 만든 검증 자산을 TrustedOSS 팀이 **자체 소유·운영**하도록 이식한다.
> "버그 발견"으로 끝나지 않고, **재현 가능한 안전망(특히 침묵 실패 감지)**을 팀의 repo·CI로 넘긴다.
> **함께 보기**: `qa-report.md`(종합·출시 가부) · `bug-report.md`(11건 상세) · `test-cases.md`(290 케이스 증빙)

---

## 1. 왜 이식하나
내부 테스트(FE Playwright 22 + 하네스 16 + BE pytest 145)는 충실하지만, **"스캔/상태는 성공인데 결과가 비어 있는 침묵 실패"**를 잡는 장치가 없었습니다. 그 갭에서 출시 blocker 2건이 나왔습니다:
- **BUG-008 (Critical)** — SCA 의존성 전 언어 미탐지 (Black Duck ground truth 대조로 검출)
- **BUG-010 (High)** — 조건부 라이선스 승인 자동 생성 누락

이 **감지 메커니즘**을 팀이 소유해야 회귀를 영구 방어할 수 있습니다.

## 2. 자산 인벤토리 (qa-automation repo)
| 자산 | 경로 | 역할 |
|------|------|------|
| E2E spec 10종 | `tests/trustedoss/specs/**/*.spec.ts` | auth·rbac·browser·sbom·fault-injection·rendering-xss·accessibility(+full)·locale·security-headers |
| 관리자 검증 | `scripts/admin-checks.js` | read·복원 412 게이트·last-super-admin 보호 (비파괴 9종) |
| 승인 동시성 | `scripts/approval-checks.js` | If-Match/etag(412) optimistic locking |
| P1/P2 검증 | `scripts/p1-checks.js`·`p2-checks.js` | boundary·concurrency·state-transition·observability·입력검증·injection |
| **BD 비교 파이프라인** ⭐ | `scripts/scan-all-fixtures.js` → `collect-scan-map.js` → `compare-bd.js` | Black Duck `summary.csv` vs 탐지 컴포넌트 수 → **false negative 자동 검출** |
| 부하 | `tests/trustedoss/load/read-load.js` | k6 read API p95 SLO |
| 정리 | `scripts/cleanup-qa-projects.js` | 테스트 데이터 정리(이름 가드) |
| CI | `.github/workflows/trustedoss-qa.yml`·`trustedoss-qa-ephemeral.yml` | 게이트·matrix·환경 전략 |
| 카탈로그 | `tests/trustedoss/specs/test-cases.md` | 290 케이스(3-pass) |

## 3. 이식 계층 — 무엇을 옮기나

| 계층 | 자산 | 이식 | 이유 |
|------|------|:---:|------|
| **고유 가치** ⭐ | BD 비교 · admin/approval/p1/p2-checks · exit-code 게이트 | **1순위** | 내부에 없던 침묵 실패 감지. blocker 2건을 잡은 메커니즘 |
| **갭** | fault-injection · accessibility(axe) · i18n · security-headers | 2순위 | 내부 커버리지 미흡 축 |
| **중복** | auth·rbac 기본·critical-flow | **이식 불필요** | 내부 Playwright/pytest가 이미 커버 |

## 4. 그들 구조로의 매핑

TrustedOSS는 이미 `apps/frontend/tests/_harness`(PortalPage 패턴)와 `apps/backend/tests/{unit,integration,e2e}`(pytest)를 보유 → **단순 복사가 아니라 기존 패턴에 통합**.

| 우리 자산 | → TrustedOSS 권장 위치 | 통합 방식 |
|-----------|----------------------|----------|
| `tests/trustedoss/specs/*.spec.ts` | `apps/frontend/tests/e2e/` | `_harness` PortalPage 셀렉터로 리팩토링 (getByRole/testid는 대부분 그대로) |
| `admin/approval/p1/p2-checks.js` | `apps/backend/tests/integration/` | **pytest로 포팅** (httpx) 또는 `scripts/`에 Node 그대로 |
| `scan-all/collect/compare-bd.js` | `apps/backend/scripts/` + CI job | BD `summary.csv`를 repo에 두고 nightly 비교 |
| `read-load.js` (k6) | `tests/load/` | 그대로 |
| CI workflow | `.github/workflows/` | **내부라 더 쉬움** — 자체 `docker compose`로 기동(아래 §5) |

## 5. CI 통합 — 내부 repo의 이점
외부(우리)는 staging이 없어 ephemeral을 만들었지만, **팀은 repo 내부**라 훨씬 간단합니다:
- 우리 `trustedoss-qa-ephemeral.yml`이 그대로 **청사진**: `docker compose -f docker-compose.dev.yml up --build` → health → `seed_demo` → 검증 → `down -v`
- 내부라 cross-repo checkout/public 빌드 이슈 없음. `make dev-reset-rebuild` 흐름 재사용
- **핵심: 검증 스크립트는 실패 시 `exit 1`** → 침묵 실패(BUG-008류)가 재발하면 CI가 멈춤. known-bug는 명시적 격리(그린 가장 금지)

### BD 비교를 CI에 (BUG-008 영구 방어)
```
nightly:
  fixture matrix(언어별) → 각 zip 스캔 → 컴포넌트 수
  vs ground-truth/summary.csv(Black Duck) → 미달 시 FAIL
```
이게 "스캔은 succeeded인데 컴포넌트 0"을 매일 잡습니다.

## 6. 환경·시크릿
- 내부 CI는 자체 `docker compose` → 외부 URL/PAT 불필요
- seed 비번: `DEMO_SUPER_ADMIN_PASSWORD`(≥12자) env로 고정 → 검증 계정 일관
- 격리 원칙: 매 run `down -v`로 폐기 → 데이터 오염·leftover 0 (전용/격리 환경, 프로덕션 쓰기 금지)

## 7. 유지보수 (회귀 운영)
- **regression 루프**: prod 에러/문의 → 재현 → 영구 spec → CI 게이트
- **flaky 0 목표**: 원인 분류(셀렉터/타이밍/데이터/실버그), 임의 재시도로 덮지 않기
- **known-bug 격리**: 미수정 버그는 quarantine + 이슈 등록, 수정 시 자동 "FIXED" 감지(예: `p2-checks.js`의 BUG-011)

---

## 8. 권장 이식 순서
1. **BD 비교 파이프라인** → nightly CI (BUG-008 영구 방어) ⭐
2. **api-checks(admin/approval/p1/p2)** → pytest/scripts + 게이트
3. **갭 E2E**(fault/a11y/i18n/security-headers) → `_harness` 통합
4. `test-cases.md`를 회귀 카탈로그로 채택 → 290 케이스를 내부 스위트에 매핑
5. blocker 2건(BUG-008/010) 수정 후 → 해당 검증이 자동 "FIXED" 되는지 확인

> 핵심 자산(고유 가치)은 별도 PR로 trustedoss-portal에 직접 제공합니다 — 이 가이드와 함께 보세요.
