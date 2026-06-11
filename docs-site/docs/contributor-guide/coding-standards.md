---
id: coding-standards
title: Coding standards
description: Language conventions, schema migration policy, error response shape, structured logging, and i18n key rules for TrustedOSS Portal.
sidebar_label: Coding standards
sidebar_position: 2
---

# Coding standards

These conventions are enforced by code review, CI lint, and (where possible) automated checks. Read them before your first PR — fixing them late costs cycles.

:::note Audience
All contributors. Apply on every PR, including chore and docs PRs that touch code blocks.
:::

## TypeScript — strict mode, no `any`

`apps/frontend/tsconfig.json` runs with `"strict": true` and `"noImplicitAny": true`. Concretely:

- **No `any` casts.** If you reach for `any`, you are usually missing a type guard or a generic. Use `unknown` and narrow it deliberately.
- **No non-null assertions (`!`)** unless the value is provably non-null at that point and a comment explains why.
- **Discriminated unions over enums.** Enums leak transitive imports; literal unions (`type Status = "pending" | "running" | "succeeded"`) are tree-shakable.

Justified narrowing example:

```ts
// `data` comes from the server as JSON; runtime-validate before use.
function isProject(value: unknown): value is Project {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof (value as { id: unknown }).id === "string"
  );
}
```

If `any` is genuinely unavoidable (e.g. interop with an untyped third-party callback), wrap it in a typed boundary function and add a `// eslint-disable-next-line @typescript-eslint/no-explicit-any` with a one-line justification.

## Pydantic v2 — `BaseModel` + `Field(...)`

The backend is on Pydantic v2. Schemas live in `apps/backend/schemas/`.

- **Always declare types.** `Field(...)` for required fields, `Field(default=...)` or `Field(default_factory=...)` for defaults.
- **Use `model_validator` for cross-field invariants.** Avoid raising in `__init__`; the validator runs at the right time and produces structured errors.
- **Avoid `arbitrary_types_allowed`.** It bypasses validation. Wrap third-party types in a custom validator instead.

```python
from pydantic import BaseModel, Field, model_validator

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repository_url: str = Field(pattern=r"^(https?|ssh)://")
    visibility: Literal["team-only", "org-wide"] = "team-only"

    @model_validator(mode="after")
    def visibility_requires_team_admin(self) -> "ProjectCreate":
        # cross-field check goes here; raise ValueError on violation
        return self
```

## Alembic — forward-only

The migration policy is **forward-only**. `downgrade()` is not implemented:

```python
def upgrade() -> None:
    op.add_column("projects", sa.Column("watch_list", sa.ARRAY(sa.String())))
    op.execute("UPDATE projects SET watch_list = ARRAY[]::text[]")

def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
```

Two consequences:

- **Breaking column changes are 3-stage.** Adding `NOT NULL`, dropping a column, or renaming requires *expand* (add new column nullable) → *migrate data* (a separate revision or one-shot Celery task) → *contract* (drop old column / set NOT NULL). Never combine in one revision.
- **Schema and data migrations are separate revisions.** A schema revision should not embed a `bulk_insert` more than a few rows. For larger data shifts, write a one-shot Celery task that is **idempotent** and queue it from a separate `data_xxxx_*` revision.

## RFC 7807 — `application/problem+json`

Every 4xx and 5xx response uses `application/problem+json`. The base shape:

```json
{
  "type": "https://trustedoss.dev/problems/forbidden",
  "title": "Forbidden",
  "status": 403,
  "detail": "API key 'tos_a1b2c3d4_…' lacks the scan:trigger permission.",
  "instance": "/v1/projects/42/scans"
}
```

- `type` is a stable URI, even if the URI does not resolve. We treat it as a **machine-readable error code**.
- `title` is a short human-readable summary; do not interpolate user input.
- `detail` may interpolate user input but never leak secrets.
- `instance` is the URL path that produced the error (request-id is in the `X-Request-ID` header, not the body).

Domain-specific extensions are **snake_case** and registered as Pydantic models so they appear in OpenAPI:

```python
class GateFailedProblem(Problem):
    type: Literal["https://trustedoss.dev/problems/gate-failed"]
    failing_components: list[str]
    license_findings: int
    cve_findings: int
```

