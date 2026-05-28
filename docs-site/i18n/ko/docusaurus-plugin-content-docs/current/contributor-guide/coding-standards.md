---
id: coding-standards
title: 코딩 표준
description: 언어 컨벤션, 스키마 마이그레이션 정책, 오류 응답 형태, 구조화된 로깅, i18n 키 규칙.
sidebar_label: 코딩 표준
sidebar_position: 2
---

# 코딩 표준

이 컨벤션들은 코드 리뷰·CI lint·(가능한 경우) 자동 체크로 강제됩니다. 첫 PR 전에 읽어 두세요 — 나중에 수정하면 사이클을 낭비합니다.

:::note 대상 독자
모든 컨트리뷰터. 코드 블록을 건드리는 chore·docs PR을 포함해 모든 PR에 적용됩니다.
:::

## TypeScript — strict 모드, `any` 금지

`apps/frontend/tsconfig.json`은 `"strict": true`와 `"noImplicitAny": true`로 동작합니다. 구체적으로:

- **`any` 캐스팅 금지.** `any`에 손이 간다면 보통 type guard 또는 generic이 빠진 것입니다. `unknown`을 사용하고 의도적으로 좁히세요.
- **non-null assertion(`!`) 금지** — 해당 시점에 값이 증명적으로 non-null이고 그 이유를 주석으로 설명하지 않는 한.
- **enum보다 discriminated union.** Enum은 transitive import를 누설합니다. 리터럴 union(`type Status = "pending" | "running" | "succeeded"`)은 tree-shake됩니다.

좁히기의 정당한 예:

```ts
// `data`는 서버에서 JSON으로 오므로 사용 전에 런타임 검증.
function isProject(value: unknown): value is Project {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof (value as { id: unknown }).id === "string"
  );
}
```

`any`가 정말 불가피한 경우(예: 타입이 없는 third-party 콜백과의 interop), 타입 경계 함수로 감싸고 한 줄 정당화와 함께 `// eslint-disable-next-line @typescript-eslint/no-explicit-any`를 붙이세요.

## Pydantic v2 — `BaseModel` + `Field(...)`

백엔드는 Pydantic v2입니다. 스키마는 `apps/backend/schemas/`에 둡니다.

- **항상 타입 선언.** 필수 필드는 `Field(...)`, 기본값은 `Field(default=...)` 또는 `Field(default_factory=...)`.
- **교차 필드 불변식은 `model_validator`.** `__init__`에서 raise하지 마세요. validator가 적절한 시점에 동작하며 구조화된 오류를 만듭니다.
- **`arbitrary_types_allowed` 회피.** 검증을 우회합니다. 대신 third-party 타입을 커스텀 validator로 감싸세요.

```python
from pydantic import BaseModel, Field, model_validator

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repository_url: str = Field(pattern=r"^(https?|ssh)://")
    visibility: Literal["team-only", "org-wide"] = "team-only"

    @model_validator(mode="after")
    def visibility_requires_team_admin(self) -> "ProjectCreate":
        # 교차 필드 체크. 위반 시 ValueError raise.
        return self
```

## Alembic — forward-only

마이그레이션 정책은 **forward-only**입니다. `downgrade()`는 구현하지 않습니다.

```python
def upgrade() -> None:
    op.add_column("projects", sa.Column("watch_list", sa.ARRAY(sa.String())))
    op.execute("UPDATE projects SET watch_list = ARRAY[]::text[]")

def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
```

두 가지 결과:

- **Breaking 컬럼 변경은 3단계.** `NOT NULL` 추가, 컬럼 drop, rename은 *expand*(새 컬럼을 nullable로 추가) → *migrate data*(별도 revision 또는 일회성 Celery task) → *contract*(기존 컬럼 drop / NOT NULL 설정)로 분리. 한 revision에 결합 금지.
- **스키마와 데이터 마이그레이션은 별도 revision.** 스키마 revision에는 몇 행 이상의 `bulk_insert`를 넣지 않습니다. 더 큰 데이터 이동은 **멱등한** 일회성 Celery task로 작성하고 별도 `data_xxxx_*` revision에서 큐에 넣으세요.

## RFC 7807 — `application/problem+json`

모든 4xx·5xx 응답은 `application/problem+json`을 사용합니다. 기본 형태:

```json
{
  "type": "https://trustedoss.dev/problems/forbidden",
  "title": "Forbidden",
  "status": 403,
  "detail": "API key 'tos_a1b2c3d4_…' lacks the scan:trigger permission.",
  "instance": "/v1/projects/42/scans"
}
```

