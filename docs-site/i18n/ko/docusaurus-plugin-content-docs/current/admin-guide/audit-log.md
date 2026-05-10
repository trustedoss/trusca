---
id: audit-log
title: 감사 로그
description: TrustedOSS Portal에서 모든 쓰기 작업의 추가 전용 감사 로그를 읽고 필터하고 내보냅니다.
sidebar_label: 감사 로그
sidebar_position: 4
---

# 감사 로그

포털의 모든 쓰기 작업은 **추가 전용** 감사 로그에 기록됩니다. 로그는 "누가 언제 무엇을 무엇에 했는가"의 진실의 원천 — 사고 조사·컴플라이언스 요청 응대 시 가장 먼저 보는 곳입니다.

`/admin/audit` 페이지는 툴바(actor / target table / action / 시간 범위 필터)와 라이브 데이터 위의 행 표를 노출합니다:

![Admin 감사 로그 — actor / target table / action 필터 검색 툴바와 행 표](/img/screenshots/admin-audit-list.png)

:::note 대상 독자
조직 단위 읽기는 `super_admin`; 팀 단위 읽기는 `team_admin`.
:::

## 스키마

각 항목 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | UUID | 기본 키. |
| `created_at` | timestamptz | 작업 발생 시각(서버 시계, UTC). |
| `actor_user_id` | UUID | 작업 수행 사용자(시스템 작업은 null). |
| `team_id` | UUID | 해당 시 작업의 팀 범위(조직 단위 쓰기는 null). |
| `action` | text | 점-네임스페이스 동사. 예: `project.create`, `vuln_finding.update`, `team_membership.delete`. |
| `target_table` | text | 영향 받은 객체가 속한 테이블(`projects`, `teams`, `users`, `vuln_findings` 등). |
| `target_id` | UUID | 영향 받은 객체의 UUID. |
| `request_id` | text | 구조화 로그(`X-Request-ID`)와 상관. |
| `diff` | jsonb | 정제된 before/after diff. PII는 마스킹(`mask_pii`). |
| `ip` | inet | 출처 IP. |
| `user_agent` | text | 잘린 UA 문자열. |

추가 전용 계약은 **두 계층에서** 강제됩니다.

1. **애플리케이션** — 감사 리스너는 insert 만 발신하며 API 가 update / delete 엔드포인트를 노출하지 않습니다.
2. **데이터베이스** — 두 개의 트리거(마이그레이션 `0012`)가 모든 변경 시도에 `SQLSTATE 23000` (integrity_constraint_violation)을 발생시킵니다.
   - `audit_logs_immutable_trigger` — BEFORE UPDATE OR DELETE, FOR EACH ROW.
   - `audit_logs_immutable_truncate` — BEFORE TRUNCATE, FOR EACH STATEMENT (PostgreSQL 은 row 트리거를 UPDATE / DELETE 에서만 발사 — TRUNCATE 는 BEFORE-row 를 우회하므로 테이블 wipe 경로에는 별도 statement-level 가드가 필요).
   
   super-admin 이 raw `psql`로 `UPDATE audit_logs ...`, `DELETE FROM audit_logs ...`, `TRUNCATE TABLE audit_logs` 를 실행하면 `ERROR: audit_logs is append-only (TG_OP=…)` 와 함께 트랜잭션이 abort 됩니다. INSERT 는 영향받지 않으므로 리스너 경로는 정상 동작합니다.

이 트리거들은 PR #44 가 로드맵으로 문서화했던 defense-in-depth 갭을 닫습니다. **알려진 잔여 우회**: 기본 설치에서는 마이그레이션 role 과 런타임 앱 role 이 동일한 PostgreSQL role (`trustedoss`) 입니다. 이 role 이 함수와 트리거를 소유하므로 `DROP TRIGGER` / `ALTER FUNCTION ... OWNER` 를 통해 "DROP TRIGGER → mutate → re-CREATE TRIGGER" 우회가 가능합니다. Phase 7 / 8 강화 PR 에서 런타임 role 과 마이그레이션 role 분리(`trustedoss_app` 은 `audit_logs` 에 DML 만 + `trustedoss_owner` 는 마이그레이션) 가 예정되어 있으며, 분리 후에는 런타임 앱에서 우회 불가능. 그 전까지 우회는 관측 가능합니다 — `DROP TRIGGER` 는 DDL 문이라 `pg_event_trigger` (향후 audit-of-audit 강화) 와 두-운영자 retention purge 의 운영자 세션 로그가 포착합니다.