The conversion from exception to Problem happens in a single FastAPI `exception_handler`. Do not return raw `HTTPException` from routes.

## structlog — JSON lines, context-propagated

Logs are JSON, one event per line. `structlog` is configured in `apps/backend/core/logging.py`. Every log line carries:

- `request_id` — `X-Request-ID` from the inbound header, or a UUIDv7 minted by the middleware.
- `user_id`, `team_id` — set by the auth middleware, `None` for unauthenticated calls.
- `task_id` — set by Celery for tasks (worker logs).

Use the binder pattern:

```python
log = structlog.get_logger().bind(component="scan-pipeline")

async def run_scan(scan_id: str) -> None:
    bound = log.bind(scan_id=scan_id)
    bound.info("scan.start")
    ...
    bound.info("scan.finish", duration_ms=duration_ms)
```

### PII masking

**Never log PII in plaintext.** Pass values through `mask_pii()`:

```python
from core.security import mask_pii

log.info("auth.login.success", email=mask_pii(email))
# emits: { ..., "email": "h****@n****.com" }
```

The masked categories are: email, full names, raw tokens, API key full secrets (the prefix is fine), webhook URLs, and OAuth `code` query parameters.

## i18n keys — `<feature>.<screen>.<element>` kebab-case

Every UI string lives in `apps/frontend/src/locales/{en,ko}/<namespace>.json`. Keys follow:

```
notifications.inbox.empty-state-title
notifications.inbox.empty-state-body
notifications.preferences.toggle-email-tooltip
```

Rules:

- **Three segments minimum:** feature → screen → element. Rare edge case (a global helper) may use two.
- **Kebab-case** within a segment. Avoid camelCase or snake_case.
- **EN and KO mirror each other.** Adding a key in EN without the corresponding KO key fails the `i18next-parser` drift gate in CI.
- **No string concatenation.** Use ICU placeholders: `"badge.unread-count": "{{count}} unread"`.

When you remove a key from a component, the parser drift gate also catches the orphan in EN/KO files; remove it from both.

## `# nosec` and `# nosemgrep` — justify in line

Static analysis runs `bandit` (Python) and `semgrep` (multi-language) in CI. SAST is **hard-fail** on High+. Suppress only when the finding is provably a false positive, and justify on the line:

```python
data = pickle.loads(blob)  # nosec B301: blob comes from the local backup volume, owned by root, never user-supplied
```

```python
expression = re.compile(user_input)  # nosemgrep: regex-from-user-input: validated upstream by `is_safe_regex()`
```

The format is:

```
# nosem grep: <rule-id>: <one-sentence justification>
```

Reviewers reject suppressions that lack a justification or whose justification does not address the rule. If multiple lines need the same suppression, lift the offending logic into a single function and suppress once.

## Frontend UI — use the shared primitives

The frontend has a single design system; the [Design system reference](../reference/design-system.md) is the source of truth for tokens, components, and motion. Do not hand-roll what a primitive already covers:

- **Page headers** — render every route header through `PageHeader` (stacked / bar). Never write a bare `<header><h1>` (the breadcrumb detail pages are the one documented exception).
- **Typography** — use the `typography.tsx` primitives (`PageTitle` / `SectionTitle` / `Subtitle` / `Body` / `Caption` / `Eyebrow`); reach for a raw `text-*` utility only for one-off inline spans.
- **Feedback** — `useToast()` for success / non-blocking notices; keep form-validation errors inline (RFC 7807 `detail`). Toasts carry the `data-toast-key` e2e contract — pass a stable `key`.
- **Empty / loading** — `EmptyState` (layered medallion) and the composite skeletons in `skeletons.tsx`, not ad-hoc bars.
- **Colour** — reference tokens / Tailwind utilities (`bg-card`, `text-risk-high`), never a hex literal in a component.

Eyeball every primitive at `/dev/design-preview` (the living reference); new shared primitives should be added there.

## See also

- [Design system](../reference/design-system.md) — tokens, components, motion, accessibility.
- [Getting started](./getting-started.md) — how to get the dev stack up.
- [Testing guide](./testing-guide.md) — pytest, Playwright, coverage gates.
- [Agent team](./agent-team.md) — security review checkpoints.
