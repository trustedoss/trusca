# 핸드오프 — W2 BD 정합 진행 (#31 완료 · #33a 완료 · #33b 다음) (2026-05-26)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 "수동 테스트 발견 + Black Duck 정합 (Wave 1~4)". 이 문서는 그 세션 스냅샷이다.
> 직전 세션 핸드오프: [`2026-05-26-w1-trust-recovery-complete.md`](./2026-05-26-w1-trust-recovery-complete.md) — W1 신뢰 복구 ✅, W2~W4 시작점 안내.

---

## 이번 세션 결과 (3 push)

- **W1 잔여 정리** — `d5c7ad9` (사용자 직접 푸시): W1 Surface B(`scan_metadata.dt_vulnerability_count` 캡처 + Overview `vuln_data_available` tri-state + 캐비엇 Alert) + W1 CI red 픽스(`0024` ruff E501·`test_admin_ops_api` DT count probe side_effect·`test_project_list_enrichment` #29 `last_scan_at`=최신시도 계약). 직전 세션 핸드오프 시점 미푸시였던 잔여물 일괄.
- **walk 디버그 정리** — `a7aa1b0` ✅ CI green: 7e3ccd0 일괄 커밋에 섞여 들어간 `apps/frontend/*walk*.mjs` 7개(exploratory Playwright, 하드코딩 UUID/계정, "Temporary; delete after") `git rm`. 영구 자산(`playwright.walkthroughs.config.ts`, `tests/walkthroughs/`, `docs-site/static/img/walkthroughs/`, `scripts/encode-walkthroughs.sh`)은 보존.
- **W2 #31 풀스택** — `66d2a77` 🟢 CI(스킵+성공): Components 탭 Direct/Transitive + Usage(BD 정합).
  - BE: `ComponentSummary`/`ComponentDetailResponse`에 `dependency_scope`(req>opt 집계, NULL→`—`) 추가, `?direct=true|false`·`?dependency_scope=required|optional|unspecified`(미지값 drop, no 422) 필터, `_SCOPE_RANK`/`_normalize_scope_filter` 도입(sev/lic 패턴 미러).
  - FE: 신규 `DependencyTypeBadge`·`DependencyScopeBadge`, 테이블 컬럼 폭 재배치(Name/Type/Version/License/Usage/Severity/CVEs), 툴바 Type 3-state segment + Usage MultiSelect, 드로어 meta, EN/KO 22키.
  - 게이트: ruff/mypy clean · pytest 54 · typecheck · lint(0 errors) · i18n:check · vitest 908(+21).
- **W2 #33a (License 리스크축)** — `1739f3e` 🟦 CI(in_progress at handoff write): 갭 분석 시 "조치신호(Exploitable/Solution)+CVSS 벡터"는 이미 구현됨 확인(Exploitable=7-state enum + status 배지, Solution=v2.2-a3 `upgrade_recommendation`+`DrawerUpgradeSection`, CVSS 벡터=`Vulnerability.cvss_vector`+드로어 `cvss_vector_label`). 실제 갭(a) License 리스크축만 이 커밋에 구현:
  - BE: `lic_subq` JOIN(per-cv worst rank, COALESCE→`unknown`)으로 `component_license_category` 노출 + `?license_category=` 필터(unknown=LEFT-JOIN-miss 버킷, 미지값 drop). project_detail_service의 `_license_rank_case`/`_LICENSE_CATEGORY_RANK`/`_normalize_license_filter` 그대로 import 재사용 — Components 탭과 같은 분류기.
  - FE: VulnerabilitiesTab에 License 컬럼(CVE→Severity→**License**→Reachable→… 순), 기존 `LicenseCategoryBadge` 재사용, 툴바 MultiSelect, EN/KO 신규 **2키만**(옵션 라벨은 기존 `license_category.*` 재사용 — 중복 회피).
  - 게이트: ruff/mypy clean · pytest 144(+5)/7 skipped · vitest 913(+5) · openapi snapshot regen 1줄.

## 트래커 §0.5 갱신

- #31 ✅ 완료 (2026-05-26)
- #33 → 🟦 진행중(a✅, b⬜) + 한 줄에 'Exploitable/Solution+CVSS 벡터 이미 구현됨' 정정 명시
- (W1 #34/#35는 직전 세션에서 ✅로 갱신 끝)

## 다음 세션: **W2 #33b (Bulk actions)** 부터

### 33b 범위 (3 sub-task)
1. **백엔드 신규 엔드포인트** `POST /v1/projects/{project_id}/vulnerabilities:bulk-transition`
   - 입력: `{ "finding_ids": uuid[], "target_status": str, "justification"?: str }`
   - per-finding `_assert_can_transition`(권한 + STATUS_TRANSITIONS 검증)
   - 부분실패 정책 결정 필요(D-bulk): all-or-nothing tx vs per-row 결과 배열. 기존 단건 PATCH가 멱등이라 per-row 결과 배열이 UX적으로 자연스러움(BD도 그렇게).
   - 감사로그: 기존 단건 패턴(`_emit_audit` 또는 ORM listener) per-row 동일 emit, bulk wrapper에서 group_id 부여(추적용)
2. **권한 게이팅** `suppressed` 전이는 team_admin↑ (기존 `STATUS_REQUIRES_TEAM_ADMIN`) — bulk에서도 그대로
3. **프론트엔드 UI**
   - VulnerabilitiesTab 행에 체크박스 컬럼 추가(Virtuoso row 멀티선택 — 헤더 selectAll · 행 토글 · 페이지간 보존 정책 결정 필요)
   - 선택 시 상단 액션 바(BD 패턴) — 선택한 N개 + 상태 전이 드롭다운 + Apply
   - 422/409 부분실패 시 per-row 결과 토스트(성공 N건·실패 M건 with details)

### 33b 의존성 결정 (D-bulk 착수 전 확정)
- 부분실패 처리 **= per-row 결과 배열**(권장: BD/Snyk 정합 + UX 자연), all-or-nothing은 옵션 백오프
- 페이지간 선택 보존 **= 단일 페이지만 유지**(권장: 1만 row scope에서 충분, 멀티페이지 셀렉트는 follow-up). 사용자가 다중페이지 워크플로 요구하면 BE에 "전체 일치 선택" 토큰 API 추가
- bulk 크기 캡 **= 200**(권장: 단일 트랜잭션 안전선, 단건 PATCH 90% 케이스 흡수)

### 시작 절차 (다음 세션 첫 메시지)
"트래커 §0.5 #33b부터. 핸드오프 `docs/sessions/2026-05-26-w2-bd-parity-progress.md` §"다음 세션"의 sub-task 1~3 순서. D-bulk 결정 셋(per-row 결과 / 단일 페이지 / 200 cap)을 권장값으로 채택 — 다른 선택 있으면 그 자리에서 정정."

### 33b 후 W3
- **#32** 통합 Reports 센터 탭(Notices/SBOM/Vulnerability/VEX 생성·이력 통합 UI)
- **#30** 프로젝트 목록 행에 릴리스/스캔 수 표시

### W4 후속/위생
- #26·#27·#19~#22 (vex_import 앵커 보안검토 · vuln 툴바 레이아웃 · 콘솔 위생/정리)

---

## 환경 메모 (직전 세션과 동일)

- 컨테이너 실행 중(`backend`/`celery-worker`/`celery-beat`/`postgres`/`redis`/`dtrack-api`/`frontend`). 백엔드 코드 편집 후 `docker-compose restart backend` 필수.
- 백엔드 테스트는 컨테이너에서: `docker-compose exec -T backend python -m pytest …`. 호스트 직접 실행 불가(conftest가 `redis`/`postgres` hostname에 연결).
- OpenAPI 스냅샷 의도적 갱신: `docker-compose exec -T -e REGEN_OPENAPI_SNAPSHOT=1 backend python -m pytest tests/unit/test_openapi_contract.py -q` → diff 리뷰.
- 프론트: `npm run typecheck` / `lint` / `i18n:check`(푸시 전 필수, 복수형 `_one/_other` 금지) / `npx vitest run`.
- 풀-런 13 fail/15 error는 모두 테스트 격리 오염(우선 obligation/license 클러스터)·rate-limit 플레이크 — 단독 실행 시 PASS. CI clean DB에서는 무관(직전 세션 핸드오프와 동일 확인).

## 미푸시 잔여

없음(이 핸드오프 포함 다음 커밋으로 처리).
