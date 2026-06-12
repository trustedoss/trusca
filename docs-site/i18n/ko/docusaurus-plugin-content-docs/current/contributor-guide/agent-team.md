---
id: agent-team
title: 에이전트 팀
description: TRUSCA를 만드는 9명의 전문 에이전트, Producer-Reviewer 패턴, security review 트리거 시점.
sidebar_label: 에이전트 팀
sidebar_position: 4
---

# 에이전트 팀

TRUSCA는 9명의 전문 에이전트로 오케스트레이션된 팀이 만듭니다. 각 에이전트는 도메인을 소유하며, 작업이 그 도메인으로 넘어갈 때 하네스가 호출합니다. 이 페이지는 컨트리뷰터를 위해 어떤 에이전트가 무엇을 하는지, 언제 어떤 에이전트를 트리거해야 하는지, **필수 security review 체크포인트**가 무엇인지 설명합니다.

:::note 대상 독자
에이전트 하네스로 작업을 출하하는 컨트리뷰터, 그리고 어떤 체크포인트가 비협상인지 알고자 하는 리뷰어.
:::

## 9명의 에이전트

| 에이전트 | 소유 영역 | 주된 Phase |
|---|---|---|
| **`backend-developer`** | FastAPI 엔드포인트, Pydantic 스키마, `apps/backend/services/`의 비즈니스 로직. | Phase 1 ~ 5 |
| **`db-designer`** | PostgreSQL 스키마, Alembic forward-only 마이그레이션, 인덱스, 제약. | Phase 0 ~ 1 |
| **`scan-pipeline-specialist`** | Celery 태스크, cdxgen / scancode / Trivy 통합, Trivy DB 라이프사이클(부팅 다운로드, 주간 refresh, 자동 재매칭). | Phase 2 |
| **`frontend-dev`** | React 18 + shadcn/ui 컴포넌트, TanStack Query 훅, Zustand 스토어, 라우트 와이어링. | Phase 2 ~ 6 |
| **`i18n-specialist`** | `react-i18next` 셋업, EN / KO 번역, `i18next-parser` 드리프트 게이트, 언어 토글. | Phase 6 |
| **`devops-engineer`** | Docker Compose dev / prod, GitHub Actions, Helm chart, install / upgrade / backup / restore 스크립트. | Phase 0, 7 ~ 8 |
| **`test-writer`** | pytest 단위·통합, Playwright `PortalPage` 하네스, 적대적 입력 매트릭스. | 매 phase |
| **`doc-writer`** | Docusaurus 페이지, EN / KO 문서, API 레퍼런스, 본 컨트리뷰터 가이드. | Phase 7 |
| **`security-reviewer`** | OWASP Top 10 리뷰, 의존성 CVE 분류, 감사 로그 검증, 구현 후 보안 사인오프. | Phase 8 (필요 시 수시) |

## 협력 — 패턴

오케스트레이터는 네 가지 패턴으로 에이전트 간 작업을 라우팅합니다.

### 팬-아웃 / 팬-인

phase 내 독립 작업은 병렬로 실행됩니다.

```
                   ┌── backend-developer (엔드포인트)
Phase 4 admin  ──┬─┤
                   ├── frontend-dev (UI)
                   ├── test-writer (하네스 + 테스트)
                   └── doc-writer (admin guide)
```

오케스트레이터는 **모든 가닥이 green**일 때만 머지합니다.

### Producer-Reviewer

producer 에이전트가 초안을 만들고 reviewer 에이전트가 도전합니다. 보안 임계 경로에 사용됩니다 — 아래 [필수 security review 체크포인트](#필수-security-review-체크포인트)를 참고.

### 파이프라인

순서 제약이 있는 phase는 파이프라인됩니다 — Phase 0(기반) → Phase 1(인증) → Phase 2(스캔) → … . 하류 에이전트는 상류 의존성이 머지되기 전까지 시작하지 않습니다.

### Expert pool

오케스트레이터는 도메인에 맞는 에이전트로 라우팅합니다. 마이그레이션은 `db-designer`, Helm 변경은 `devops-engineer`. 직접 코드를 작성하는 컨트리뷰터도 머릿속으로 적합 에이전트를 골라 그 컨벤션을 따라야 합니다.

## 필수 security review 체크포인트 {#필수-security-review-체크포인트}

`security-reviewer`는 **선택적인** 의례 리뷰가 아닙니다. 다음 코드 경로는 Producer-Reviewer를 의무적으로 트리거합니다 — 이 경로를 건드리는 PR은 `security-reviewer` 사인오프 없이 머지되지 않습니다.

1. **인증과 세션** — `apps/backend/auth/`, JWT 발급, refresh-token 회전, 세션 쿠키 정책, 비밀번호 해싱 구성, auth 라우트의 레이트 리밋 구성.
2. **API Key 관리** — `apps/backend/services/api_key_service.py`, 해싱, prefix 조회, scope 시멘틱, 폐기 전파, 감사 emit.
3. **Trivy DB 라이프사이클** — 부팅 시 `trivy --download-db-only`, 주간 Celery beat refresh, 자동 재매칭, air-gapped 미러 경로, 그리고 신뢰할 수 없는 SBOM 입력을 파싱하는 스캔별 `trivy sbom` 호출.
4. **OAuth 흐름** — `apps/backend/auth/oauth/`, `(provider, provider_user_id)` 기반 신원 매칭, signed-state CSRF 토큰, 7개 오류 코드 매핑, `redirect_after` 통과.
5. **CI 빌드 게이트** — `apps/backend/services/policy_gate.py`, `gate=pass|fail` 결정, action / 템플릿 / Jenkinsfile의 exit-code-1 계약.
6. **백업 / 복원 파괴적 흐름** — `apps/backend/tasks/backup.py`, `/admin/backup` Upload+Restore 엔드포인트, `X-Confirm-Restore` 사전 조건, 타이핑 게이트, super-admin 강제.

리뷰어는 (최소한) 다음을 확인합니다 — RFC 7807 준수, structlog PII 마스킹, 적대적 입력 parametrize 커버리지, 감사 로그 emit, OpenAPI 스키마 추가, 레이트 리밋 설정, 경계에서의 입력 검증.

## security review 트리거 방법

위 6개 체크포인트 중 하나를 PR이 건드린다면:

1. PR 설명에 `## Security checkpoints` 섹션을 추가하고 위 규칙 번호를 나열.
2. `@security-reviewer`(에이전트 식별자)를 멘션하거나 `security-review-required` 라벨을 적용.
3. 일반 코드 리뷰를 요청하기 전에 리뷰어 코멘트를 처리.

경로가 보안 임계인지 확신이 없다면 **그냥 리뷰를 요청하세요**. False positive는 비용이 싸지만, 누락된 리뷰는 취약점을 출하시킵니다.

## 에이전트 정의 읽기

에이전트 정의는 본 레포 외부 `revfactory/harness`에 있습니다. 각 정의는 역할·허용 도구·도메인 가이드라인·출력 형식·mock task를 명시합니다. 하네스 오케스트레이터가 매 호출마다 이를 읽습니다. 컨트리뷰터로서 에이전트를 직접 구성하지는 않습니다 — PR을 작성하면 오케스트레이터가 라우팅합니다.

## 함께 보기

- [시작하기](./getting-started.md) — 환경 셋업.
- [코딩 표준](./coding-standards.md) — 모든 에이전트가 강제하는 컨벤션.
- [테스트 가이드](./testing-guide.md) — `test-writer`가 diff에서 기대하는 것.
