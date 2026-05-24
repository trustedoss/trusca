# 외부 QA 인수인계 — 침묵 실패 감지 자산

외부 독립 QA가 출시 전 검증에서 만든 **고유 가치 자산**입니다. 내부 테스트(FE Playwright + BE pytest)에 없던
**"스캔/상태는 성공인데 결과가 비어 있는 침묵 실패"**를 외부 ground truth 대조로 잡습니다.

이 메커니즘이 출시 blocker 후보 2건을 검출했습니다:
- **BUG-008** — 소스 아카이브 스캔이 의존성을 미탐지(스캔은 `succeeded` 표시).
- **BUG-010 (High)** — 조건부 라이선스 컴포넌트의 Pending 승인 **자동 생성 누락**(가이드 `approvals.md` 명세 위반).

> 전체 맥락: 외부 QA의 `qa-report.md`·`bug-report.md`·`test-cases.md`·`HANDOVER.md` 참고.

> ## 🛠 메인테이너 후속 (2026-05-24)
> 이 자산들을 인수해 진단·조치를 완료했다. 요약:
> - **BUG-008**: cdxgen 런타임 직접 검증 결과 **Critical 오진**이었다. `scan-all-fixtures.js`가
>   `source_type=upload`+`archive_id`를 누락해 빈 워크스페이스를 스캔한 하네스 아티팩트(실제 UI는 정상,
>   node/python/maven 모두 BD ground truth 충족). 스크립트는 올바른 페이로드로 수정했다. 잔여 실버그는
>   "소스 없는 스캔의 조용한 성공"이며 `trigger_scan`에 `ScanSourceUnavailable`(422) 가드로 차단했다.
>   상세는 `reports/bug-report.md` BUG-008 메인테이너 부록 참조.
> - **BUG-010**: 확정 버그였다. 스캔 finalize에 conditional 라이선스 승인 자동생성을 구현했다.
> - **CI 통합**: 이 레포에는 이미 동등한 **golden-fixture drift gate**(`apps/backend/tests/e2e/golden/`,
>   `e2e-nightly.yml`)가 **올바른 `source_type=upload` 페이로드로** BD-비교(baseline 컴포넌트 수 diff)를
>   수행한다 — 즉 "스캔 succeeded인데 0개"를 이미 잡는다. `compare-bd.js`는 그 **외부 standalone 버전**으로
>   참고용으로 보존한다(중복 신규 배선 불필요). golden 티어가 외부 `bd-scan` 코퍼스 없이도 동작하도록
>   `node`/`python-pip` 픽스처를 in-repo로 커밋해 nightly에서 항상 BUG-008 클래스를 가드하도록 만들었다.

## 자산 (`qa-handover/scripts/`)
| 스크립트 | 검증 | 게이트 |
|----------|------|:---:|
| `compare-bd.js` ⭐ | 각 fixture 탐지 컴포넌트 수 vs Black Duck `summary.csv` → **false negative 검출(BUG-008)** | 미달 시 FAIL |
| `scan-all-fixtures.js` / `collect-scan-map.js` | 전 fixture 배치 스캔 → projectId 매핑(BD 비교 입력) | — |
| `admin-checks.js` | 관리자 read·복원 412 게이트·last-super-admin 보호 (비파괴 9종) | 실패 시 `exit 1` |
| `approval-checks.js` | 승인 If-Match/etag(412) 동시성. Pending 존재 시 자동생성(**BUG-010**) 확인 | 실패 시 `exit 1` |
| `p1-checks.js` / `p2-checks.js` | boundary·concurrency·state-transition·observability·입력검증·injection | 실패 시 `exit 1` (known-bug 격리) |
| `cleanup-qa-projects.js` | 테스트 데이터 정리(이름 가드) | — |

## 실행 (내부 환경 — 외부보다 간단)
내부 repo라 자체 `docker compose`로 기동하면 됩니다(외부는 staging/터널이 필요했음):
```bash
docker compose -f docker-compose.dev.yml up -d --build   # 또는 make dev-reset-rebuild
# health 후 seed
docker compose -f docker-compose.dev.yml exec -T backend python scripts/seed_demo.py

# 검증 (env: API_BASE, TEST_USER_EMAIL/PASSWORD=dev@demo, ADMIN_EMAIL/PASSWORD=admin@demo)
API_BASE=http://localhost:8000 node qa-handover/scripts/admin-checks.js
node qa-handover/scripts/approval-checks.js
node qa-handover/scripts/p1-checks.js
node qa-handover/scripts/p2-checks.js

# BD 정확성 (요구: Black Duck summary.csv ground truth)
BD_SUMMARY=path/to/summary.csv node qa-handover/scripts/scan-all-fixtures.js
FIXTURE_PROJECT_MAP=$(node qa-handover/scripts/collect-scan-map.js) \
  BD_SUMMARY=path/to/summary.csv node qa-handover/scripts/compare-bd.js
```

## 통합 권장 (영구 소유)
1. **`compare-bd`를 nightly CI에** → "스캔 succeeded인데 컴포넌트 0"을 매일 차단 (BUG-008 회귀 방어) ⭐
2. **`*-checks`를 pytest로 포팅**(httpx) 하거나 Node 그대로 → CI 게이트(실패=중단)
3. **known-bug 격리 규칙 유지** — 미수정 버그는 명시(그린 가장 금지), 수정 시 자동 "FIXED" 감지(`p2-checks.js`의 BUG-011 참고)

> 스크립트는 외부 환경(`process.env` 기반)을 가정합니다. 내부 통합 시 경로/계정 env만 맞추면 동작합니다.
