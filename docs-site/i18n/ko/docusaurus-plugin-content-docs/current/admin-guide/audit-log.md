---
id: audit-log
title: 감사 로그
description: TRUSCA에서 모든 쓰기 작업의 추가 전용 감사 로그를 읽고 필터하고 내보냅니다.
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
| `action` | text | 동사만(`create` / `update` / `delete`). 테이블은 `target_table`에 별도 캡처. 예: `target_table=projects&action=create`로 필터. |
| `target_table` | text | 영향 받은 객체가 속한 테이블(`projects`, `teams`, `users`, `vulnerability_findings` 등). |
| `target_id` | String(64) | 영향 받은 객체의 식별자. |
| `request_id` | text | 구조화 로그(`X-Request-ID`)와 상관. |
| `diff` | jsonb | 정제된 before/after diff. PII는 마스킹(`mask_pii`). |
| `ip` | inet | 출처 IP. |
| `user_agent` | text | 잘린 UA 문자열. |

추가 전용 계약은 **두 계층에서** 강제됩니다.

1. **애플리케이션** — 감사 리스너는 insert 만 발신하며 API가 update / delete 엔드포인트를 노출하지 않습니다.
2. **데이터베이스** — 두 개의 트리거(마이그레이션 `0012`)가 모든 변경 시도에 `SQLSTATE 23000` (integrity_constraint_violation)을 발생시킵니다.
   - `audit_logs_immutable_trigger` — BEFORE UPDATE OR DELETE, FOR EACH ROW.
   - `audit_logs_immutable_truncate` — BEFORE TRUNCATE, FOR EACH STATEMENT (PostgreSQL은 row 트리거를 UPDATE / DELETE 에서만 발사 — TRUNCATE는 BEFORE-row를 우회하므로 테이블 wipe 경로에는 별도 statement-level 가드가 필요).
   
   super-admin이 raw `psql`로 `UPDATE audit_logs ...`, `DELETE FROM audit_logs ...`, `TRUNCATE TABLE audit_logs`를 실행하면 `ERROR: audit_logs is append-only (TG_OP=…)`와 함께 트랜잭션이 abort 됩니다. INSERT는 영향받지 않으므로 리스너 경로는 정상 동작합니다.

이 트리거들은 PR #44가 로드맵으로 문서화했던 defense-in-depth 갭을 닫습니다. **알려진 잔여 우회**: 기본 설치에서는 마이그레이션 role과 런타임 앱 role이 동일한 PostgreSQL role (`trustedoss`) 입니다. 이 role이 함수와 트리거를 소유하므로 `DROP TRIGGER` / `ALTER FUNCTION ... OWNER`를 통해 "DROP TRIGGER → mutate → re-CREATE TRIGGER" 우회가 가능합니다. Phase 7 / 8 강화 PR에서 런타임 role과 마이그레이션 role 분리(`trustedoss_app`은 `audit_logs`에 DML 만 + `trustedoss_owner`는 마이그레이션)가 예정되어 있으며, 분리 후에는 런타임 앱에서 우회 불가능. 그 전까지 우회는 관측 가능합니다 — `DROP TRIGGER`는 DDL 문이라 `pg_event_trigger` (향후 audit-of-audit 강화)와 두-운영자 retention purge의 운영자 세션 로그가 포착합니다.

### 하나뿐인 예외와 그 범위

마이그레이션 `0080` 이후로 트리거가 허용하는 UPDATE는 정확히 하나입니다. 익명화 대상 사용자의 행에서 `ip`와 `user_agent`를 비우는 것입니다. 범위는 설계상 좁습니다. 호출자가 테이블 소유자 역할의 구성원이어야 하고, 그 두 열은 NULL 방향으로만 바뀔 수 있으며, `diff`를 포함한 나머지 모든 열이 그대로여야 하고, 실행하는 함수는 요청 테이블에 그 대상자에 대한 승인된 요청이 없으면 거부합니다.