## 무엇이 기록되는가

인증된 모든 `POST`, `PATCH`, `PUT`, `DELETE`가 정확히 하나의 항목을 생성합니다. 읽기 엔드포인트(`GET`)는 기록하지 않으며 예외는 SBOM·보고서 다운로드입니다 — `*.export` 이벤트를 발신해 무엇이 누구에게 공개되었는지 증명할 수 있게 합니다.

시스템 작업(Celery)도 기록합니다. 예시:

- `scan.create`(시스템, Webhook이 스캔 트리거)
- `dt_orphan.delete`
- `backup.complete`
- `notification.send`

## 감사 로그 페이지

**/admin/audit**은 페이징되고 필터 가능한 뷰입니다.

### 필터

v2.0.0 의 상단 인라인 필터 바:

- **행위자 user ID** — UUID 정확 일치.
- **대상 테이블** — enum 단일 선택(`projects`, `teams`, `users`, `vuln_findings` 등).
- **동작** — 자유 텍스트 contains(대소문자 구분).
- **날짜 범위** — `from` 과 `to`(사용자 지정).
- **검색** — 자유 텍스트 쿼리(`q`); action 과 target 필드를 가로질러 매칭.

필터는 결합됩니다. URL이 갱신되어 동료와 필터된 뷰를 공유 가능. 다중 선택 드롭다운, 프리셋 날짜 범위, 요청 ID 필터, 대상 ID 필터는 로드맵 항목입니다(아래 참고).

### 테이블

기본 컬럼: `created_at`, `행위자`, `동작`, `대상`, `ip`. 행을 클릭하면 전체 diff가 펼쳐집니다.

테이블은 가상화 — 1만 항목도 부드럽게 스크롤.

## CSV 내보내기

툴바의 **Export CSV**는 **현재 필터된** 결과 집합을 한 번에 최대 10만 행까지 내보냅니다. CSV 는 UTF-8 이며 선두에 byte-order mark (`EF BB BF`) 가 붙어 있습니다 — 한국어 / 일본어 / 중국어 로케일의 Excel 이 CP949 / SJIS / GB18030 으로 폴백하지 않고 UTF-8 을 자동 인식하므로, 비ASCII actor 이메일이나 감사 행 diff 가 mojibake 로 깨지지 않습니다. 이미 UTF-8 을 자동 인식하는 도구(LibreOffice, awk, Python `csv` / `utf-8-sig` 코덱)는 BOM 을 자동으로 제거합니다.

더 큰 윈도는 API로 페이지네이션:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&page_size=1000"
```

응답이 `page` + `page_size`로 페이지됩니다.

## 흔한 쿼리

### "프로젝트 X를 누가 삭제했나?"

필터: `action=project.delete`, `target_id=<project-uuid>`. 정확히 한 행이 있습니다.

### "사용자 Y가 지난주에 무엇을 했나?"

필터: `actor=y@acme.com`, 날짜 범위 지난 7일. 동작 목록이 활동을 요약합니다.

### "모든 프로젝트에 걸쳐 CVE-2024-12345를 누가 억제했나?"

필터: `action=vuln_finding.update`, 그 후 각 행의 payload를 펼쳐 — `payload.new_state == "suppressed"`이면서 매칭 CVE ID인 행이 답입니다. (1급 CVE 필터는 로드맵 항목)

### "한 요청을 end-to-end 추적"

사용자가 오류를 신고하면 오류 페이지에 표시된 `X-Request-ID`를 요청하세요. 본 `request_id`로 감사 로그를 필터하면 요청이 트리거한 모든 쓰기의 정식 기록을 얻습니다. 구조화 로그와 교차 참조:

```bash
docker-compose -f docker-compose.yml logs backend \
  | jq -c "select(.request_id == \"$REQ\")"
