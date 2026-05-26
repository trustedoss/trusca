# P3 #12 — 0-vuln + null Type/Usage 진단 (2026-05-26)

> 본 문서는 사용자 보고 2건 (`/projects/bcf29f0e-3ee5-4f37-b6f4-615f9519707b?tab=vulnerabilities`) 에 대한 근본 원인 분석이다. 코드 변경 1건(소형 픽스)을 포함하며, 큰 변경은 후속 PR로 남긴다.

대상 스캔: `841b6e7b-45ea-4b4c-aa4d-269f46a4400c` (project=`maven-node`, status=succeeded, 2026-05-26 05:45:44).

## 갭 1 — Vulnerabilities 0건

### 사실 관계

- 포털 DB: `vulnerability_findings` 0 rows for the scan
- DT project metrics: `vulnerabilities=0`, `vulnerableComponents=0`, `findingsTotal=0`
- DT 자체 `/api/v1/finding/project/{uuid}`: `[]` (강제 재분석 후에도 동일)
- DT vulnerability mirror: 정상 (NVD 352,109 CVEs, 2026-05-26 01:03 mirror 완료)
- DT가 컴포넌트는 정상 인덱싱: 69개 컴포넌트 모두 `purlCoordinates` + `repositoryMeta` 채워짐 (npm/maven 모두 인식)
- 알려진 취약 버전인 `body-parser@1.20.1`, `express@4.18.2`, `qs@6.11.0` 등을 포함해도 0 매치

### 근본 원인 (가장 결정적)

**DT의 OSS Index 분석기가 HTTP 401 Unauthorized로 실패**한다. 컨테이너 로그:

```
2026-05-26 09:36:35,014 WARN  [OssIndexAnalysisTask] An API username or token has not been specified for use with OSS Index. Using anonymous access
2026-05-26 09:36:35,015 INFO  [OssIndexAnalysisTask] Starting Sonatype OSS Index analysis task
2026-05-26 09:36:35,782 ERROR [OssIndexAnalysisTask] HTTP Status : 401 Unauthorized
2026-05-26 09:36:35,783 ERROR [OssIndexAnalysisTask]  - Analyzer URL : https://ossindex.sonatype.org
2026-05-26 09:36:35,790 INFO  [OssIndexAnalysisTask] Sonatype OSS Index analysis complete
```

- DT 분석기 구성:
  - `scanner.internal.enabled = true` — CPE 매칭 (NVD 미러), npm purl 매칭 ❌ (NVD CVE 데이터에 npm CPE가 없음)
  - `scanner.npmaudit.enabled = true` — npm 전용, npm 한정 매칭
  - `scanner.ossindex.enabled = true` — purl 매칭의 메인 엔진, Sonatype hosted ❗ **401 거부**
  - `scanner.snyk.enabled = false`
  - `scanner.trivy.enabled = false`
- 결과: npm/maven 컴포넌트 매칭이 OSS Index에 100% 의존하는데 Sonatype이 anonymous를 막아 0 매치.
- Black Duck는 자체 KB를 들고 있어 영향 없음. DT 기반 SCA는 모두 영향.

### 근본 원인 (보조)

**포털의 DT findings 폴 예산이 너무 짧다.** 코드: `apps/backend/tasks/scan_source.py:1944`
```
_DT_FINDINGS_POLL_DELAYS_SECONDS: tuple[int, ...] = (2, 4, 8, 16, 30)  # 합계 60s
```

실측: 이 스캔의 DT `lastBomImport=05:46:38`, `lastVulnerabilityAnalysis=06:20:09`. **DT 분석에 33.5분 걸렸지만 포털은 60초만 폴**. OSS Index가 살아 있어도 60초 안에 끝나지 않으면 0 findings로 마감된다.

비고: DT의 `BOM_UPLOAD_ANALYSIS` 이벤트는 token 기반이라 `GET /api/v1/event/token/{token}` 으로 `processing=false`를 확인할 수 있다. 현재 코드는 token 결과를 **버린다** (`upload_sbom` 이 token을 리턴은 하지만 `_poll_dt_findings_with_retry` 가 사용 안 함).