**역할을 분리한 설치에서는 애플리케이션 역할이 소유자 역할의 구성원이 아니므로 실행 중인 포털에서 이 경로에 닿을 수 없습니다. 단일 역할 설치에서는 닿습니다.** 그 경우 런타임 역할과 소유자가 같은 역할이어서 구성원 검사가 무조건 참이 되고, 소유자의 암묵적 EXECUTE 권한은 REVOKE로 사라지지 않습니다. 바로 위 문단의 기본 설치 설명이 여기에도 그대로 적용됩니다. 이 예외를 형식이 아니라 실제 경계로 만드는 것은 역할 분리입니다. [사용자 익명화](./user-anonymisation.md)를 참고하십시오.

함수의 승인 검사는 행이 존재하는지를 보는 것이지 두 사람이 동의했음을 증명하는 것이 아닙니다. `user_anonymisation_requests`에 쓸 수 있는 것은 무엇이든 그런 행을 만들 수 있습니다. 그 뒤를 받치는 것은 제품 안의 2인 승인 절차, 화면에 표시된 두 사람을 확인할 수 있는 운영자, 그리고 데이터베이스에 직접 쓴 요청은 이 로그에 대응하는 항목을 남기지 못한다는 사실입니다.

`diff`는 의도적으로 예외 밖입니다. 변경의 내용을 고쳐 쓸 수 있을 만큼 넓은 예외는 이 테이블을 증거로 만드는 성질을 없앱니다. 그 결과로 옛 주소가 `diff` 안에 남을 수 있다는 사실은 [사용자 익명화](./user-anonymisation.md)에 적었습니다. 구멍을 넓혀서 조용히 해결하지 않았습니다.

직접 시험해 보기 전에 알아 둘 것이 하나 있습니다. 트리거는 값이 실제로 바뀌었는지로 판정하므로, 이미 가진 값을 다시 쓰는 UPDATE는 아무것도 바꾸지 않아 통과합니다. `ip`가 이미 NULL인 행에 위조된 스크럽을 겨누면 `UPDATE 1`이 돌아오는데, 예외가 뚫린 것처럼 읽히지만 그렇지 않습니다.

## 무엇이 기록되는가

인증된 모든 `POST`, `PATCH`, `PUT`, `DELETE`가 정확히 하나의 항목을 생성합니다. 읽기 엔드포인트(`GET`)는 기록하지 않습니다. SBOM 내보내기는 structlog `sbom_exported` 이벤트를 발신하지만 현재 릴리스에서는 `audit_logs` 행을 **생성하지 않습니다** — 내보내기를 감사 테이블에 통합하는 것은 로드맵 항목입니다.

:::note v0.10.0이 감사하지 **않는** 항목
다음 사용자 가시 작업은 `structlog` 이벤트를 발신하지만 v0.10.0 에서는
`audit_logs` 행을 **생성하지 않습니다**:

- SBOM 내보내기(`sbom_exported`)
- NOTICE 파일 다운로드(오늘은 structlog 이벤트도 없음; 로드맵 참고)
- API Key 폐기 명시 이벤트(`api_key.revoked`; 기저 `api_keys.update`
  ORM 행은 `audit_logs`에 들어갑니다)

"누가 언제 무엇을 다운로드했나" 컴플라이언스 감사 시
`docker-compose logs backend | grep sbom_exported`와 Loki / journald
집계기를 확인하세요. 이를 `audit_logs` 행으로 승격하는 작업은
로드맵 항목입니다.
:::

시스템 작업(Celery)도 기록합니다. 각 행은 동사만 담고 `target_table`을 별도로 가집니다. 예시:

- `target_table=scans&action=create`(시스템, Webhook이 스캔 트리거)
- `target_table=dt_orphans&action=delete`
- `target_table=backups&action=create`
- `target_table=notifications&action=create`

:::note 필터 노출 vs raw 행 테이블
Admin UI의 `target_table` 필터 드롭다운은 `apps/backend/schemas/admin_ops.py`의 `AuditTargetTable` 화이트리스트로 제한됩니다. 이 화이트리스트에 없는 테이블 이름(예: `dt_orphans`, `backups`, `api_keys`, `notifications`, `dt_breaker`)을 가진 행은 `audit_logs`에 그대로 기록되지만 raw SQL 로만 조회 가능합니다.
:::

## 감사 로그 페이지

**/admin/audit**은 페이징되고 필터 가능한 뷰입니다.

