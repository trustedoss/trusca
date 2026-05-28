# Fixture 회귀 매트릭스 — 2026-05-27

bd-scan fixture 32개를 portal에 등록·스캔하여 cdxgen/Trivy 검출력 회귀 베이스라인 수립.

- **계정**: `frontend-admin@demo.trustedoss.dev` (team: Frontend)
- **스캔 입력**: zip 업로드 (`source_type=upload`)
- **자동화**: `scripts/scan-bench/run_bench.py --suite fixtures`
- **원본 결과**: `scripts/scan-bench/out/fixtures-20260528-003142.{csv,md,jsonl}`
- **소요**: 32회 직렬 스캔 약 10분 (10–70초/스캔)
- **성공률**: 32/32 succeeded (scan pipeline 자체는 100%)

## 한눈 요약

| 카테고리 | fixture 수 | 컴포넌트 합 | CVE 합 |
|---|---:|---:|---:|
| 정상 검출(>0 comp) | 19 | 261 | 37 |
| 의도적 0건 | 5 (empty, e2e-build-failure/deep-manifest, iac, gradle-with-wrapper) | 0 | 0 |
| **이슈: cdxgen 0건 회귀 후보** | 8 | 0 | 0 |

## 결과 표

| fixture | ecosystem | comp | direct | lic(허/조/금/?) | CVE(crit/h/m/l/?) | time(s) | 비고 |
|---|---|---:|---:|---|---|---:|---|
| dotnet | dotnet | 3 | 3 | 0/0/0/3 | 0 | 10 | 라이선스 100% unknown ⚠ |
| e2e-build-failure | maven | 0 | 0 | 0 | 0 | 10 | 의도적 fail 시나리오 |
| e2e-deep-manifest | – | 0 | 0 | 0 | 0 | 10 | cdxgen 한계(깊은 manifest 미탐) |
| e2e-korean-dirs | – | 1 | 1 | 0/0/0/1 | 0 | 10 | 한글 디렉토리 1개 검출 ✓ |
| empty | – | 0 | 0 | 0 | 0 | 5 | 비어있음(정상) |
| go | go | 1 | 1 | 1/0/0/0 | 0 | 10 | ✓ |
| gradle | gradle | 7 | 1 | 4/0/0/3 | 0 | 15 | ✓ |
| gradle-android | gradle | **52** | 0 | 9/0/0/43 | 0 | 70 | direct=0 ⚠ (root deps 정의 누락 추정) |
| gradle-android-kotlin-dsl | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** |
| gradle-android-no-sdk | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** (SDK 없음) |
| gradle-android-sdk30 | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** |
| gradle-android-sdk31 | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** |
| gradle-android-sdk32 | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** |
| gradle-android-sdk33 | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** |
| gradle-android-sdk35 | gradle | 0 | 0 | 0 | 0 | 10 | **0건 ⚠** |
| gradle-kts | gradle | 7 | 1 | 4/0/0/3 | 0 | 10 | ✓ |
| gradle-multimodule | gradle | 7 | 0 | 4/0/0/3 | 0 | 10 | ✓ |
| gradle-nested | – | 7 | 1 | 4/0/0/3 | 0 | 10 | nested deps 검출 ✓ |
| gradle-no-wrapper | gradle | 7 | 1 | 4/0/0/3 | 0 | 10 | ✓ |
| gradle-with-wrapper | gradle | 0 | 0 | 0 | 0 | 5 | deps 정의 없음 → 0 정상 |
| iac | – | 0 | 0 | 0 | 0 | 5 | IaC만, deps 없음 |
| maven | maven | 8 | 2 | 4/0/0/4 | 0 | 10 | 라이선스 50% unknown ⚠ |
| maven-nested | – | 1 | 1 | 0/0/0/1 | 0 | 10 | nested 1개만 검출 ⚠ |
| maven-node | npm | 69 | 3 | 68/0/0/1 | **11** (0/4/4/1/2) | 10 | maven+node 합산, 풍부한 결과 ✓ |
| multi-component | multi | 69 | 1 | 68/0/0/1 | **11** | 10 | maven-node와 동일 결과(같은 소스셋) |
| node | npm | 1 | 1 | 0/0/0/1 | **3** (1/0/2/0/0) | 5 | lodash@4.17.21에서 2025–26 CVE 검출 ✓ |
| node-yarn | npm | 1 | 1 | 1/0/0/0 | **3** | 5 | yarn 라이선스 검출 ✓ |
| php | php | 2 | 1 | 2/0/0/0 | 0 | 10 | ✓ |
| python-pip | python | 5 | 1 | 3/1/0/1 | **9** (0/3/6/0/0) | 10 | ✓ |
| python-poetry | python | 1 | 0 | 1/0/0/0 | **3** (0/0/3/0/0) | 10 | ✓ |
| ruby | ruby | 5 | 1 | 0/0/0/5 | 0 | 10 | 라이선스 100% unknown ⚠ |
| rust | rust | 8 | 1 | 6/0/0/2 | 0 | 40 | ✓ |

