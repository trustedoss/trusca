---
id: sbom-upload
title: SBOM 업로드
description: 외부 도구가 이미 생성한 CycloneDX 또는 SPDX SBOM을 업로드하면 TRUSCA가 CVE를 매칭하고 선언 라이선스를 분류하며 적합성을 채점하고 빌드 게이트를 실행하는 스캔을 큐에 넣습니다.
sidebar_label: SBOM 업로드
sidebar_position: 5
---

# SBOM 업로드

다른 도구로 만든 SBOM(software bill of materials, 소프트웨어 구성 명세)이 이미 있습니까? 기존 TRUSCA 프로젝트에 업로드하면 TRUSCA가 소스를 복제하거나 스캔하지 않고도 그 컴포넌트를 취약점 데이터와 매칭하고, 선언 라이선스를 분류하고, 의존성 그래프를 구성하고, SBOM의 적합성을 채점하고, 빌드 게이트를 실행합니다. **CycloneDX-JSON**과 **SPDX**(JSON 또는 Tag-Value)를 모두 받습니다.

엔드포인트는 `POST /v1/projects/{project_id}/sbom-ingest` 입니다. 비동기로 동작합니다. 요청이 성공하면 큐에 들어간 스캔 행과 함께 `202 Accepted`를 반환하므로, 스캔을 폴링해 결과를 확인합니다.

:::note 대상 독자
자체 도구(예: 빌드에서 실행하는 cdxgen)로 CycloneDX JSON SBOM을 생성하고 TRUSCA로 분석하려는 엔지니어와 CI 파이프라인. TRUSCA API Key가 필요합니다 — [API keys](../admin-guide/api-keys.md) 참고.
:::

:::caution Dependency-Track 엔드포인트 아님
TRUSCA는 Dependency-Track API 호환이 **아닙니다**. Dependency-Track 방식 — `X-Api-Key` 헤더와 `autoCreate` 폼 필드, base64 `bom` 필드를 쓰는 `POST /api/v1/bom` — 은 여기서 통하지 않습니다. 아래에 정리한 TRUSCA 엔드포인트와 `Authorization: Bearer` 헤더, multipart 필드를 사용하세요. 프로젝트는 사전에 존재해야 하며 자동 생성은 없습니다.
:::

## 사전 조건

- `tos_<prefix>_<secret>` 형식의 TRUSCA API Key. **/integrations → API keys → New API key**에서 생성하며, 스코프 모델은 [API keys](../admin-guide/api-keys.md) 참고.
- 대상 **프로젝트가 이미 존재**. UUID는 **Project Settings → CI/CD**에서 복사합니다. SBOM 업로드는 프로젝트를 생성하지 않습니다.
- API Key의 스코프가 그 프로젝트를 커버 — 프로젝트에 바인딩된 `project` 스코프 키이거나, 팀이 소유한 프로젝트라면 `team` 스코프 키.
- **CycloneDX-JSON** 문서(지원하는 `specVersion`은 `1.2`부터 `1.6`) **또는** JSON·Tag-Value 형식의 **SPDX** 문서. CVE 매칭에서는 Trivy가 포맷을 자동 감지하고, 컴포넌트 적재를 위해 SPDX는 CycloneDX로 변환됩니다. SPDX RDF/XML은 받지 않습니다.
- 프로젝트에 큐 대기 중이거나 실행 중인 스캔이 없음(프로젝트당 진행 스캔 1개, 두 번째는 `409` 반환).

## SBOM 업로드 방법

문서를 `multipart/form-data`로 보냅니다.

| 필드 | 필수 | 예 | 설명 |
|---|---|---|---|
| `sbom` | 예 | `@bom.cdx.json` | CycloneDX JSON SBOM 파일. |
| `ref` | 아니오 | `main` | SBOM을 생성한 git ref(브랜치명·태그·전체 ref). TRUSCA가 보존 키로 정규화합니다. |
| `release` | 아니오 | `v1.2.3` | 결과 스냅샷에 붙일 릴리스/버전 레이블. |

API Key를 베어러 토큰으로 인증합니다. 헤더는 `Authorization: Bearer <API_KEY>` 이며, `X-Api-Key`가 **아닙니다**.