### 필터

현재 릴리스의 상단 인라인 필터 바:

- **행위자 user ID** — UUID 정확 일치.
- **대상 테이블** — enum 단일 선택(`projects`, `teams`, `users`, `vulnerability_findings` 등).
- **동작** — 자유 텍스트 contains(대소문자 무시 — 서버가 `ilike`를 사용하므로 `create`와 `CREATE`가 같은 행을 매칭합니다).
- **날짜 범위** — `from`과 `to`(사용자 지정).
- **검색** — 자유 텍스트 쿼리(`q`). JSON 인코딩된 `diff` 컬럼에 대해 `ilike` 매칭을 수행합니다. `action`과 `target_table`은 별도 필터 파라미터(`action=`, `target_table=`)이며 `q`는 이 두 컬럼을 매칭하지 않습니다.

필터는 결합됩니다. URL이 갱신되어 동료와 필터된 뷰를 공유 가능. 다중 선택 드롭다운, 프리셋 날짜 범위, 요청 ID 필터, 대상 ID 필터는 로드맵 항목입니다(아래 참고).

### 테이블

기본 컬럼: `created_at`, `행위자`, `동작`, `대상`, `ip`. 행을 클릭하면 전체 diff가 펼쳐집니다.

테이블은 가상화 — 1만 항목도 부드럽게 스크롤.

![감사 로그 드로어 — 단일 행의 전체 diff JSON 패널 (PII 마스킹 + request_id 상관자)](/img/screenshots/admin-audit-row-diff.png)

## CSV 내보내기

툴바의 **Export CSV**는 **현재 필터된** 결과 집합을 한 번에 최대 10만 행까지 내보냅니다. CSV는 UTF-8 이며 선두에 byte-order mark (`EF BB BF`)가 붙어 있습니다 — 한국어 / 일본어 / 중국어 로케일의 Excel이 CP949 / SJIS / GB18030으로 폴백하지 않고 UTF-8을 자동 인식하므로, 비ASCII actor 이메일이나 감사 행 diff가 mojibake로 깨지지 않습니다. 이미 UTF-8을 자동 인식하는 도구(LibreOffice, awk, Python `csv` / `utf-8-sig` 코덱)는 BOM을 자동으로 제거합니다.

더 큰 윈도는 API로 페이지네이션:

<!-- docs-uat: id=audit-api-paginate kind=api auth=admin url=/v1/admin/audit?page=1&page_size=10 expect=status:200 tier=nightly -->
```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&page_size=200"
```

응답이 `page` + `page_size`로 페이지됩니다. `page_size` 상한은 **200**이며(초과
값은 `422` 반환), 더 넓은 범위는 `page`를 증가시켜 조회하십시오.

## 흔한 쿼리

### "프로젝트 X를 누가 삭제했나?"

필터: `target_table=projects&action=archive&target_id=<project-uuid>`. 정확히 한 행이 있습니다. 프로젝트 삭제는 *soft delete*(행의 `archived_at`만 채워지고 물리 삭제 없음)라 감사 동사가 `archive`입니다 — `action=delete`는 물리 삭제된 행(memberships, teams의 hard delete 등)에만 해당합니다. 보관된 프로젝트를 복원하면 대응하는 `action=unarchive` 행이 남습니다.

### "사용자 Y가 지난주에 무엇을 했나?"

필터: `actor=y@acme.com`, 날짜 범위 지난 7일. 동작 목록이 활동을 요약합니다.

### "모든 프로젝트에 걸쳐 CVE-2024-12345를 누가 억제했나?"

필터: `target_table=vulnerability_findings&action=update`, 그 후 각 행의 diff를 펼쳐 — `diff.new_state == "suppressed"`이면서 매칭 CVE ID인 행이 답입니다. (1급 CVE 필터는 로드맵 항목)

### "한 요청을 end-to-end 추적"

사용자가 오류를 신고하면 오류 페이지에 표시된 `X-Request-ID`를 요청하세요. 본 `request_id`로 감사 로그를 필터하면 요청이 트리거한 모든 쓰기의 정식 기록을 얻습니다. 구조화 로그와 교차 참조:

