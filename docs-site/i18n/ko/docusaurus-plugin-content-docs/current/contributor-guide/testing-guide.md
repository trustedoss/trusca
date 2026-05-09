---
id: testing-guide
title: 테스트 가이드
description: pytest 레이아웃, Playwright PortalPage 하네스, 적대적 입력 parametrize, 80% coverage 머지 게이트.
sidebar_label: 테스트 가이드
sidebar_position: 3
---

# 테스트 가이드

테스트는 1급 시민입니다. PR 머지 게이트는 **변경된 코드의 line coverage ≥ 80 %**와 **모든 E2E 핵심 시나리오 green**입니다. 이 페이지는 레이아웃, 하네스 패턴, 정적 분석으로는 못 잡는 버그를 잡는 적대적 입력 규칙을 다룹니다.

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

**변경된 라인** 기준 line coverage ≥ 80 %를 목표로. CI의 `coverage diff` 잡이 파일별 델타를 보고합니다 — 79 %에서 멈추면 머지가 막힙니다.

### 레이아웃 가이드

- **Unit:** 테스트 대상 함수가 DB·HTTP·Celery를 사용하지 않음. 경계에서 mock.
- **Integration:** 라우트를 FastAPI TestClient로 종단 간 실행, 실제 PostgreSQL은 `pytest-testcontainers`로. **SQLAlchemy를 mock하지 않음.**
- **E2E (backend):** worker가 별도 fixture로 실제 동작하는 상태에서 HTTPX로 API를 블랙박스로 구동. Playwright가 주된 E2E이므로 절제해서 사용.

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
- 사용자 콘텐츠가 regex·경로·셸로 보간되는 모든 곳.

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

## Coverage 게이트 — 구체

머지 게이트는 `.github/workflows/ci.yml`에서 강제됩니다.

- **Unit + integration 합산:** **변경된 라인** 기준 line coverage ≥ 80 %.
- **E2E (Playwright):** `apps/frontend/tests/e2e/_core/`의 핵심 시나리오 전부 통과. 새 핵심 시나리오는 해당 기능과 함께 추가합니다.

CI는 PR 코멘트로 coverage 보고서를 게시합니다 — 79.x %에서 머지가 막히니 테스트를 추가하세요.

## 함께 보기

- [시작하기](./getting-started.md) — 먼저 dev 스택부터.
- [코딩 표준](./coding-standards.md) — 테스트가 검증하는 규칙.
- [에이전트 팀](./agent-team.md) — `test-writer`와 `security-reviewer`는 언제 동원할지.
