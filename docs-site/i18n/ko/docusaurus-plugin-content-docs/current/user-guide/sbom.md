---
id: sbom
title: SBOM
description: TrustedOSS Portal에서 CycloneDX(JSON·XML)와 SPDX(JSON·Tag-Value) SBOM을 내보내고 NOTICE 파일을 생성합니다.
sidebar_label: SBOM
sidebar_position: 5
---

# SBOM

포털은 가장 최근 성공 스캔으로부터 **Software Bill of Materials**(SBOM) 산출물을 생성합니다. 4가지 교환 포맷과 attribution `NOTICE` 파일을 지원합니다.

![프로젝트 상세 — 포맷 선택기와 마지막 스캔 요약이 있는 SBOM 탭](/img/screenshots/user-sbom-tab.png)

:::note 대상 독자
릴리스를 출고하는 엔지니어, 산출물을 제출하는 컴플라이언스 리드, [EO 14028](https://www.cisa.gov/topics/cyber-threats-and-advisories/cybersecurity-best-practices/secure-by-design/sbom)에 따라 SBOM 요청을 처리하는 고객. 팀 멤버십 기반 읽기 권한.
:::

## 지원 포맷

| 포맷 | 쿼리 값 (`format=`) | MIME | 사용 사례 |
|---|---|---|---|
| **CycloneDX 1.6 (JSON)** | `cyclonedx-json` | `application/vnd.cyclonedx+json` | SCA 도구의 사실상 표준. VEX 포함. |
| **CycloneDX 1.6 (XML)** | `cyclonedx-xml` | `application/vnd.cyclonedx+xml` | 동일 데이터; 레거시 도구를 위한 XML. |
| **SPDX 2.3 (JSON)** | `spdx-json` | `application/spdx+json` | NTIA 최소 요소; 규제 산업에서 폭넓게 수용. |
| **SPDX 2.3 (Tag-Value)** | `spdx-tv` | `text/spdx` | 원래의 SPDX 라인 기반 포맷. |

두 포맷 모두 동일한 내부 모델에서 생성되므로 컴포넌트 목록은 (포맷별 필드 제외) 동일합니다.

## Byte-stable 출력

4가지 내보내기 모두 **byte-stable**입니다 — 같은 스캔을 다시 내보내면 동일 바이트가 생성됩니다. diff·서명·캐싱이 단순해집니다.

byte-stability 달성 방법:

- 컴포넌트를 `purl`(사전식)로 정렬.
- 각 컴포넌트 내 라이선스 표현을 알파벳순 정렬.
- `serialNumber`(CycloneDX) / `documentNamespace`(SPDX)를 `(project_id, scan_id)` 기반 결정적 값으로 고정.
- 본문에서 타임스탬프를 제외(SBOM 메타데이터에는 스캔 종료 시각이 기록되며 스캔당 안정적).

## UI에서 다운로드

1. 프로젝트 열기.
2. **SBOM** 탭 클릭.
3. 드롭다운에서 포맷 선택.
4. **Download** 클릭.

파일명은 `<project-name>-<scan-finished-iso>.sbom.<ext>`.

## API에서 다운로드

```bash
# CycloneDX JSON
curl -sS -L -OJ \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/sbom?format=cyclonedx-json"

# SPDX JSON
curl -sS -L -OJ \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/sbom?format=spdx-json"
```

`format` 허용값: `cyclonedx-json`, `cyclonedx-xml`, `spdx-json`, `spdx-tv`.

기본적으로 내보내기는 프로젝트의 **가장 최근 성공 스캔**을 반영합니다. 특정 스캔으로 고정하려면 `?scan_id=<uuid>`를 전달하세요.

## NOTICE 파일

Apache-2.0 §4(d)와 유사한 attribution 의무 이행을 위해 포털은 프로젝트 최신 스캔으로부터 `NOTICE.txt`를 자동 생성합니다.

파일 내용:

- 프로젝트 이름과 스캔 타임스탬프 헤더.
- 컴포넌트별: 이름, 버전, 라이선스, 저작권 문구(ORT가 추출한 경우), 상위 라이선스 텍스트 링크.
- 라이선스별로 그룹화하여 재배포 패키지 작성을 단순화.

### 다운로드

- **UI:** 프로젝트 → **Obligations** 탭 → **Download NOTICE**.
- **API:**

  ```bash
  curl -sS -L -OJ \
    -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/notice"
  ```

`NOTICE` 파일은 내보내기 간 byte-stable — 릴리스 간 diff 가능합니다.

## VEX 내보내기

CycloneDX SBOM은 모든 결과의 프로젝트 VEX 상태를 포함합니다. SPDX는 native VEX 표현이 없으므로 SPDX 내보내기는 결과별 상태를 생략합니다. 다운스트림 소비자가 기대하면 SPDX 내보내기와 별도 CycloneDX VEX 문서를 함께 제공하세요.

VEX 상태와 CycloneDX `analysis.state` 매핑:

| 포털 상태 | CycloneDX VEX `state` | `justification` |
|---|---|---|
| `New` | `in_triage` | (없음) |
| `Analyzing` | `in_triage` | 분석가 노트 |
| `Exploitable` | `exploitable` | 분석가 노트 |
| `Not affected` | `not_affected` | 사유 텍스트(`code_not_present`, `vulnerable_code_not_in_execute_path` 등) |
| `False positive` | `false_positive` | 분석가 노트 |
| `Suppressed` | `not_affected` | 사유 텍스트 |
| `Fixed` | `resolved` | (`fix_version` 채움) |

## 정상 동작 확인

1. 다운로드된 SBOM이 검증기를 통과 — CycloneDX는 [`cyclonedx validate`](https://github.com/CycloneDX/cyclonedx-cli) 실행:

   ```bash
   cyclonedx validate --input-file checkout-service.sbom.json
   ```

2. SPDX는 [`spdx-tools`](https://github.com/spdx/tools-python)로 검증:

   ```bash
   pyspdxtools -i checkout-service.sbom.json
   ```

3. 같은 스캔을 다시 다운로드하면 byte 동일 파일 생성:

   ```bash
   sha256sum checkout-service.sbom.json checkout-service.sbom.json.again
   # → 동일 해시
   ```

## 트러블슈팅

### `/sbom` 호출 시 `404`

프로젝트에 성공 스캔이 아직 없습니다. 스캔을 트리거하세요 — [스캔](./scans.md) 참고.

### `/sbom?format=…` 호출 시 `422`

쿼리 문자열이 API가 받지 않는 값을 사용했습니다. 위 표의 4가지 정식 쿼리 값 중 하나를 사용하세요 — 특히 **SPDX Tag-Value 포맷의 값은 `spdx-tv`(이며 `spdx-tag-value`가 아닙니다)**.

### NOTICE에 일부 컴포넌트의 저작권이 누락

ORT는 라이선스 헤더에서 저작권을 추출합니다. 일부 패키지는 이를 생략하므로 NOTICE 항목이 "Copyright holder unspecified"로 표시됩니다.

## 로드맵 (v2.x)

매뉴얼이 이전에 약속했으나 v2.0.0에 포함되지 않은 항목.

- Excel·PDF 보고서 — 컴포넌트 Excel, 취약점 Excel, 컴플라이언스 PDF — 는 v2.0.0에 구현되지 않았습니다. **Reports** 메뉴와 `/v1/projects/{id}/reports/...` 엔드포인트는 향후 릴리스에서 제공됩니다. 표 형태가 즉시 필요한 이해관계자는 SBOM(CycloneDX JSON)을 선호 도구로 소비하세요.
- NOTICE 조립을 위한 컴포넌트 드로어의 수동 저작권 오버라이드 — v2.2 예정.

## 함께 보기

- [컴포넌트·라이선스](./components-and-licenses.md)
- [취약점](./vulnerabilities.md)
- [API 개요](../reference/api-overview.md)