```

## 보존 정책

감사 로그는 **자동 정리되지 않습니다**. 컴플라이언스 가치 대비 저장소 비용이 저렴합니다(전형적 설치는 활성 사용자당 연 ~50 MB 증가). 테이블 크기를 줄여야 한다면 **archive then truncate**(운영자 확인 포함) 권장:

```bash
docker-compose -f docker-compose.yml exec postgres \
  pg_dump -U trustedoss -t audit_logs trustedoss | gzip > audit-archive-2024.sql.gz

# 그 다음 archive cutoff 이전 행 삭제. UI 없음 —
# 의도적으로 수동 SQL 세션 필요.
docker-compose -f docker-compose.yml exec postgres \
  psql -U trustedoss -d trustedoss \
  -c "DELETE FROM audit_logs WHERE created_at < '2025-01-01';"
```

`DELETE`는 immutability 트리거에 의해 **DB 레이어에서 차단됩니다**([스키마](#스키마) 참고). 의도된 retention purge 시에는 동일 유지보수 트랜잭션 안에서 두 트리거를 drop, `DELETE` 실행, 트리거 재생성을 commit 전에 마쳐야 합니다.

```sql
BEGIN;
DROP TRIGGER audit_logs_immutable_truncate ON audit_logs;
DROP TRIGGER audit_logs_immutable_trigger ON audit_logs;
DELETE FROM audit_logs WHERE created_at < '2025-01-01';
CREATE TRIGGER audit_logs_immutable_trigger
  BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION audit_logs_prevent_mutation();
CREATE TRIGGER audit_logs_immutable_truncate
  BEFORE TRUNCATE ON audit_logs
  FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_prevent_mutation();
COMMIT;

-- 유지보수 윈도 종료 전 두 트리거가 복원됐는지 검증.
SELECT tgname FROM pg_trigger
 WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal;
-- 기대 결과: 정확히 두 행
--   audit_logs_immutable_trigger
--   audit_logs_immutable_truncate
```

운영자 동작 자체는 별도로 기록하세요(트리거 DDL 자체는 감사 행을 발신하지 않습니다). 두 명의 운영자가 함께 있는 상태에서 실행하며, 두 번째 운영자가 위 `pg_trigger` 검증 쿼리를 실행해 두 트리거가 모두 등록됐음을 확인한 뒤 세션을 닫습니다.

## 정상 동작 확인

권한 작업 후:

1. **/admin/audit**이 ~1초 이내 최상단에 새 행을 표시.
2. `request_id`가 원래 요청의 `X-Request-ID` 응답 헤더와 일치.
3. `payload` diff가 예상과 일치. PII 필드(이메일·비밀번호 해시·API Key)가 마스킹되어 표시.

## 트러블슈팅

### 예상한 항목이 누락

세 가지 가능성:

- 동작이 읽기 전용(감사 행 없음).
- 동작이 감사 hook 발신 전 실패(commit 전 500). `request_id`로 구조화 로그 확인.
- 행위자가 본 행을 읽을 권한 없음(team-admin 범위는 다른 팀 행을 숨김). super-admin 세션 사용.

### CSV 내보내기가 잘림

내보내기는 10만 행 상한입니다. 필터를 좁히거나 페이지네이션 API를 사용하세요.

### diff grep 불가

`diff` 컬럼은 `jsonb`. 마이그레이션이 만든 GIN 인덱스로 SQL 쿼리가 빠릅니다.

```sql
SELECT * FROM audit_logs
 WHERE diff @> '{"new_state": "suppressed"}'::jsonb
 ORDER BY created_at DESC LIMIT 100;
```

`super_admin` SQL 세션 필요(UI 없음).

## 로드맵 (v2.x)

다음 기능들은 초기 문서에 언급되었으나 v2.0.0 에는 **반영되지 않았습니다**.

- `/admin/audit`의 다중 선택 필터(Action 다중 선택, Target table 다중 선택), 프리셋 날짜 범위(지난 1시간 / 오늘 / 지난 7일), 정확 일치 Target ID 필터, Request ID 필터.
- `actor_kind` 컬럼 / 필터(현재는 감사 행의 행위자가 `actor_user_id`로 식별되며 API Key 행위자는 동작 컨텍스트에서 추론).

## 함께 보기

- [사용자 및 팀](./users-and-teams.md)
- [백업·복원](./backup-and-restore.md)
- [API 개요](../reference/api-overview.md)
