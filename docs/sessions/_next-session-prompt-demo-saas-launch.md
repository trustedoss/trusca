---
session_prompt: Demo SaaS 출시 준비 — 9 묶음
target_branches: 9 묶음 (audit → multi-tenant 강화 → OAuth runbook → Cloud Armor → 무료 티어 → observability → 법적 페이지 → invite-only beta → GA 체크리스트)
estimated_total: ~10~15 세션 (외부 의존 — OAuth provider 등록 / DNS / 결제 / 법무 검토는 사용자가 직접 실행)
date_authored: 2026-05-11
authoring_session: post-backlog-marathon (PR #82 머지 직후)
status: draft (사용자가 scope/순서 승인 후 ready)
---

# 다음 세션 시작 prompt — Demo SaaS 출시 준비

> 본 prompt 는 TrustedOSS Portal 의 **공개 Demo SaaS (GCP 호스팅)** 출시를 위한 9 묶음 가이드다.
> 새 세션 시작 시 첫 메시지로 본 파일 경로를 그대로 인용하면 메인 세션이 컨텍스트를 자동 복원한다.

## 0. 컨텍스트 (자동 복원)

- **레포**: `github.com/trustedoss/trustedoss-portal`, main HEAD ≈ `0197b8e` (backlog-marathon 종료) 또는 그 이후.
- **단일 진실 문서**: `docs/v2-execution-plan.md`, `CLAUDE.md`, `docs/sessions/2026-05-11-backlog-marathon-complete.md`.
- **이미 구축된 인프라 코드** (재사용 대상):
  - `terraform/` — Cloud Run + Cloud SQL + Memorystore + VPC Connector (idle 비용 ~$46/월).
  - `docs-site/docs/installation/gcp-deploy.md` (+KO) — 운영자용 runbook.
  - `apps/backend/api/v1/auth/oauth*.py` — GitHub + Google OAuth flow 코드 (이미 머지됨).
  - `apps/backend/services/oauth_identity_service.py` — keyed BLAKE2b audit hash (마라톤 묶음 4).
  - PostgreSQL role 분리 (`trustedoss_app`) 와 audit_logs immutable trigger 는 마라톤 묶음 8 에서 적용 완료.
- **사용자 정책 (메모리)**:
  - `git push` + `gh pr create` 는 Claude 직접 가능.
  - **PR 머지는 사용자 직접** (backlog-marathon 의 자동 머지 권한은 #82 머지로 회수됨).
  - 외부 시스템 변경 (GCP project 생성, OAuth app 등록, DNS, 결제, 법무 문구) 은 **사용자가 직접 실행** — Claude 는 runbook 작성 + 코드 변경만.

## 1. 공통 진행 정책

### 1.1 묶음 단위 라이프사이클

1. `git checkout main && git pull --ff-only`.
2. 묶음 진입 — 새 브랜치 `feat/saas-launch-<bundle-slug>` 또는 `chore/saas-<bundle-slug>`.
3. Claude 작업 — 코드 / Helm / Terraform / Docusaurus / 테스트 EN+KO 동시.
4. 검증 — lint / typecheck / 단위 테스트 ≥ 80 % coverage / E2E green / Docusaurus EN+KO build.
5. commit + push + `gh pr create` — Conventional Commits, body 에 "사용자가 해야 할 외부 작업" 섹션 포함.
6. 사용자 머지 → "머지 했어" → 다음 묶음.

### 1.2 CLAUDE.md 규칙 (생략 금지)

- PostgreSQL only / Alembic forward-only / Celery 비동기 / DT circuit breaker / 하네스 우선 / coverage ≥ 80 % / EN+KO 동시 / RFC 7807 / 비밀번호 + JWT 표준 — 마라톤과 동일.
- `docker-compose` V1 (하이픈) / `os.getenv()` 런타임 호출 / 모든 인증 엔드포인트 / dev CORS `*` 만 허용.

### 1.3 외부 작업 마커

각 묶음 PR body 에 다음 섹션을 명시:

```markdown
## 사용자 외부 작업 (this PR 머지 전/후)

- [ ] (선택사항이면 [optional] 명시)
- ...

## Claude 가 못 하는 작업 (절대)

- GCP / Cloud Console UI 클릭.
- OAuth provider 콘솔 등록.
- DNS / 도메인 등록.
- 결제 / 청구.
- 법무 문구 작성/검토.
```

### 1.4 Producer-Reviewer (security-reviewer)

다음 묶음은 머지 전 `security-reviewer` 통과 필수:
- 묶음 2 (multi-tenant 강화) — BOLA/IDOR 검증.
- 묶음 4 (Cloud Armor) — WAF rule 우회 가능성.
- 묶음 8 (invite-only beta) — 토큰 enumeration / replay.

## 2. 묶음 1 — Demo SaaS 출시 준비도 audit

**목표**: 코드/인프라/문서의 출시 준비도를 정량 평가하고, 묶음 2-9 의 scope 를 fix.

**Claude scope**:
- `terraform/`, `apps/backend/api/v1/auth/oauth*.py`, OAuth-related 테스트, `docs-site/docs/installation/gcp-deploy.md` 검토.
- 다음 항목 표 작성:
  - OAuth GitHub/Google flow 코드 완성도 (state CSRF, PKCE, redirect URI 검증).
  - Multi-tenant 격리 — 사용자 A 의 team_id 데이터에 사용자 B 의 JWT 로 접근 가능한지 BOLA/IDOR 후보 목록.
  - Free tier 한도 (projects, scans/day, components) — 현재 enforcement 코드 위치.
  - Backup / restore 검증 — 마라톤 묶음 3 의 backup 파이프라인이 Cloud SQL 자동 백업과 어떻게 결합되는지.
  - Observability — structlog → Cloud Logging 매핑 검증.
- **산출물**: `docs/sessions/2026-05-XX-demo-saas-audit.md` — 묶음 2-9 의 입력 자료.

**사용자 외부 작업**: 없음 (코드 audit only).

**DoD**: 9 묶음 backlog 가 audit 결과로 fix 됨, 우선순위 정렬.

## 3. 묶음 2 — Multi-tenant 격리 강화

**목표**: BOLA/IDOR + RLS-level 다중-테넌트 격리 보강.

**Claude scope**:
- 묶음 1 audit 의 IDOR 후보 endpoint 별 통합 테스트 작성 (사용자 A 의 토큰 + 사용자 B 의 resource_id → 404 existence-hide 검증).
- 필요 시 service-layer `team_id` filter 추가 + Alembic 0015 (만약 RLS policy 도입).
- `security-reviewer` 머지 전 통과.

**사용자 외부 작업**: 없음.

**DoD**: 모든 user-scoped resource 에 cross-team access 테스트 1 건 이상 + 모두 grren.

## 4. 묶음 3 — OAuth provider 등록 runbook

**목표**: GitHub OAuth App + Google OAuth Client 등록 절차를 사용자가 따라할 수 있는 단계별 runbook 작성.

**Claude scope**:
- `docs-site/docs/installation/oauth-providers-setup.md` (+ KO mirror) 작성:
  - GitHub OAuth App 생성 화면별 스크린샷 자리표시자 (사용자가 자기 화면을 캡처).
  - Google Cloud Console OAuth 동의화면 + Client ID 발급.
  - 각 provider 의 callback URL = `https://demo.trustedoss.example.com/auth/oauth/{provider}/callback`.
  - Helm values / Terraform tfvars 에 ClientID/Secret 주입 위치.
- `terraform/variables.tf` 에 `github_client_id` / `github_client_secret` / `google_*` 변수 추가 (Secret Manager 우회 또는 KMS encrypted tfvars).
- E2E 테스트는 외부 provider 호출 불가 → existing visibility-only 테스트만 유지 + 신규 unit test (state token mint/verify).

**사용자 외부 작업**:
- GitHub 에서 OAuth App 생성 → Client ID/Secret 발급.
- Google Cloud Console 에서 OAuth client 생성.
- 발급된 secret 을 `terraform.tfvars` 에 기입 (절대 git commit 금지).

**DoD**: runbook 머지, 사용자가 따라가서 secret 발급까지 완료 보고.

## 5. 묶음 4 — Cloud Armor WAF + 레이트 리밋

**목표**: GCP Cloud Armor 정책 + slowapi 사용자-단위 레이트 리밋 보강 (현재 IP 단위 5/min 로그인만 존재).

**Claude scope**:
- `terraform/modules/cloud_armor/` 신규 모듈 — Cloud Run frontend 앞 LB 에 Cloud Armor policy 첨부.
- Rule set: SQLi/XSS preconfigured rule, geo-block (선택), per-IP rate limit (10 req/sec sustained).
- backend slowapi: 인증된 사용자 단위 rate limit decorator (scan trigger 등 비싼 endpoint 우선).
- `security-reviewer` 머지 전 통과.

**사용자 외부 작업**:
- `terraform apply` 로 Cloud Armor policy 적용.
- LB IP 가 변경되면 DNS 갱신.

**DoD**: WAF 정책 적용 + backend rate limit 테스트 green.

## 6. 묶음 5 — Free tier 한도 enforcement

**목표**: 무료 사용자가 demo SaaS 를 abuse 하지 못하도록 quota 강제.

**Claude scope**:
- DB schema — `teams` 테이블에 `tier`, `quota_*` 컬럼 (Alembic 0015).
- backend — quota 초과 시 RFC 7807 `urn:trustedoss:problem:free_tier_quota_exceeded` 응답.
- frontend — 사용량 표시 (project 카운트, 오늘 스캔 수) + 한도 도달 시 banner.
- 기본 limit: project ≤ 3, scans/day ≤ 10, components/project ≤ 1000.

**사용자 외부 작업**: 없음.

**DoD**: 한도 초과 시나리오 E2E + 한도 표시 UI 캡처 추가.

## 7. 묶음 6 — Observability (Cloud Logging + Cloud Monitoring)

**목표**: structlog JSON → Cloud Logging 자동 파싱, 핵심 알람 설정.

**Claude scope**:
- structlog JSON 출력 검증 (이미 적용됨) — Cloud Logging severity / trace context 매핑 확인.
- `terraform/modules/monitoring/` — 알림 정책 4 개: error rate spike, scan queue depth, DT health flap, Cloud SQL connection saturation.
- backend `/health/ready` + `/health/live` 점검 — Cloud Run health probe 정확도.
- `docs-site/docs/admin-guide/observability.md` (+KO) — 운영자 대시보드 안내.

**사용자 외부 작업**:
- `terraform apply` 로 알람 정책 적용.
- 알람 수신 채널 (이메일 / Slack webhook) 등록.

**DoD**: 4 개 알람 정책 trigger 시나리오 문서화 + `terraform plan` clean.

## 8. 묶음 7 — 법적 페이지 (ToS / Privacy / Cookie)

**목표**: OAuth provider 등록과 데이터 처리 법적 근거를 위한 페이지 작성 — 법무 검토는 사용자가 직접 진행.

**Claude scope**:
- `docs-site/src/pages/terms.tsx`, `privacy.tsx`, `cookies.tsx` — 표준 SaaS template 기반 초안 (EN + KO).
- 푸터에 링크 추가 (`docs-site/src/theme/Footer/` 커스터마이즈).
- `data-retention-policy.md` — backup retention, log retention, account deletion SLA.
- **법무 검토 placeholder**: 모든 페이지 상단에 `:::warning 법무 미검토 초안 :::` 명시.

**사용자 외부 작업**:
- 법무 검토 (변호사 또는 사내 법무팀).
- 검토 후 placeholder 제거 PR.

**DoD**: 3 개 페이지 + 푸터 링크 머지, placeholder 표기.

## 9. 묶음 8 — Invite-only beta gate

**목표**: 공개 GA 직전 invite-only 단계 — 신규 가입을 초대 토큰 보유자로 제한.

**Claude scope**:
- DB — `invite_tokens` 테이블 (Alembic 0016) — `token`, `expires_at`, `redeemed_by_user_id`, `created_by_admin_id`.
- backend — `POST /auth/register` 가 token 필수 (header 또는 query). `POST /admin/invites` 엔드포인트로 admin 이 토큰 발급.
- frontend — `/register?invite=<token>` URL 자동 prefill, invalid token 시 명확한 안내.
- admin UI — `/admin/invites` 페이지에서 발급 / 회수 / 사용 현황.
- `security-reviewer` 머지 전 통과 — token enumeration / replay 방어.

**사용자 외부 작업**: 없음 (단계적 전환).

**DoD**: invite 없이 가입 차단 + admin UI 작동.

## 10. 묶음 9 — Launch readiness review + GA 체크리스트

**목표**: 공개 GA 직전 마지막 검증.

**Claude scope**:
- `docs-site/docs/operator/saas-launch-checklist.md` 작성 — 묶음 1-8 결과 종합:
  - [ ] Multi-tenant 격리 테스트 green
  - [ ] OAuth flow 양 provider 수동 검증
  - [ ] Cloud Armor 정책 + rate limit 적용
  - [ ] Free tier 한도 enforced
  - [ ] Cloud Logging / Monitoring 알람 trigger
  - [ ] ToS / Privacy / Cookie 페이지 법무 검토 완료
  - [ ] Invite gate 적용 + admin 토큰 발급 가능
  - [ ] Cloud SQL automated backup 활성, restore drill 1 회 완료
  - [ ] Domain DNS A record + Let's Encrypt TLS
  - [ ] On-call rotation 합의
- `docs/sessions/2026-05-XX-demo-saas-launch-complete.md` 핸드오프 작성 (`docs/v2-execution-plan.md` §7 양식).

**사용자 외부 작업**:
- 체크리스트 모두 ✅ → invite gate 제거 PR → 공개 발표.

**DoD**: 체크리스트 머지 + 사용자 review 합의.

## 11. 묶음 간 cross-cutting

- **메모리**: 새 발견은 `feedback_*` / `project_*` 로 저장. 특히 OAuth provider 별 quirks, Cloud Armor false-positive, 무료 티어 enforcement edge case 우선.
- **글로싱**: "Demo SaaS" 는 공식 명칭 — KO 문서도 "Demo SaaS" 그대로. "데모 SaaS" 도 허용하되 한 문서 내에서 통일.
- **비용 알람**: GCP billing budget alert 는 묶음 6 의 observability 와 별도 — 사용자 외부 작업.

## 12. 세션 핸드오프 양식

`docs/v2-execution-plan.md` §7 의 양식을 그대로 사용. 마라톤 종료 시 `docs/sessions/2026-05-11-backlog-marathon-complete.md` 가 좋은 참고.

---

## 시작 첫 메시지 예시 (새 세션에 그대로 붙여넣기)

```
docs/sessions/_next-session-prompt-demo-saas-launch.md 에 따라 Demo SaaS 출시 준비를 진행한다.
묶음 1 (Demo SaaS 출시 준비도 audit) 부터 시작.
```