```bash
curl -X POST \
  https://trustedoss.example.com/v1/projects/<PROJECT_ID>/sbom-ingest \
  -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" \
  -F "sbom=@bom.cdx.json" \
  -F "ref=main" \
  -F "release=v1.2.3"
```

`<PROJECT_ID>`는 프로젝트 UUID로 바꾸고 `TRUSTEDOSS_API_KEY`는 환경에 설정합니다. cdxgen 기반 파이프라인은 빌드 단계에서 `bom.cdx.json`을 생성한 다음 위 명령으로 업로드할 수 있습니다.

성공하면 응답은 큐에 들어간 스캔 행과 함께 `202 Accepted` 입니다.

```json
{
  "id": "3f9a2c10-7b4e-4d2a-9c11-0e8f5d6a1b22",
  "project_id": "<PROJECT_ID>",
  "kind": "sbom",
  "status": "queued",
  "ref": "main",
  "release": "v1.2.3"
}
```

업로드된 SBOM의 `kind`는 항상 `sbom`이고 `status`는 `queued`로 시작합니다. `id`를 보관하세요 — 다음에 폴링할 스캔 id입니다.

## 스캔 완료 확인

같은 베어러 토큰으로 스캔이 최종 상태(`succeeded`·`failed`·`cancelled`)에 도달할 때까지 폴링합니다. [GitHub Actions](./github-actions.md) 연동이 쓰는 폴링 패턴과 동일합니다.

```bash
curl https://trustedoss.example.com/v1/scans/<SCAN_ID> \
  -H "Authorization: Bearer $TRUSTEDOSS_API_KEY"
```

`status`는 `queued → running → succeeded`로 이동합니다. 30초에 한 번 폴링하는 주기가 적당합니다. `status`가 `succeeded`가 되면 포털에서 프로젝트를 열어 컴포넌트·취약점·라이선스를 확인합니다.

## 적합성(conformance) 결과 읽기

SBOM을 업로드하면 TRUSCA는 매칭 이전에(그리고 매칭 여부와 무관하게) SBOM의 **품질**을 정해진 기준으로 채점합니다. 버전·패키지 URL·의존성 그래프가 없는 "껍데기" SBOM이 조용히 빈 결과를 내는 대신 드러나게 하기 위해서입니다. 결과는 다음으로 읽습니다.

```bash
curl -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" \
  https://trustedoss.example.com/v1/projects/<PROJECT_ID>/scans/<SCAN_ID>/conformance
```

응답은 해당 스캔의 채점 결과입니다.

```json
{
  "scan_id": "<SCAN_ID>",
  "project_id": "<PROJECT_ID>",
  "source_format": "cyclonedx",
  "result": "warn",
  "n_fail": 0,
  "n_warn": 1,
  "component_count": 42,
  "purl_coverage_pct": 100,
  "license_coverage_pct": 96,
  "hash_coverage_pct": 0,
  "checks": [
    { "id": "purl", "label": "PURL coverage (>= 90%)", "required": true, "status": "pass", "detail": "100% (42/42)", "missing": [] },
    { "id": "hash", "label": "Hash coverage (>= 50%, recommended)", "required": false, "status": "warn", "detail": "0% (0/42)", "missing": [] }
  ]
}
```

- **`result`**는 `pass`·`warn`·`fail` 중 하나입니다. `fail`은 **필수** 검사가 실패했다는 뜻이고, `warn`은 필수 검사는 모두 통과했으나 **권장** 검사(라이선스 또는 해시 커버리지)가 기준에 못 미친 경우이며, `pass`는 모든 검사를 통과한 경우입니다.
- **필수 검사**: 타임스탬프, 도구 정보, name·version을 가진 최상위 컴포넌트, 컴포넌트 name+version 100%, PURL 커버리지가 `SBOM_CONFORMANCE_PURL_MIN_PCT`(기본 `90`) 이상, `pkg:generic` 자리표시자 없음, 전이 의존성 그래프 존재.
- **권장 검사**(warn만): 라이선스 커버리지가 `SBOM_CONFORMANCE_LICENSE_MIN_PCT`(기본 `80`) 이상, 해시 커버리지가 `SBOM_CONFORMANCE_HASH_MIN_PCT`(기본 `50`) 이상.
- `fail` 결과여도 인제스트를 **중단하지 않습니다** — TRUSCA는 CVE 매칭과 라이선스 분류를 그대로 수행하므로 구체적 사유와 함께 부분 결과를 얻습니다. 공급사의 SBOM을 받아들일지 반려할지 판단하는 근거로 씁니다.
- `purl_coverage_pct`·`license_coverage_pct`·`hash_coverage_pct`는 SPDX Tag-Value 문서에서는 `null`입니다. Tag-Value는 패키지별 커버리지가 아니라 존재 여부로 채점하기 때문입니다.

