---
id: testing-guide
title: 테스트 가이드
description: pytest 레이아웃, Playwright PortalPage 하네스, 적대적 입력 parametrize, 80% coverage 머지 게이트.
sidebar_label: 테스트 가이드
sidebar_position: 3
---

# 테스트 가이드

테스트는 1급 시민입니다. PR 머지 게이트는 **변경된 코드의 line coverage ≥ 80 %**이며, Playwright E2E 스위트는 PR에서 실행되지 않습니다(아래 [커버리지 게이트](#coverage-gate) 참고). 이 페이지는 레이아웃, 하네스 패턴, 정적 분석으로는 못 잡는 버그를 잡는 적대적 입력 규칙을 다룹니다.

:::note 대상 독자
모든 컨트리뷰터. `apps/backend/`나 `apps/frontend/`를 건드리는 모든 PR에 적용.
:::

## Backend — pytest

테스트는 `apps/backend/tests/` 아래에 있으며 세 계층으로 분리됩니다.

```
apps/backend/tests/
├── unit/             # 순수 함수 테스트, DB·네트워크 없음
├── integration/      # FastAPI TestClient + Postgres (testcontainers)
└── e2e/              # 백엔드 단독 블랙박스 흐름; Playwright 스위트와 다름
```

각 계층의 `conftest.py`는 적절한 fixture를 노출합니다. 최상위 `conftest.py`는 계층 간 공용 헬퍼(factory, time freezing)를 제공합니다.

### 집중된 셋만 실행

```bash
cd apps/backend

# 전체 스위트
pytest -q

# 단일 계층
pytest -q tests/unit

# 키워드로
pytest -q -k "api_key and revoke"

# 단일 테스트 + print
pytest -s tests/integration/test_api_key_endpoints.py::test_revoke_immediate
```

### Coverage

```bash
pytest --cov=. --cov-report=term-missing --cov-report=xml
```

**변경된 라인** 기준 line coverage ≥ 80 %를 목표로 합니다. CI는 두 스위트를 별도 잡(`test (backend-unit)`, `test (backend-integration)`)으로 돌리고, `coverage-gate (backend)`가 두 잡의 커버리지 데이터를 합쳐 두 가지를 판정합니다. 전체 트리는 `fail_under = 80`으로, 브랜치가 바꾼 라인은 `diff-cover`로 같은 80 % 기준을 적용합니다. 둘 중 하나라도 미달이면 잡이 실패합니다.

### 레이아웃 가이드

- **Unit:** 테스트 대상 함수는 DB, HTTP, Celery를 사용하지 않습니다. 경계에서 mock합니다.
- **Integration:** 라우트를 FastAPI TestClient로 종단 간 실행합니다. 실제 PostgreSQL은 `pytest-testcontainers`로 띄우고, **SQLAlchemy는 mock하지 않습니다.**
- **E2E (backend):** worker가 별도 fixture로 실제 동작하는 상태에서 HTTPX로 API를 블랙박스로 구동합니다. Playwright가 주된 E2E이므로 절제해서 사용합니다.

## Frontend — `PortalPage` 하네스 기반 Playwright

`apps/frontend/tests/_harness/PortalPage.ts`가 도메인 언어로 된 Page Object를 정의합니다. **테스트 코드는 `page.click(...)`을 직접 호출하지 않습니다.**

### 왜 하네스인가

도메인 동사로 표현된 테스트는 UI 변화에도 살아남습니다. 동일 시나리오를 비교해 보세요.

```ts
// ❌ 부서지기 쉬움 — 모달 마크업이 바뀌면 깨짐
await page.click("button:has-text('New API key')");
await page.fill("input[name='label']", "ci-runner");
await page.click("button:has-text('Create')");

// ✅ 안정적 — 제품 언어로 표현
await portal.createApiKey({ label: "ci-runner", scope: "team", expiryDays: 90 });
```

### 하네스에 동사 추가

새 화면이나 흐름을 추가할 때는 **먼저 `PortalPage`에 동사를 추가**한 다음 시나리오를 작성하세요.

```ts
// apps/frontend/tests/_harness/PortalPage.ts
async createApiKey(opts: { label: string; scope: ApiKeyScope; expiryDays: number }) {
  await this.page.getByRole("button", { name: "New API key" }).click();
  await this.page.getByLabel("Label").fill(opts.label);
  await this.page.getByLabel("Scope").selectOption(opts.scope);
  await this.page.getByLabel("Expiry").selectOption(`${opts.expiryDays}d`);
  await this.page.getByRole("button", { name: "Create" }).click();
  return this.captureKeyFromOneTimeRevealModal();
}
```

하네스에는 현재 ~17개 동사가 있습니다. `PortalPage.ts`만 읽고도 제품의 사용자 여정을 다시 풀어낼 수 있어야 합니다.

### 실행

```bash
cd apps/frontend
npm run test:e2e          # 모든 시나리오
npm run test:e2e -- --grep "api keys"   # 필터링
npm run test:e2e:headed   # 브라우저 표시, 디버깅 시 유용
```

E2E 실행 전 dev 스택이 떠 있어야 합니다(`docker-compose -f docker-compose.dev.yml up -d`).

## 적대적 입력 — parametrize 필수

**신뢰할 수 없는 입력**을 파싱하는 코드는 적대적 케이스의 parametrize 매트릭스로 반드시 검증해야 합니다. 포털은 이미 한 번 당했습니다 — chore PR #7의 재귀적 `normalize_spdx_id`는 88 % 커버리지에서도 separator-only 토큰으로 DoS를 허용했습니다.

### 적용 대상

- 레지스트리 메타데이터 파서(`packages/`, `npm`, `pypi`, `cargo`, `go.mod`).
- Webhook URL·페이로드 파서(GitHub, GitLab, Slack, Teams).
- SPDX·CycloneDX 표현식 정규화기.
- OAuth `state`·`code` 파서.
- 사용자 콘텐츠가 regex, 경로, 셸로 보간되는 모든 곳.

### 매트릭스

각 표면에 대해 **최소** 다음 적대적 입력으로 parametrize.

| 분류 | 예시 |
|---|---|
| Separator-only 토큰 | `"AND"`, `"OR"`, `"WITH"`, `"OR OR OR"`, `" "` |
| Scheme 남용 | `"javascript:alert(1)"`, `"file:///etc/passwd"`, `"data:text/html,..."` |
| 과대 크기 | 1 MiB 문자열, 65 535 nested parens, 10 000자 URL |
| 제어 바이트 | CRLF (`"\r\n"`), null byte (`"\x00"`), BOM (`"﻿"`) |
| Unicode 트릭 | RTL override (`"‮"`), homoglyph(`"аpple"` 키릴), zero-width(`"​"`) |
| 빈 / 공백 | `""`, `"   "`, `"\t\n"` |

`pytest.mark.parametrize`를 사용하고 각 케이스에 라벨을 붙여 실패 메시지가 진단 정보가 되게 하세요.

```python
@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param("MIT AND Apache-2.0", ["MIT", "Apache-2.0"], id="happy-path"),
        pytest.param("AND", [], id="separator-only-token"),
        pytest.param("javascript:alert(1)", [], id="scheme-abuse"),
        pytest.param("(" * 10_000 + "MIT" + ")" * 10_000, ["MIT"], id="deep-nesting"),
        pytest.param("MIT\r\nApache-2.0", ["MIT", "Apache-2.0"], id="crlf-injection"),
        pytest.param("MIT\x00Apache-2.0", ["MIT"], id="null-byte"),
    ],
)
def test_normalize_spdx_id(raw: str, expected: list[str]) -> None:
    assert normalize_spdx_id(raw) == expected
```

적대적 parametrize는 fuzzing의 대체가 아니라 보완입니다. 이미 알려진 케이스를 회귀 차단하기 위해 parametrize에 의존합니다.

## 보강 규칙 — 2026-06 검증 캠페인이 가르쳐 준 것

외부 검증팀이 가이드에서 도출한 1,360케이스를 라이브 포털에 전수 실행해, 우리 unit / functional / e2e가 전부 green인 상태에서 고유 결함 70건을 찾아냈습니다. 사후 분석으로 구조적으로 확인되지 않던 범위를 추적했고, 아래 각 규칙이 그 범위를 하나씩 닫으며 그 근거가 된 결함 클래스를 함께 적습니다. 이 규칙들은 신규 PR에 구속력이 있습니다(CLAUDE.md §2와 동일). 6번은 나중에 2026-09 도입 준비도 점검에서 추가됐습니다.

### 1. 보안 단언은 권한 × 상태 매트릭스로

"타팀 → 404" 테스트도 있었고 "종결 → 409" 테스트도 있었지만 그 **교차점**은 없었고, 실제 누출이 정확히 거기 있었습니다(비소속 계정이 타 팀의 *종결된* 스캔을 찔러 409를 받아 존재가 확인됨). 권한 거부(404 존재 은닉 / 403)는 상태에서 파생된 409보다 항상 먼저여야 합니다. 새 409 표면은 `apps/backend/tests/integration/test_existence_hide_state_matrix.py`에 케이스를 추가합니다.

이 규칙에는 라우트 수준의 나머지 절반이 있습니다. `tests/contracts/permission-matrix.json`이 라우트마다 어떤 게이트를 다는지 선언하고, `apps/backend/tests/unit/test_permission_baseline.py`가 이를 양방향으로 단언합니다. 행이 없는 라우트는 실패하고(아무도 분류하지 않은 표면이 나갔다는 뜻), 라우트가 없는 행도 실패합니다(매트릭스가 낡아 오라클 구실을 못 한다는 뜻). 라우트를 추가하면 행도 추가합니다. 게이트를 바꾸면 픽스처를 바꿔야 하고, 그래야 변경이 API 모듈 여기저기에 흩어지지 않고 검토자 앞에 놓입니다. 역할마다 허용과 거부를 모두 단언합니다. 거부만 단언하면 게이트가 넓어져도 green이고, 모르는 역할은 어디서나 거부되므로 역할이 빠진 상태가 안전한 것처럼 읽힙니다.

### 2. 같은 어휘가 두 곳에 있으면 정합 테스트가 의무

닫힌 어휘가 두 곳에 존재하면 — DB enum과 디스패처 카탈로그, emitter와 공표 목록, 백엔드 enum과 프런트엔드 미러 상수 — 모듈별 테스트는 green인 채로 둘이 어긋납니다(알림 kind 드리프트는 승인 트리거가 연결되기 전까지 잠복했습니다). 양쪽을 import해 집합 동등성을 단언합니다: `apps/backend/tests/unit/test_catalog_contracts.py`가 그 패턴입니다.

### 3. persist 경계 테스트는 실물 도구 출력 채록 픽스처로

손으로 만든 최소 픽스처는 지나치게 깨끗합니다. 실제 컨테이너 이미지는 패키지당 CVE 여러 개가 *정상*이고, 컨테이너 스캔 persist 버그가 정확히 그 밀도에 살았습니다 — 패키지당 CVE 1개짜리 픽스처로는 도달할 수 없었습니다. 실물 도구 출력을 채록하고(`tests/fixtures/trivy/`), 기대 수치는 픽스처에서 도출해 재채록에도 단언이 깨지지 않게 합니다.

### 4. 문서가 오라클이다

70건 중 34건이 가이드-구현 불일치였습니다 — 코드는 코드대로 일관되게 틀려서, 코드에서 파생한 테스트로는 구조적으로 보이지 않습니다. 문서가 약속한 것(상태코드, CLI 명령, 설정 키)마다 docs-uat 단언 또는 가드 테스트를 기능 DoD에 포함합니다.

설정 키에는 이미 가드가 있습니다. `apps/backend/tests/unit/test_config_key_contract.py`가 코드가 읽는 키와 `.env.example`, 레퍼런스 문서를 한 목록으로 묶습니다. 새 키는 템플릿이나 문서에 항목이 있어야 하고, 더 이상 읽히지 않는 키는 항목을 빼야 합니다. 그래야 운영자에게 아무 일도 하지 않는 설정을 내놓지 않습니다. 검사가 볼 수 없는 두 가지 형태는 그 파일에 선언합니다. 실행 중에 이름을 조합하는 키와, 읽지 않고 하위 프로세스로 전달만 하는 키입니다.

### 5. 라이프사이클 시퀀스는 별도 테스트 카테고리

단일 동작 테스트가 전부 통과하는 동안 폐기 → 재등록은 영구 409였습니다(유니크 제약이 revoked 행까지 셌습니다). 생성 → 폐기 → 재생성, 보관 → 복원 → 사용: 동사 하나하나가 아니라 시퀀스를 테스트합니다.

### 6. 쓰기를 하는 태스크는 그것을 실행하는 테스트를 가진다

유지보수 태스크 두 개가 아무것도 쓰지 않으면서 성공을 보고했습니다. 행을 메모리에서 고치고 고친 건수를 요약에 담았지만 `session.commit()`을 부르지 않아, `sync_session_scope`가 닫히면서 작업이 그대로 버려졌습니다(이 헬퍼는 자동 커밋하지 않고 독스트링에 그렇게 적혀 있습니다). 두 태스크 모두 그것을 실행하는 테스트가 없었고, 태스크를 import만 하거나 태스크가 부르는 헬퍼만 단언하는 테스트로는 이 상태를 볼 수 없습니다. 쓰기를 하는 태스크는 그것을 실행하고 데이터베이스에서 행을 다시 읽는 테스트를 하나 이상 가집니다.

### 회귀망은 의도적으로 두 겹

`tests/verify-specs/`는 검증팀의 결정적 스펙 모듈을 저장소에 포함한 것이고(동봉 `PROVENANCE.md` 참조) nightly(`verify-specs-nightly.yml`)가 신선하게 시드된 스택에 전수 실행합니다. 이 nightly는 우리 내부 회귀망입니다 — 검증팀의 독립 Tier-3 재검증을 대체하지 않으며, 후자의 가치는 오라클이 우리 것이 아니라는 데 있습니다.

## 디자인 게이트 — 색과 픽셀

리뷰 코멘트로만 지켜지던 규칙 두 가지를 기계가 강제합니다.

**디자인 토큰.** `npm run token:lint`는 `apps/frontend/src/` 아래의 raw hex와 Tailwind 팔레트 클래스(`bg-amber-50`, `text-emerald-700`)에서 실패합니다. 토큰을 쓰세요 — shadcn 시맨틱 계열, finding 심각도는 `risk-*`, 개체·동작 상태는 `status-*` 입니다. [디자인 시스템 레퍼런스](../reference/design-system.md)를 참고하세요.

기존 부채는 `scripts/token-lint-baseline.json`에 파일별로 동결돼 있고, 게이트는 래칫입니다. 새 위반도, 늘어난 파일도 실패하며, **줄어든 파일도 실패**시켜 낮아진 baseline을 커밋하게 합니다. 마지막 방향이 핵심입니다 — 상환했는데 기록하지 않은 예산은 다른 사람이 다시 쓸 수 있는 예산입니다.

```bash
npm run token:lint          # 검사
npm run token:lint:update   # 부채를 상환한 뒤, 결과를 커밋
```

**시각 baseline.** `ui-gates.yml`은 프런트엔드를 건드리는 모든 PR에서 돌며 픽셀 드리프트를 막습니다. 어떤 화면을 지킬지는 `tests/visual/coverage-manifest.ts`가 정합니다 — 라우터가 렌더하는 모든 화면이 represented(baseline 보유) 이거나 exempt(사유 명시)로 분류되고, 누락되면 `visualCoverage.test.ts`가 실패합니다. 화면 집합은 라우트마다 하나가 아니라 레이아웃 템플릿마다 하나로 의도적으로 제한합니다 — baseline 한 장은 유지보수 부채이고, 흔들리는 diff가 쌓이면 리뷰어는 빨간 표시를 넘겨보게 됩니다.

의도한 UI 변경 후에는 로컬이 아니라 CI에서 baseline을 다시 촬영하세요. macOS 폰트 힌팅은 리눅스 러너와 텍스트 밀집 화면에서 5~20 % 차이가 납니다.

```bash
gh workflow run ui-gates.yml --ref <branch> -f update_baselines=true
# 이후 `visual-baselines` 아티팩트를 내려받아 PNG 를 커밋합니다
```

skip 라벨은 의도적으로 두지 않습니다. 라벨이 필요할 만큼 변동이 큰 것(상대 시각 표시, dev 서버 전용 devtools 런처)은 spec에서 마스킹하거나 숨깁니다.

**접근성.** 같은 워크플로가 같은 화면들에 axe-core(WCAG 2.1 A/AA)를 돌립니다. 실제 브라우저가 필요한 이유는 `color-contrast` 입니다 — axe는 jsdom에서 이 규칙을 평가하지 못하며, 그래서 기존 `badgeContrast.test.tsx`는 컴포넌트 하나의 대비를 손으로 계산합니다.

토큰 린트와 같은 래칫이고 0을 요구하지 않습니다. 이 앱은 한 번도 스캔된 적이 없었고, 충족 불가능한 게이트는 결국 꺼집니다. 위반은 `tests/a11y/a11y-baseline.json`에 화면·규칙별로 동결되며 수치는 줄어들기만 합니다. 매 실행이 관측 결과(건수와 위반 선택자)를 `a11y-observed` 아티팩트로 게시하므로, 실패했을 때 어디를 봐야 할지 다시 파헤칠 필요가 없습니다.

갱신 방법은 시각 baseline과 같습니다 — `update_baselines` dispatch가 둘 다 처리합니다. 픽셀과 규칙 건수는 같은 이유로 함께 움직이기 때문입니다.

두 게이트는 화면 목록 하나(`tests/_harness/screenIds.ts`)를 공유하므로 커버 범위에 대해 서로 어긋날 수 없습니다.

## Coverage 게이트 상세 {#coverage-gate}

머지 게이트는 `.github/workflows/ci.yml`에서 강제됩니다.

- **Unit + integration 합산:** 전체 line coverage ≥ 80 %, 그리고 **PR이 바꾼 라인** 기준 ≥ 80 %. 두 스위트는 별도 잡으로 돌고 `coverage-gate (backend)`가 합친 뒤에 판정합니다. 한쪽 잡만 놓고 보는 숫자는 뜻이 없기 때문입니다.
- **E2E (Playwright):** PR 게이트에 포함되지 않습니다. 이 스위트는 나이틀리 일정과 수동 `workflow_dispatch`에서만 실행되므로 PR을 올려도 돌지 않습니다. 핵심 시나리오는 `apps/frontend/tests/e2e/_core/`에 있고 해당 기능과 함께 추가합니다. 사용자에게 보이는 흐름을 바꿨다면 직접 실행하거나 메인테이너에게 워크플로 실행을 요청하세요.
- **디자인 토큰:** 위의 `token:lint` 래칫.
- **시각·접근성:** 위의 `ui-gates.yml`.

합쳐진 `coverage.xml`은 실행에 `backend-coverage` 아티팩트로 첨부되고, 빠진 라인은 `coverage-gate` 로그에 나옵니다. diff coverage 실패는 `apps/backend`에서 `diff-cover coverage.xml --compare-branch=origin/main`으로 로컬에서 재현할 수 있습니다.

## 함께 보기

- [시작하기](./getting-started.md) — 먼저 dev 스택부터.
- [코딩 표준](./coding-standards.md) — 테스트가 검증하는 규칙.