### 권장 픽스

1. **(운영 - 무코드)** `scanner.ossindex.api.username` + `scanner.ossindex.api.token` 환경에 OSS Index API token 등록. Sonatype 회원가입 후 무료 발급. anonymous 폐지 이후 사실상 필수.
2. **(소형 코드, 본 PR 포함)** Poll 예산을 60s → 9.5분(보수적)으로 늘림. 본 PR 의 단일 코드 변경. token-based wait 는 후속 (DTClient surface 변경 필요).
3. **(중형 코드, 후속 PR)** DT BOM upload 가 리턴한 token 으로 `event/token` 을 폴해서 DT 분석이 끝났는지 확인한 뒤 findings 를 가져오기. token 폴 자체는 가벼우니 (수 ms 응답) 길게 잡아도 비용이 없다. cleaner UX (clean 프로젝트도 빨리 마감) + 정확성 향상.
4. **(중형 코드, 후속 PR)** DT가 OSS Index 401을 emit하면 portal 측에 audit/notification 을 띄움. 현재는 사용자가 0 vulns 만 보고 끝.
5. **(대형 코드, 후속 PR)** DT 의존 단일점이라는 점이 드러났다. 옵션: OSV API 직접 조회 (purl 기반, GoogleOSV), Trivy db 매칭 (purl→CVE), Sonatype Lifecycle 같은 상용 백업. CLAUDE.md DT-only 결정과 충돌하므로 별도 의사결정 필요.

## 갭 2 — Type/Usage 컬럼 거의 NULL

### 사실 관계

DB 쿼리:
```sql
SELECT direct, dependency_scope, COUNT(*)
FROM scan_components
WHERE scan_id = '841b6e7b-45ea-4b4c-aa4d-269f46a4400c'
GROUP BY direct, dependency_scope;
-- direct=false, scope=NULL      : 68
-- direct=false, scope=required  : 1   (commons-lang3)
```

스키마: `scan_components.direct` (bool, default false), `dependency_scope` (varchar nullable). UI Type 컬럼 = `direct ? "direct" : "transitive"`, Usage = `dependency_scope` 직접 노출. 모두 NULL/default.

`depth` 도 NULL 100%, `component_dependency_edges` 0 rows.

### 근본 원인 #1 — cdxgen은 npm 컴포넌트에 `scope` 를 emit 하지 않는다

cdxgen 12.3.3 + 본 fixture(`maven-node`) 직접 실행 결과:
```
With scope: 1 ['commons-lang3']
```
Maven은 POM `<scope>` 가 있으니 cdxgen이 채우지만, npm은 source가 없다 (`package.json` 의 `dependencies` vs `devDependencies` 구분은 가능하나 cdxgen은 노출하지 않음). 즉 **npm 프로젝트는 영구히 scope NULL**이 정상 동작.

UI 표시: 현재 `-` 로 dash 출력. "이 스캔에는 dependency scope 정보가 없음"을 더 명확히 알려야 하지만 BE 데이터 갭은 아니다. **frontend 영역 후속**.

### 근본 원인 #2 — Dependency graph 가 영구히 비어 있음