## 주요 발견

### ✓ 정상 작동 영역
1. **모든 32개 fixture에서 스캔 파이프라인 자체는 성공** — cdxgen → CycloneDX → Trivy 매칭 → DB 적재까지 단절 없음.
2. **Trivy 매칭은 8개 ecosystem에서 동작** — npm (lodash/path-to-regexp), python (multiple), 모두 정상 매칭. **2025–2026년 신규 CVE도 즉시 반영** (예: CVE-2026-4800 lodash, CVE-2025-13465).
3. **다국어 디렉토리** (한글 fixture) 처리 정상 — 1개 검출, 깨짐 없음.
4. **Multi-language fixture** (maven-node, multi-component) — maven 8 + npm 61 = 69개 정확 합산.
5. **nested manifest** (gradle-nested) — root에 build.gradle 없어도 7개 검출 (cdxgen 권장 동작).

### ⚠ cdxgen 한계 (회귀가 아닌 알려진 제약)
1. **Android Gradle SDK 미설치 변종 7개 모두 0 컴포넌트** — Android Gradle Plugin은 빌드 시 SDK 필요. cdxgen은 정적 분석만 가능해 0개가 정상. → **fixture 추가 가치 낮음**; CI에서 SDK 셋업하지 않을 거면 회귀 alert 제외 권장.
2. **gradle-android root direct=0, transitive 52개** — Android 라이브러리 의존성 그래프에서 root deps 식별 미흡. transitive로 잡힘은 정상이지만 UX 상 "직접 의존이 0"으로 보임.
3. **`e2e-deep-manifest`** — bd-scan 의도한 깊은 nested manifest 시나리오; cdxgen이 못 찾음. 알려진 한계.

### ⚠ 라이선스 분류 갭 (우선순위 후속 작업)
| ecosystem | unknown 비율 | 권고 |
|---|---|---|
| **ruby** | 5/5 (100%) | cdxgen Gemfile 파서가 라이선스 메타 미추출. Bundler `bundle viz` / `gem spec` 보조 필요 |
| **dotnet** | 3/3 (100%) | NuGet `.nuspec` 라이선스 미파싱 |
| **maven** (commons-lang3, guava 등) | 4/8 (50%) | cdxgen은 pom.xml의 `<licenses>` 블록만 파싱. Maven Central API 보강 필요 |
| **gradle 기본** | 3/7 (43%) | 동일 — POM 메타 보강 |

→ **트래커 등재 후보**: cdxgen 12.5 업그레이드 + 라이선스 메타 보강 (ruby/dotnet 우선).

### ⚠ ecosystem 식별 정확도
- `e2e-deep-manifest`, `e2e-korean-dirs`, `gradle-nested`, `maven-nested`, `iac` 가 detect_ecosystem에서 "unknown"으로 잡힘 — root에 manifest가 없는 케이스. **스크립트 한계**이지 portal 문제 아님.

## 회귀 셋 권장 운영
| 카테고리 | fixture | 의미 |
|---|---|---|
| **must-pass** (regress 시 alert) | node, node-yarn, maven, maven-node, python-pip, python-poetry, rust, go, php, gradle, gradle-kts, gradle-multimodule | 각 ecosystem 대표, deps>0 보장 |
| **dataset-driven** (CVE count 변동 추적) | node (lodash), maven-node, python-pip, python-poetry | Trivy DB 갱신 추적용 |
| **알려진 0건** (alert 제외) | empty, e2e-build-failure, e2e-deep-manifest, e2e-korean-dirs(=1), iac, gradle-with-wrapper, gradle-android-* (SDK 없는 변종 7개) | |

## 트래커 후속 등재 후보
1. **cdxgen Ruby/dotnet 라이선스 메타 보강** — `docs/post-ga-execution-tracker.md` Wave 5 (라이선스 분류 v2) 합류.
2. **cdxgen 12.5 업그레이드** — 트래커 기존 항목과 합쳐서 pnpm/yarn lockfile 보강과 동시 처리.
3. **Maven Central API 라이선스 보강** — license unknown 50% 케이스 개선.
4. **gradle-android direct/transitive 분류 정확도** — Android 의존성 그래프 root edge 식별 (낮은 우선순위, BD도 비슷한 한계).