<!-- docs-uat: id=audit-log-correlate kind=shell ctx=host tier=nightly waiver=illustrative-log-grep-no-deterministic-assertion -->
```bash
docker-compose -f docker-compose.yml logs backend \
  | jq -c "select(.request_id == \"$REQ\")"
```

## 로그 수집 시스템으로 상시 반출 {#continuous-export}

위의 CSV 내보내기는 사람이 눌러야 합니다. 로그를 중앙에서 수집한다면
`AUDIT_EXPORT_URL`로 감사 기록을 자동으로 넘길 수 있습니다. 백그라운드 작업이
5분마다 지정한 주소로 행 묶음을 보냅니다.

기본은 꺼짐이고, 켜든 끄든 감사 페이지와 CSV 내보내기는 그대로입니다. 밀어
보내는 경로가 하나 늘 뿐 기존 기능이 줄지 않습니다.

각 묶음은 페이지에 보이는 것과 같은 열을 담습니다. `diff`도 포함되는데, 이미
기록 시점에 마스킹된 값입니다. 비밀번호·토큰·API 키는 애초에 그 열에 들어가지
않으므로 수집 시스템에도 가지 않습니다.

```json
{
  "version": 1,
  "source": "trusca",
  "destination": "https://logs.example.com/ingest/trusca-audit",
  "count": 500,
  "rows": [
    {
      "id": "…", "created_at": "2026-08-20T02:11:44.912+00:00",
      "actor_user_id": "…", "team_id": "…",
      "action": "update", "target_table": "projects", "target_id": "…",
      "request_id": "…", "ip": "10.0.0.4", "user_agent": "…",
      "diff": {"name": {"old": "api", "new": "payments-api"}}
    }
  ]
}
```

켜기 전에 알아 둘 동작이 셋 있습니다.

처음부터 보냅니다. 새로 지정한 주소에는 앞으로 생길 것만이 아니라 이미 쌓인
기록도 넘깁니다. 설정한 순간부터 조용히 시작하면 아무도 찾아볼 생각을 못 하는
구멍이 남습니다.

건너뛰지 않고 멈춥니다. 위치는 수집 시스템이 묶음을 받아들인 뒤에만
움직입니다. 수집 시스템이 죽었거나 문서를 거부하면 재시도한 뒤 더 나아가지
않고 멈춥니다. 의도한 동작입니다. 재시도하지 않는다는 것은 행을 건너뛴다는
뜻이고, 그것이 바로 이 기능이 막으려는 상황입니다. 어디까지 갔는지는 커서
행의 `rows_exported`와 `last_run_at`에 있습니다.

현재 시각보다 조금 뒤에서 읽습니다. 행의 시각은 트랜잭션이 커밋될 때 찍히므로,
먼저 시작한 트랜잭션이 나중에 커밋될 수 있습니다. 현재 시각까지 읽으면 아직
열려 있는 트랜잭션이 나중에 그 뒤쪽에 쓸 수 있고, 그 행은 영영 전달되지
않습니다. `AUDIT_EXPORT_LAG_SECONDS`가 그 여유이고 기본값은 30초입니다.
수집 시스템의 사본이 그만큼 늦을 뿐 행은 빠지지 않습니다.

:::note 감사 기록 자체는 바뀌지 않습니다
반출은 `audit_logs`에 아무 표시도 남기지 않습니다. 그 테이블은 추가 전용이고
트리거가 그것을 강제하므로, `exported_at` 같은 열을 두면 이 기록을 신뢰할 수
있게 만드는 성질에 예외를 파는 일이 됩니다. 위치는 별도 테이블에 둡니다.
:::

## 보존 정책

감사 로그는 **자동 정리되지 않습니다**. 컴플라이언스 가치 대비 저장소 비용이 저렴합니다(전형적 설치는 활성 사용자당 연 ~50 MB 증가). 테이블 크기를 줄여야 한다면 **archive then truncate**(운영자 확인 포함) 권장:

:::tip 삭제해도 되는 시점을 알려면
매일 도는 `trustedoss.audit_log_retention_report` beat가 이미 로그 수집기로 반출된([연속 반출](#continuous-export) 커서보다 앞선) 행 중 `AUDIT_LOG_RETENTION_DAYS`보다 오래된 것의 수를 셉니다. 삭제는 하지 않는, 읽기 전용 신호입니다. [데이터 보존 → 감사 로그 보존만 수동으로 남긴 이유](./data-retention.md#audit-log-retention-why-this-one-stays-manual)를 봅니다.
:::

<!-- docs-uat: id=audit-archive-truncate kind=shell ctx=host tier=nightly waiver=destructive-retention-archive-on-production-compose -->
```bash
docker-compose -f docker-compose.yml exec postgres \
  pg_dump -U trustedoss -t audit_logs trustedoss | gzip > audit-archive-2024.sql.gz

# 그 다음 archive cutoff 이전 행 삭제. UI 없음 —
# 의도적으로 수동 SQL 세션 필요.
docker-compose -f docker-compose.yml exec postgres \
  psql -U trustedoss -d trustedoss \
  -c "DELETE FROM audit_logs WHERE created_at < '2025-01-01';"
```

immutability 트리거가 `DELETE`를 **DB 레이어에서 차단합니다**([스키마](#스키마) 참고). 의도된 retention purge 시에는 동일 유지보수 트랜잭션 안에서 두 트리거를 drop, `DELETE` 실행, 트리거 재생성을 commit 전에 마쳐야 합니다.

<!-- docs-uat: id=audit-retention-purge kind=sql ctx=postgres tier=nightly waiver=destructive-drops-triggers-and-deletes-rows -->
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

<!-- docs-uat: id=audit-verify-new-row kind=sql ctx=postgres expect=rows:>0 tier=nightly -->
1. **/admin/audit**이 ~1초 이내 최상단에 새 행을 표시.

   ```sql
   SELECT count(*) FROM audit_logs
    WHERE created_at > now() - interval '1 hour';
   ```

<!-- docs-uat: id=audit-verify-request-id kind=sql ctx=postgres expect=rows:>0 tier=nightly -->
2. `request_id`가 원래 요청의 `X-Request-ID` 응답 헤더와 일치.

   ```sql
   SELECT count(*) FROM audit_logs
    WHERE request_id IS NOT NULL
      AND created_at > now() - interval '1 hour';
   ```

<!-- docs-uat: id=audit-verify-diff-masked kind=sql ctx=postgres expect=rows:0 tier=nightly -->
3. `diff`가 예상과 일치. PII 필드(이메일·비밀번호 해시·API Key)가 마스킹되어 표시.

   ```sql
   -- 새로 적재되는 감사 행의 자격증명 컬럼은 항상 '***'로 마스킹되어야 합니다
   SELECT count(*) FROM audit_logs
    WHERE target_table = 'refresh_tokens'
      AND action = 'create'
      AND created_at > now() - interval '1 hour'
      AND (diff ->> 'token_hash' <> '***' OR diff ->> 'jti' <> '***');
   ```

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

<!-- docs-uat: id=audit-diff-query kind=sql ctx=postgres expect=ok tier=nightly -->
```sql
SELECT * FROM audit_logs
 WHERE diff @> '{"new_state": "suppressed"}'::jsonb
 ORDER BY created_at DESC LIMIT 100;
```

`super_admin` SQL 세션 필요(UI 없음).

## 로드맵

다음 기능들은 초기 문서에 언급되었으나 v0.10.0 에는 **반영되지 않았습니다**.

- `/admin/audit`의 다중 선택 필터(Action 다중 선택, Target table 다중 선택), 프리셋 날짜 범위(지난 1시간 / 오늘 / 지난 7일), 정확 일치 Target ID 필터, Request ID 필터.
- `actor_kind` 컬럼 / 필터(현재는 감사 행의 행위자가 `actor_user_id`로 식별되며 API Key 행위자는 동작 컨텍스트에서 추론).
- SBOM 내보내기(`sbom_exported`), NOTICE 파일 다운로드, API Key 폐기 이벤트를 `structlog` 전용에서 `audit_logs` 행으로 승격 — 예정.

## 함께 보기

- [사용자 및 팀](./users-and-teams.md)
- [백업·복원](./backup-and-restore.md)
- [API 개요](../reference/api-overview.md)