여기서 `404`는 프로젝트에 접근할 수 없거나, 해당 스캔에 아직 결과가 없다는 뜻입니다(인제스트된 SBOM 스캔이 아니거나, 인제스트가 적합성 단계에 도달하지 않음).

## 동작 확인

스캔이 `succeeded`에 도달한 다음:

- 프로젝트의 **Components** 탭에 SBOM의 패키지가 나열되고 컴포넌트 개수가 0보다 큽니다.
- **Vulnerabilities** 탭에 Trivy가 컴포넌트와 매칭한 CVE(Common Vulnerabilities and Exposures, 공통 취약점·노출) 발견 항목이 표시됩니다.
- **Licenses** 탭에 SBOM이 담은 선언 라이선스가 표시됩니다.
- **Overview** 탭에 의존성 그래프와 프로젝트 리스크 점수가 표시됩니다.

프로젝트에 빌드 게이트 정책이 있으면, 소스 스캔과 똑같이 업로드된 SBOM에도 게이트가 실행됩니다.

## 업로드된 SBOM이 채우는 것

업로드된 SBOM은 생성한 도구가 안에 기록한 내용만 담으므로, TRUSCA가 보강하는 영역과 그렇지 않은 영역이 나뉩니다.

**채워지는 것:**

- 컴포넌트 목록 — SBOM의 모든 컴포넌트.
- 취약점 — Trivy가 PURL로 컴포넌트와 매칭한 CVE 발견 항목.
- 선언 라이선스 — 각 컴포넌트가 SBOM에 선언한 라이선스.
- 의존성 그래프 — SBOM의 `dependencies`로 구성.
- 빌드 게이트 — Critical CVE와 금지 분류 라이선스가 게이트를 발동하므로, 이 엔드포인트를 호출한 다음 게이트를 확인하는 CI 단계는 소스 스캔과 동일하게 빌드를 차단할 수 있습니다.

**채워지지 않는 것(소스/저장소 스캔에서만 나옵니다):**

- 검출 라이선스 — 소스 스캔이 파일 안에서 직접 찾는 라이선스 텍스트(scancode). 업로드된 SBOM은 복제도 스캔도 하지 않으므로 검출할 대상이 없습니다.
- 레지스트리 concluded 라이선스 — 소스 스캔이 레지스트리 메타데이터에서 도출하는 정리된 라이선스.
- SBOM 서명·증명 — 업로드된 SBOM은 서명(cosign)되지 않으므로 서명·인증서·증명 다운로드 엔드포인트가 제공할 대상이 없습니다.
- 소스 보존 — 소스를 가져오거나 보관하지 않습니다.

검출 라이선스·서명·소스 보존이 필요하면 저장소를 대상으로 소스 스캔을 실행하세요 — [Scans](../user-guide/scans.md) 참고.

## 제한

| 제한 | 기본값 | 환경 변수 | 초과 시 |
|---|---|---|---|
| 업로드 용량 | 32 MiB | `SBOM_INGEST_MAX_BYTES` | `413` |
| 컴포넌트 개수 | 50,000 | `SBOM_INGEST_MAX_COMPONENTS` | `422` |

운영자는 배포마다 두 제한을 올리거나 내릴 수 있습니다 — [환경 변수](../reference/env-variables.md) 참고.

## 오류

모든 오류는 RFC 7807(Problem Details for HTTP APIs) 응답이며 `application/problem+json` 콘텐츠 타입을 씁니다.