조사: 본 코드 베이스에 `_persist_dependency_graph` (apps/backend/tasks/scan_source.py:2198) 가 cdxgen `dependencies` 배열을 파싱하여 `depth` + `direct` + `component_dependency_edges` 를 채우게 되어 있다 (PR #154, merged 2026-05-24).

실측:
- 최근 20개 succeeded 스캔 전수 조사: **comps_with_depth=0, edges=0** (전 스캔 0). 시스템 전반 누락.
- `dependency_graph_persisted` 로그가 모든 worker log 에 단 한 번도 발생한 적 없음.
- 동일 fixture에 cdxgen 을 새로 돌려 본 SBOM에는 71개 dependency entries가 있음 (메타데이터 component → maven root → commons-lang3, 그리고 분리된 npm root → express → 67개 transitive). `graph_depths_from_sbom` 을 그대로 호출해 69 depths/3 direct/125 edges 정상 산출.
- worker 내부에서 **이 SBOM dict 를 그대로** `_persist_components` → `_persist_dependency_graph` 에 전달하면 정상 동작 확인 (직접 repro: with_depth=69, direct=3, edges=125).

→ 즉 **코드는 정상**. 실제 production 스캔에서는 `cdxgen_result.sbom["dependencies"]` 가 **빈 배열 또는 키가 없다**고 추정. early-return at `if not adjacency: return`.

### 가능한 가설

- **가설 A**: cdxgen 12.3.3 이 부분적으로 분석 실패하면 `components` 는 채우면서 `dependencies` 는 비워서 emit. 본 fixture는 잘 됐지만 외부 네트워크 실패 / Maven dependency:list 실패 / npm install 실패 시 부분 결과를 낼 수 있다.
- **가설 B**: DT 가 업로드받은 SBOM 의 `dependencies` 를 자체적으로 rebuild 하는데, 우리가 본 DT의 70-entry `dependencies` 는 cdxgen 원본이 아니라 DT 가 재구성한 결과. 즉 cdxgen 원본은 빈 배열이었을 수 있다.
- **가설 C**: workspace 가 cleanup 되기 전 cdxgen SBOM 파일을 다른 task 가 손대지만, 본 코드베이스에서 그런 path 는 없음 (`_sanitize_sbom_hashes_for_dt` 는 bytes 작업, dict 변형 안 함).

### 권장 픽스

1. **(소형 코드, 본 PR 포함)** `_persist_dependency_graph` 의 early-return 시 **WARNING 로그** 를 emit. `cdxgen_result.sbom` 에 `dependencies` 가 비었거나 없을 때 가시화 → 다음 스캔에서 운영자가 즉시 발견 가능. 현재는 silent.
2. **(소형 코드, 본 PR 포함)** cdxgen SBOM artifact 의 `dependencies_count` 를 `cdxgen_succeeded` 로그에 추가 → 향후 매번 cdxgen 출력 품질을 메트릭으로 추적.
3. **(중형 코드, 후속 PR)** SBOM 의 `dependencies` 가 비어 있으면 ORT analyzer cross-merge 또는 npm/maven 별 fallback parser (`package-lock.json` → 직접 그래프 추출, `mvn dependency:tree` → tree parser) 로 graph 를 보완. cdxgen 만 의지하지 말고 lockfile-direct.
4. **(중형 코드, 후속 PR)** UI에서 `direct/transitive/usage` 가 모두 NULL인 컴포넌트 다수면 banner: "이 스캔은 dependency graph 정보를 수집하지 못했습니다. scope/usage는 표시되지 않습니다." 현재 dash 만으로는 운영자가 "버그인지 정상인지" 구분 불가.

## 본 PR 의 변경 사항

`fix(P3 #12)` 본 PR 은 진단 위주의 **저위험 패치 3건만** 포함한다:

1. `apps/backend/tasks/scan_source.py` — `_DT_FINDINGS_POLL_DELAYS_SECONDS` 60s → 570s (~9.5 분) 확장. DT 분석기 ramp-up 을 더 기다린다. token-based wait 는 후속 (DTClient surface 변경 필요).
2. `apps/backend/tasks/scan_source.py` — `_persist_dependency_graph` 의 early-return 자리에 `dependency_graph_missing` WARNING 로그 추가. 다음 스캔부터 SBOM 누락이 즉시 가시화.
3. `apps/backend/integrations/cdxgen.py` — `cdxgen_succeeded` 로그에 `dependencies_count` 추가. 매 스캔 cdxgen 산출물 품질 추적.
4. 본 진단 문서 (`docs/diagnose/p3-12-vulns-type-usage-2026-05-26.md`).

후속 PR로 미루는 것:
- DT BOM token-based wait (DTClient API 변경, integration test 필요)
- OSS Index 401 처리 / Alert 발생
- ORT cross-merge 또는 lockfile-direct fallback graph extractor
- UI banner "graph 미수집"

운영 액션 (코드 외):
- DT `scanner.ossindex.api.username` + `scanner.ossindex.api.token` 등록 → npm/maven vuln 매칭 즉시 회복.
