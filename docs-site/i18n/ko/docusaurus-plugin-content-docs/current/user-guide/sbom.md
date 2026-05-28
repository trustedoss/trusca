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
3. 4개 포맷 버튼 중 하나(CycloneDX JSON, CycloneDX XML, SPDX JSON, SPDX Tag-Value)를 클릭하여 다운로드.

![SBOM 탭 — 4개 포맷 다운로드 버튼(CycloneDX JSON/XML, SPDX JSON/Tag-Value)](/img/screenshots/user-sbom-format-buttons.png)

파일명은 `sbom-<project-slug>.<ext>`.

## API에서 다운로드

```bash
# CycloneDX JSON
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/sbom?format=cyclonedx-json"

# SPDX JSON
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/sbom?format=spdx-json"
```

`format` 허용값: `cyclonedx-json`, `cyclonedx-xml`, `spdx-json`, `spdx-tv`.

엔드포인트는 항상 **가장 최근에 성공한 스캔(latest succeeded)** 의 SBOM을 내보냅니다. 특정 과거 스캔 ID로 고정하는 기능은 로드맵 항목입니다.

:::caution 감사 증거 — 스캔을 외부에서 고정하세요
SBOM 내보내기는 항상 최신 성공 스캔을 반영합니다. 외부 감사관은
보통 특정 릴리스 시점의 SBOM 을 요청합니다(예: "2026-01-15 에
출고된 것은 무엇인가?"). 과거 스캔 고정() 이 적용되기 전까지는
각 릴리스 경계에서 SBOM 산출물을 캡처하여 릴리스 아카이브에
보관하세요. 포털은 *현재* SBOM 으로 다루되 *과거* SBOM 의 출처로
보지 마세요.
:::

## NOTICE 파일

Apache-2.0 §4(d)와 유사한 attribution 의무 이행을 위해 포털은 프로젝트 최신 스캔으로부터 NOTICE attribution 본문을 자동 생성합니다.

파일 내용:

- 프로젝트 이름과 스캔 타임스탬프 헤더.
- 컴포넌트별: 이름, 버전, 라이선스, 저작권 문구(스캔이 수집한 경우), 상위 라이선스 텍스트 링크.
- 라이선스별로 그룹화하여 재배포 패키지 작성을 단순화.

### 지원 포맷

NOTICE 엔드포인트는 `format` 쿼리 값을 받습니다(기본 `text`).

| 포맷 | 쿼리 값 (`format=`) | MIME | 확장자 | 사용 사례 |
|---|---|---|---|---|
| **일반 텍스트** | `text` | `text/plain` | `.txt` | 릴리스 tarball의 `NOTICE` 파일로 그대로 사용. 기본값. |
| **Markdown** | `markdown` | `text/markdown` | `.md` | 문서 사이트나 PR 설명에 렌더링. |
| **HTML** | `html` | `text/html` | `.html` | attribution 페이지용 self-contained 문서(인라인 `<style>`, 스크립트 없음). |

출력은 동일 스캔·동일 포맷에 대해 내보내기 간 byte-stable이며 릴리스 간 diff가 가능합니다.

### 다운로드

- **UI:** 프로젝트 → **Obligations** 탭 → 포맷 선택(**text** 또는 **HTML**) → **Download NOTICE**. 브라우저가 `NOTICE-<project>.<ext>`로 저장합니다. markdown 변형은 API에서 제공됩니다.
- **API:**

  ```bash
  # 일반 텍스트(기본)
  curl -sS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/notice?format=text&download=true"

  # HTML
  curl -sS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/notice?format=html&download=true"
  ```

  `format` 허용값: `text`, `markdown`, `html`. `download=true`를 전달하면 응답에 `Content-Disposition: attachment`가 설정되고 `-OJ`가 서버 제공 파일명(`NOTICE-<project>.<ext>`)으로 저장합니다. 생략하면 본문을 인라인으로 스트리밍합니다.

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

### 성공한 스캔이 아직 없을 때의 빈 SBOM

프로젝트에 아직 성공한 스캔이 없는 경우에도 내보내기는 빈 `components`/`packages` 리스트를 가진 유효한 SBOM 문서(HTTP 200)를 반환합니다. 다운스트림 도구가 그대로 파싱할 수 있습니다.

### `/sbom?format=…` 호출 시 `422`

쿼리 문자열이 API가 받지 않는 값을 사용했습니다. 위 표의 4가지 정식 쿼리 값 중 하나를 사용하세요 — 특히 **SPDX Tag-Value 포맷의 값은 `spdx-tv`(이며 `spdx-tag-value`가 아닙니다)**.

### NOTICE에 일부 컴포넌트의 저작권이 누락

저작권 문구는 패키지 메타데이터와 라이선스 헤더에서 옵니다. 일부 패키지는 이를 생략하므로 NOTICE 항목이 "Copyright holder unspecified"로 표시됩니다.

## 컴플라이언스 증거 체인 {#compliance-evidence-trail}

외부 감사관이 포털 운영자에게 묻는 전형적인 다섯 가지 질문입니다.
오늘 답할 수 있는 것과 우회가 필요한 것을 정리한 표입니다.

| 감사관 질문 | v0.10.0 답변 소스 | 한계 |
|------------|----------------|------|
| "릴리스 X 시점의 SBOM 을 보여달라" | 수동 아카이브; 포털은 최신본만 보존 | 과거 스캔 고정은 로드맵 |
| "지난 분기에 누가 SBOM / NOTICE 를 다운로드했나?" | `structlog`(Loki / journald) — `audit_logs` 아님 | 감사 행 승격은 로드맵 |
| "프로젝트 X 에서 GPL 이 처음 탐지된 시점은?" | `scans.create` 의 `audit_logs` + 스캔별 `vulnerability_findings.create` | 가능 — 전체 증거 체인 보유 |
| "2026 Q1 의 모든 승인 결정을 보여달라" | `component_approvals.update` 의 `audit_logs` + `decision_note` | 가능 — 전체 증거 체인 보유 |
| "감사 행이 변조되지 않았음을 증명하라" | append-only 트리거(마이그레이션 0012) | super-admin 우회 잔존 — [감사 로그 강화](../admin-guide/audit-log.md#스키마) 검토 필요 |

## 로드맵

매뉴얼이 이전에 약속했으나 v0.10.0에 포함되지 않은 항목.

- **취약점 PDF 보고서**는 v0.10.0에 _이미 구현_되어 있습니다 — [취약점 → PDF 보고서 다운로드](./vulnerabilities.md#pdf-보고서-다운로드)(`GET /v1/projects/{id}/vulnerability-report.pdf`) 참고. 아직 **구현되지 않은** 것은 **Excel** 보고서(컴포넌트 Excel, 취약점 Excel)와 **컴플라이언스 PDF**입니다. 이들을 위한 `/v1/projects/{id}/reports/...` 엔드포인트는 없으며 향후 릴리스에서 제공됩니다. 표 형태가 즉시 필요한 이해관계자는 SBOM(CycloneDX JSON)을 선호 도구로 소비하세요.
- NOTICE 조립을 위한 컴포넌트 드로어의 수동 저작권 오버라이드 — 예정.
- SBOM·NOTICE 내보내기의 과거 스캔 고정 — 예정.
- SBOM / NOTICE 다운로드를 `structlog` 이벤트에서 `audit_logs` 행으로 승격 — 예정.

## 함께 보기

- [SBOM 서명 검증 (cosign)](../reference/sbom-signature-verification.md) — SBOM이 온전하며 이 배포에서 서명되었음을 증명
- [컴포넌트·라이선스](./components-and-licenses.md)
- [취약점](./vulnerabilities.md)
- [API 개요](../reference/api-overview.md)
