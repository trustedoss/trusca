---
id: audit-log
title: 감사 로그
description: TrustedOSS Portal에서 모든 쓰기 작업의 추가 전용 감사 로그를 읽고 필터하고 내보냅니다.
sidebar_label: 감사 로그
sidebar_position: 4
---

# 감사 로그

포털의 모든 쓰기 작업은 **추가 전용** 감사 로그에 기록됩니다. 로그는 "누가 언제 무엇을 무엇에 했는가"의 진실의 원천 — 사고 조사·컴플라이언스 요청 응대 시 가장 먼저 보는 곳입니다.

:::note 대상 독자
조직 단위 읽기는 `super_admin`; 팀 단위 읽기는 `team_admin`.
:::

## 스키마

각 항목 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | UUIDv7 | 기본 키. 시간 사전식 정렬. |
| `ts` | timestamptz | 작업 발생 시각(서버 시계, UTC). |
| `actor_user_id` | UUID | 작업 수행 사용자(시스템 작업은 null). |
| `actor_kind` | enum | `user`, `api_key`, `system`. |
| `action` | text | 점-네임스페이스 동사. 예: `project.create`, `vuln_finding.update`, `team_membership.delete`. |
| `target_kind` | text | 영향 받은 객체 클래스(`project`, `team`, `user`, `vuln_finding` 등). |
| `target_id` | UUID | 영향 받은 객체의 UUID. |
| `request_id` | text | 구조화 로그(`X-Request-ID`)와 상관. |
| `payload` | jsonb | 정제된 before/after diff. PII는 마스킹(`mask_pii`). |
| `ip` | inet | 출처 IP. |
| `user_agent` | text | 잘린 UA 문자열. |

테이블에는 `CHECK` 제약이 있어 update와 delete를 막고 insert만 허용합니다. Forward-only Alembic 마이그레이션이 릴리스 간 본 속성을 보존합니다.

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

상단 인라인 필터 바:

- **행위자** — 이메일·user ID·`system`으로 검색.
- **동작** — 다중 선택.
- **대상 종류** — 다중 선택.
- **대상 ID** — 정확 일치.
- **날짜 범위** — 프리셋(지난 1시간·오늘·지난 7일) 또는 사용자 지정.
- **요청 ID** — 정확 일치(구조화 로그 라인이 있을 때 유용).

필터는 결합됩니다. URL이 갱신되어 동료와 필터된 뷰를 공유 가능.

### 테이블

기본 컬럼: `ts`, `행위자`, `동작`, `대상`, `ip`. 행을 클릭하면 전체 payload diff가 펼쳐집니다.

테이블은 가상화 — 1만 항목도 부드럽게 스크롤.

## CSV 내보내기

툴바의 **Export CSV**는 **현재 필터된** 결과 집합을 한 번에 최대 10만 행까지 내보냅니다. CSV는 BOM 포함 UTF-8이라 Excel이 비ASCII를 정상 처리합니다.

더 큰 윈도는 API로 페이지네이션:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/api/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&size=1000"
```

응답이 페이지됩니다; 마지막 페이지면 `next`가 null.

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
  pg_dump -U trustedoss -t audit_log trustedoss | gzip > audit-archive-2024.sql.gz

# 그 다음 archive cutoff 이전 행 삭제. UI 없음 —
# 의도적으로 수동 SQL 세션 필요.
docker-compose -f docker-compose.yml exec postgres \
  psql -U trustedoss -d trustedoss \
  -c "DELETE FROM audit_log WHERE ts < '2025-01-01';"
```

`DELETE`는 immutability 제약을 일시 비활성화 필요하며 그 자체가 감사 로그 항목을 발신합니다. 정말로 오래된 데이터에만 사용하세요.

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

### Payload grep 불가

`payload` 컬럼은 `jsonb`. 마이그레이션이 만든 GIN 인덱스로 SQL 쿼리가 빠릅니다.

```sql
SELECT * FROM audit_log
 WHERE payload @> '{"new_state": "suppressed"}'::jsonb
 ORDER BY ts DESC LIMIT 100;
```

`super_admin` SQL 세션 필요(UI 없음).

## 함께 보기

- [사용자 및 팀](./users-and-teams.md)
- [백업·복원](./backup-and-restore.md)
- [API 개요](../reference/api-overview.md)
