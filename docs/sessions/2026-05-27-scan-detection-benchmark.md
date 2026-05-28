# 핸드오프 — scan-bench 검출력 벤치마크 (2026-05-27)

> 양식: `docs/v2-execution-plan.md` §7. v2.4.0 GA 직전 cdxgen + Trivy 검출력 검증.

## 한 줄 요약
frontend-admin 계정으로 fixture 32개 + real-world 3개를 스캔. **npm/Python은 production-ready**, **Java multi-module + Maven classifier 케이스는 재현 가능한 P0 backend 버그 발견** (W8-#46 등재).

## 산출물
| 파일 | 용도 |
|---|---|
| `scripts/scan-bench/run_bench.py` | 자동화 orchestrator (login → create → upload → scan → poll → collect → CSV/md/jsonl) |
| `scripts/scan-bench/README.md` | 사용법 |
| `scripts/scan-bench/out/fixtures-20260528-003142.{csv,md,jsonl}` | A 매트릭스 raw |
| `scripts/scan-bench/out/realworld-*.{csv,md,jsonl}` | B 벤치마크 raw (3 run: 초기 실패 + juice-shop 재시도 + webgoat 재시도) |
| `docs/scans/fixture-matrix-2026-05-27.md` | A 보고서 (32 fixture × ecosystem 매트릭스) |
| `docs/scans/realworld-benchmark-2026-05-27.md` | B 보고서 (검출력 + 발견 버그 분석) |
| `docs/post-ga-execution-tracker.md` | **W8 신규 등재** (#46~#49) |

## 1차 결론
### 잘 됨
- 모든 32 fixture 에서 scan pipeline 자체 succeeded (실패 케이스 0).
- npm: Juice Shop v17.0.0 = **1,714 components / 121 CVE** (Critical 24, High 53), 라이선스 94% 분류.
- Python: v1 셀프스캔 = 542 components / 10 CVE (3 high). starlette·python-multipart 업그레이드 베이스라인 확보.
- Trivy DB가 2025–2026 신규 CVE (CVE-2026-24486, CVE-2026-42561 등)도 즉시 매칭 — 갱신 흐름 정상.
- 다국어 fixture (한글 디렉토리) 처리 정상.

### 발견 (W8 신규 트래커)
| # | 우선 | 항목 | 출발 파일 |
|---|---|---|---|
| W8-#46 | **P0** | Maven classifier purl `(component_id, version)` UniqueViolation 수정 | `apps/backend/tasks/scan_source.py:2582` `_get_or_create_component_version` |
| W8-#47 | P1 | zip-bomb 가드 UX (200x ratio가 실 OSS 첫 업로드 차단) | `apps/backend/services/source_archive_service.py:117` `_max_compression_ratio` |
| W8-#48 | P1 | Python 라이선스 메타 보강 (셀프스캔에서 unknown 90%) | cdxgen `--fetch-license` 또는 별도 enrich step |
| W8-#49 | P2 | Ruby / dotnet 라이선스 보강 | cdxgen 12.5 + Bundler/NuGet API 후속 |

### W8-#46 근본 원인 (재현됨)
WebGoat v8.2.2 두 번 시도 모두 동일:
```
psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint
"uq_component_versions_component_version"
DETAIL: Key (component_id, version)=(*, 1.3.1) already exists.
SQL params: purl_with_version='pkg:maven/com.github.jnr/jffi@1.3.1?classifier=native&type=jar'
```
cdxgen이 같은 Maven 아티팩트를 classifier qualifier 차이로 두 개 purl로 emit. `components.purl`은 qualifier 포함하므로 두 row가 별개 components 인데, `_get_or_create_component_version`이 (component_id, version) 만 보고 classifier 무시 → 두 번째 INSERT가 unique 위배.

**영향**: JNI/JFFI/Netty native/Tomcat native/jjs-snappy 등 native classifier 사용 Java 백엔드 OSS는 모두 스캔 실패. v2.4.0 GA 전 머지 권장.

## 다음 세션 — 인테이크 후 W6 종료 + W8-#46 머지
W6은 #43c~#43e + #44 + chore 후속이 남았고, W8-#46이 GA 블로커로 추가됨. 우선순위 제안:

1. **W6 잔여 (백로그 5건)** — 사용자/관리자 문서 교체(#43c) → install/upgrade/Helm 마이그(#43d) → admin/health Trivy DB 패널(#43e) → Trivy DB 라이프사이클(#44) → chore-#42-followup + chore-#43a-doc-drift.
2. **W8-#46 (P0 GA 블로커)** — W6과 독립적이므로 병렬 가능. db-designer + scan-pipeline-specialist Producer-Reviewer 패턴 권장 (`(component_id, version, COALESCE(classifier_qualifier, ''))` unique 키로 expand → backfill → contract).

scan-bench는 결과 모았으므로 종료. v2.4.0 첫 공개 릴리스 직전에 1회 재실행하여 회귀 가드.

## 운영 검증 후속 (당장 할 수 있는 것)
- 셀프스캔 starlette/python-multipart 업그레이드 PR — 별 트랙으로 진행 가능 (실제 자체 deps 보안).
- W8-#46 fix 시 회귀 가드로 `scripts/scan-bench/run_bench.py --suite realworld --only webgoat` 재실행 → succeeded 확인.

## 메모리 업데이트 후보
- `feedback_*`: 별도 추가 사항 없음 (기존 메모리로 커버).
- `project_*`: `project_v21_v23_execution_tracker.md` 본문에 "W8 추가됨" 1줄 첨가 권장.