- `type`은 안정된 URI입니다(URI가 resolve되지 않아도 무방). **기계가 읽는 오류 코드**로 취급합니다.
- `title`은 짧은 사람용 요약 — 사용자 입력을 보간하지 마세요.
- `detail`은 사용자 입력을 보간할 수 있지만 비밀값을 흘리지 마세요.
- `instance`는 오류를 만든 URL 경로입니다(request-id는 본문이 아니라 `X-Request-ID` 헤더).

도메인 특화 확장은 **snake_case**로 두고 OpenAPI에 등장하도록 Pydantic 모델로 등록합니다.

```python
class GateFailedProblem(Problem):
    type: Literal["https://trustedoss.dev/problems/gate-failed"]
    failing_components: list[str]
    license_findings: int
    cve_findings: int
```

예외 → Problem 변환은 단일 FastAPI `exception_handler`에서 일어납니다. 라우트에서 raw `HTTPException`을 반환하지 마세요.

## structlog — JSON 라인, 컨텍스트 전파

로그는 JSON, 한 라인 한 이벤트입니다. `structlog`는 `apps/backend/core/logging.py`에서 구성됩니다. 모든 로그 라인은 다음을 포함합니다.

- `request_id` — 인바운드 헤더 `X-Request-ID`, 또는 미들웨어가 생성한 UUIDv7.
- `user_id`, `team_id` — 인증 미들웨어가 설정. 비인증 호출은 `None`.
- `task_id` — Celery가 task에 설정(worker 로그).

binder 패턴:

```python
log = structlog.get_logger().bind(component="scan-pipeline")

async def run_scan(scan_id: str) -> None:
    bound = log.bind(scan_id=scan_id)
    bound.info("scan.start")
    ...
    bound.info("scan.finish", duration_ms=duration_ms)
```

### PII 마스킹

**평문 PII를 절대 로그에 남기지 마세요.** 값은 `mask_pii()`를 거쳐야 합니다.

```python
from core.security import mask_pii

log.info("auth.login.success", email=mask_pii(email))
# 출력: { ..., "email": "h****@n****.com" }
```

마스킹 대상: 이메일, 풀 네임, raw 토큰, API Key full secret(prefix는 무방), Webhook URL, OAuth `code` 쿼리 파라미터.

## i18n 키 — `<feature>.<screen>.<element>` kebab-case

모든 UI 문자열은 `apps/frontend/src/locales/{en,ko}/<namespace>.json`에 둡니다. 키 형식:

```
notifications.inbox.empty-state-title
notifications.inbox.empty-state-body
notifications.preferences.toggle-email-tooltip
```

규칙:

- **세그먼트 최소 3개:** feature → screen → element. 드물게 글로벌 헬퍼는 2개도 허용.
- **세그먼트 내 kebab-case.** camelCase·snake_case 회피.
- **EN과 KO는 서로 mirror.** EN에 키를 추가하고 KO를 누락하면 CI의 `i18next-parser` 드리프트 게이트가 실패합니다.
- **문자열 연결 금지.** ICU placeholder를 사용하세요 — `"badge.unread-count": "{{count}} unread"`.

컴포넌트에서 키를 제거하면 parser 드리프트 게이트가 EN/KO의 고아 키도 잡아냅니다 — 양쪽에서 제거하세요.

## `# nosec`과 `# nosemgrep` — 라인 안에서 정당화

CI는 `bandit`(Python)과 `semgrep`(다언어) 정적 분석을 돌립니다. SAST는 High+에서 **hard-fail**입니다. 정당하게 false positive로 입증된 경우에만 suppress하고 라인에서 정당화하세요.

```python
data = pickle.loads(blob)  # nosec B301: blob은 root 소유인 로컬 백업 볼륨에서 옴, 사용자 공급 아님
```

```python
expression = re.compile(user_input)  # nosemgrep: regex-from-user-input: `is_safe_regex()`로 상위에서 검증됨
```

형식:

```
# nosem grep: <rule-id>: <한 문장 정당화>
```

리뷰어는 정당화가 없거나 룰을 다루지 않는 suppress를 거절합니다. 같은 suppress가 여러 라인에 필요하면 문제 로직을 단일 함수로 추출해 한 곳에서 suppress하세요.

## 함께 보기

- [시작하기](./getting-started.md) — dev 스택 띄우기.
- [테스트 가이드](./testing-guide.md) — pytest, Playwright, coverage 게이트.
- [에이전트 팀](./agent-team.md) — security review 체크포인트.