| 상태 | 발생 시점 |
|---|---|
| `403` | 호출자가 프로젝트 소유 팀의 멤버가 아니거나, project 스코프 API Key가 다른 프로젝트를 가리킴. |
| `404` | 프로젝트가 없거나, 호출자에게 숨겨짐(존재 은닉). |
| `409` | 이 프로젝트에 스캔이 이미 큐 대기 중이거나 실행 중이거나, 프로젝트가 archived 상태. |
| `413` | 업로드가 용량 상한(`SBOM_INGEST_MAX_BYTES`)을 초과. |
| `415` | 업로드의 콘텐츠 타입과 파일명이 모두 잘못됨. `application/json` / `application/vnd.cyclonedx+json` / `application/spdx+json` / `text/spdx`를 쓰거나, `.json` / `.cdx.json` / `.spdx` / `.tag` 파일명을 쓰세요. |
| `422` | 업로드가 유효한 CycloneDX-JSON 또는 SPDX(JSON/Tag-Value) 문서가 아님 — `bomFormat`이 잘못됐거나, 지원하지 않는 CycloneDX `specVersion`이거나, `components`/`packages`가 잘못됐거나, `SBOM_INGEST_MAX_COMPONENTS`보다 많거나, 지나치게 깊게 중첩됨. |
| `429` | 레이트 리밋에 걸렸거나 팀의 동시 스캔 상한에 도달. 응답에 `Retry-After` 헤더가 실립니다. |

## 문제 해결

### `401 Unauthorized`

베어러 토큰이 없거나 형식이 잘못됐거나 만료됐습니다. 헤더가 `Authorization: Bearer <API_KEY>`인지 확인하세요 — TRUSCA는 `X-Api-Key` 헤더를 읽지 않습니다. API Key 모달에서 키를 다시 붙여 넣으세요. 키는 정확히 `tos_` + 8자 + `_` + 32자입니다.

### `403 Forbidden`

API Key의 스코프가 프로젝트를 커버하지 않습니다. 그 프로젝트에 바인딩된 `project` 스코프, 또는 팀이 소유한 프로젝트라면 `team` 스코프로 키를 다시 발급하세요. [API keys](../admin-guide/api-keys.md) 참고.

### `409 Conflict`

이 프로젝트에 스캔이 이미 큐 대기 중이거나 실행 중입니다 — TRUSCA는 프로젝트당 진행 스캔 1개만 허용합니다. 끝날 때까지 기다린 다음(`GET /v1/scans/{scan_id}` 폴링) 다시 시도하세요. 프로젝트가 archived 상태일 때도 `409`가 발생합니다. 먼저 복원하세요.

### `415 Unsupported Media Type`

TRUSCA는 CycloneDX-JSON과 SPDX(JSON 또는 Tag-Value)를 받습니다. 업로드가 허용된 미디어 타입(`application/json`·`application/vnd.cyclonedx+json`·`application/spdx+json`·`text/spdx`)이나 인식되는 파일명(`.json`·`.cdx.json`·`.spdx`·`.tag`)을 설정하는지 확인하세요. SPDX RDF/XML과 CycloneDX XML은 여기서 받지 않습니다.

### `422 Unprocessable Entity`

업로드가 처리할 수 있는 CycloneDX 또는 SPDX SBOM이 아닙니다. CycloneDX는 `bomFormat`이 `CycloneDX`이고 `specVersion`이 `1.2`에서 `1.6` 사이인지, SPDX는 `spdxVersion`(JSON)이나 `SPDXVersion:` 줄(Tag-Value)을 갖는지 확인하세요. 컴포넌트·패키지 개수는 `SBOM_INGEST_MAX_COMPONENTS` 이내여야 하고, 문서가 지나치게 깊게 중첩되면 안 됩니다. `detail` 필드가 구체적 사유를 알려 줍니다.

### `429 Too Many Requests`

사용자별 스캔 생성 레이트 리밋에 걸렸거나 팀이 동시 스캔 상한에 도달했습니다. `Retry-After` 헤더를 따라 명시된 지연 후 다시 시도하세요.

## 더 보기

- [GitHub Actions](./github-actions.md) — 워크플로에서 소스 스캔을 트리거하고 빌드를 게이트
- [API keys](../admin-guide/api-keys.md) — `tos_` 키 형식과 스코프 모델
- [Scans](../user-guide/scans.md) — 소스·컨테이너 스캔, 각각이 채우는 것
- [Scan retention](../admin-guide/scan-retention.md) — `ref`와 `release`로 스캔을 묶고 보존하는 방식
- [환경 변수](../reference/env-variables.md) — 업로드 용량·컴포넌트 제한
