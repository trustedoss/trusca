---
title: bd-scan fixtures e2e 스캔 결과 — 생태계별 컴포넌트/취약점 검출률
date: 2026-05-22
method: zip 업로드 풀 스캔 (cdxgen → scancode → DT), DT NVD 26만 CVE
related_pr: 94 (cdxgen --no-validate / DT hash sanitize / workspace 공유 볼륨)
---

# bd-scan fixtures e2e 스캔 결과 (2026-05-22)

## 환경
- 대상: `~/projects/bd-scan/tests/fixtures/projects` 32개.
- 방식: 각 fixture를 zip 업로드 → source 스캔(cdxgen→scancode→DT). 실제 사용자 흐름 e2e.
- DT: dependency-track 4.13.2, NVD 미러 263,721 CVE(기존 동기화분; NVD 1.1 feed 종료로 신규 미러는 실패하지만 매칭은 동작).
- 전제 fix(PR #94): cdxgen `--no-validate`(schema 실패 시 SBOM 드롭 버그), DT BOM hash sanitize(base64 hash 400 거부), backend↔worker workspace 공유 볼륨(zip archive 전달).

## 결과 — 32/32 status=succeeded (파이프라인 안정)

| fixture | components | vulns | 판정 |
|---------|----------:|------:|------|
| maven-node | 69 | 12 | ✅ |
| multi-component | 69 | 12 | ✅ |
| gradle-android | 52 | 0 | ✅ |
| maven | 8 | 1 | ✅ |
| rust | 8 | 0 | ✅ |
| python-pip | 5 | 0 | ✅ |
| ruby | 5 | 0 | ✅ |
| dotnet | 3 | 0 | ✅ |
| php | 2 | 0 | ✅ |
| go | 1 | 0 | ✅ |
| node | 1 | 3 | ✅ |
| maven-nested | 1 | 1 | ✅ |
| e2e-korean-dirs | 1 | 1 | ✅ (한글 경로 OK) |
| empty | 0 | 0 | ⚪ 의도적 빈 |
| e2e-build-failure | 0 | 0 | ⚪ 빌드 실패 fixture |
| iac | 0 | 0 | ⚪ IaC(컴포넌트 개념 다름) |
| **gradle** | **0** | 0 | ❌ 검출 갭 |
| **gradle-kts** | **0** | 0 | ❌ |
| **gradle-multimodule** | **0** | 0 | ❌ |
| **gradle-nested** | **0** | 0 | ❌ |
| **gradle-no-wrapper** | **0** | 0 | ❌ |
| **gradle-with-wrapper** | **0** | 0 | ❌ |
| **gradle-android-kotlin-dsl** | **0** | 0 | ❌ |
| **gradle-android-no-sdk** | **0** | 0 | ❌ |
| **gradle-android-sdk30/31/32/33/35** | **0** | 0 | ❌ (5개) |
| **node-yarn** | **0** | 0 | ❌ yarn.lock 미검출 |
| **python-poetry** | **0** | 0 | ❌ poetry.lock 미검출 |
| **e2e-deep-manifest** | **0** | 0 | ❌ deep manifest 미검출 |

## 핵심 발견

1. **파이프라인 안정**: 32/32 전부 succeeded. zip 업로드 + cdxgen + scancode + DT 매칭이 다양한 생태계에서 깨지지 않음.
2. **transitive 검출 작동**: gradle-android 52, maven-node 69, multi-component 69 — direct만으로는 안 나오는 대량 transitive를 cdxgen이 lockfile 기반으로 풀어냄.
3. **취약점 매칭 작동**: maven-node/multi-component 12, node 3, maven/maven-nested/e2e-korean-dirs 각 1. DT 기존 NVD 데이터로 매칭됨.
4. **한글 경로 OK**: e2e-korean-dirs 정상(1 comp/1 vuln).

## cdxgen 생태계별 검출 갭 (§2.4 보강 우선순위)

| 갭 | 규모 | 추정 원인 |
|----|------|-----------|
| **gradle 계열** | 12개 중 gradle-android(52)만 OK, **11개 0** | cdxgen이 gradle 빌드 resolve를 대부분 못 함(wrapper/멀티모듈/kts/SDK 변형). prep에서 `gradle dependencies` 또는 cdxgen gradle 플러그인 보강 필요 |
| **yarn** | node-yarn 0 (node=1은 OK) | cdxgen이 yarn.lock 파싱 실패 |
| **poetry** | python-poetry 0 (python-pip=5는 OK) | cdxgen이 poetry.lock 파싱 실패 |
| **deep-manifest** | e2e-deep-manifest 0 | 깊은 매니페스트 구조 미탐색 |

→ 우선순위: **gradle(최대 영향) > yarn > poetry > deep-manifest**. prep 단계(`tasks/scan_source.py`) + cdxgen 옵션/플러그인으로 보강.

## 비고
- 측정 도구: `/tmp/scan_fixture.sh`(단일), `/tmp/batch_scan.sh`(32 배치). 재사용 시 `scripts/` 또는 테스트로 승격 권장.
- 0건 중 empty/e2e-build-failure/iac는 의도적/예상 범위. 나머지(gradle 11 + yarn + poetry + deep-manifest = 14개)가 실제 검출 갭.
