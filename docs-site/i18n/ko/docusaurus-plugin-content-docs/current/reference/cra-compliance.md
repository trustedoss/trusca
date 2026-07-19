---
id: cra-compliance
title: EU 사이버 복원력법(CRA) — TRUSCA가 돕는 방식
description: EU 사이버 복원력법(Regulation (EU) 2024/2847)의 취약점 처리·신고 의무를 TRUSCA의 실제 기능(SBOM, VEX, 조치, 악용 신호)에 정직하게 대응시키고, 한계를 분명히 밝힌다.
sidebar_label: CRA 대응
---

# EU 사이버 복원력법(CRA) — TRUSCA가 돕는 방식

:::note 대상 독자
CRA의 취약점 처리 의무를 TRUSCA가 어떻게 뒷받침하는지 판단하려는 보안 리드,
제품보안팀, 법무·컴플라이언스 담당자. 이 문서는 규정 문구를 실제 기능에
대응시키고, [비교 문서](../comparison.md)와 같은 정직한 태도로 한계를 함께 밝힌다.
:::

:::warning 법률 자문이 아니며 컴플라이언스 인증도 아니다
TRUSCA는 CRA 프로그램의 일부를 뒷받침하는 도구다. TRUSCA를 쓴다고 해서 제품이
CRA를 준수하게 되는 것은 아니며, 이 문서는 법률 자문이 아니다. 준수 여부는
조직의 프로세스·문서·제품이 갖추는 성질이므로, 규정 원문을 근거로 자격을 갖춘
법률 전문가와 함께 평가한다.
:::

## CRA가 요구하는 것(SCA 도구가 관여하는 범위)

사이버 복원력법(Regulation (EU) 2024/2847)은 EU 시장에 출시되는 디지털 요소
포함 제품에 보안 요구사항을 부과한다. 이 가운데 SCA·SBOM 포털이 도울 수 있는
부분은 두 곳이다.

- Annex I Part II — 취약점 처리 요구사항. 구성요소와 취약점을 식별·문서화하고
  (통용되는 기계 판독 형식의 SBOM 포함), 지체 없이 조치하며, 정기적으로 시험하고,
  취약점 정보를 공유한다.
- Article 14 — 신고 의무. 악용이 확인된 취약점과 중대한 사고를 조율 CSIRT와
  ENISA에 촉박한 기한 안에 통지한다(초기 경보는 24시간 이내).

아래 대응표는 이 두 곳만 다룬다. 설계 단계 보안 요구사항(Annex I Part I),
업데이트 배포 인프라, 규제 신고를 실제로 제출하는 행위는 스캐너가 아니라
제품과 조직의 책임이다.

## 의무 → TRUSCA 기능 대응

| CRA 의무(Annex I Part II / Art. 14) | TRUSCA가 돕는 방식 | 위치 |
|---|---|---|
| §1 — 구성요소와 취약점을 식별·문서화하고, 통용되는 기계 판독 형식의 SBOM을 포함한다 | cdxgen이 30종 이상 생태계에서 구성요소를 탐지하고 Trivy가 이를 CVE와 대조한다. SBOM 수출은 CycloneDX와 SPDX를 byte-stable하게 생성한다. | [SBOM 수출](../user-guide/sbom.md), [구성요소](../user-guide/components-and-licenses.md) |
| §2 — 취약점을 지체 없이 조치한다 | 각 발견 항목은 수정 버전 정보를 담고, 조치 시뮬레이션과 풀 리퀘스트 생성이 업그레이드 경로를 제안한다. CI 빌드 게이트는 Critical CVE·금지 라이선스에서 빌드를 실패시켜(`exit 1`) 회귀가 출하되지 않게 한다. 조치 소요시간 추적의 한계는 아래를 참고한다. | [조치 PR](./remediation-pull-request.md), [시뮬레이션](./remediation-dry-run.md) |
| §3 — 정기적으로 보안 시험·검토를 수행한다 | 예약 스캔에 더해 자동 재매칭이 동작한다. Trivy DB가 갱신되면 Celery beat가 기존 SBOM을 다시 스캔하므로, 새로 공개된 CVE가 재업로드 없이 드러난다. | [데이터 출처](./data-sources.md), [스캔](../user-guide/scans.md) |
| §4 / §6 — 취약점 정보를 공유하고 악용 가능성을 표기한다 | VEX 반입·수출이 발견 항목별 악용 가능성 상태(`not_affected`, `under_investigation` 등)를 CycloneDX와 SPDX로 전달하므로, 하류 소비자가 원시 CVE 잡음 대신 정확한 상태를 받는다. | [VEX](../user-guide/vex.md) |
| Art. 14 — 악용이 확인된 취약점을 식별한다 | KEV 표시(CISA Known Exploited Vulnerabilities)는 실제 악용이 확인된 사실을 표시하고, EPSS는 악용 가능성을 점수화한다. 24시간 신고의 근거가 될 발견 항목을 가려내는 두 신호다. TRUSCA는 신호를 드러낼 뿐 신고서를 제출하지는 않는다. | [데이터 출처](./data-sources.md), [분류](../user-guide/triage.md) |
| 증거와 감사 추적 | 모든 쓰기 작업이 감사 로그에 기록되고(검색·CSV 수출), 보고서는 Excel·PDF로 수출해 증거 묶음을 만든다. | [감사 로그](../admin-guide/audit-log.md) |

## 한계 — TRUSCA가 대신해 주지 않는 것

먼저 정직하게 밝힌다. CRA는 프로세스와 제품 전반을 다루는 규제이고, 스캐너는
그 일부만 담당한다.

- 조치 소요시간·SLA 추적은 아직 정식 기능이 아니다. TRUSCA는 취약점을 찾고
  우선순위를 매기는 데는 도움을 주지만, 각 발견 항목이 조치 기한 대비 얼마나
  오래 열려 있었는지는 아직 추적하지 않는다. "지체 없이"라는 SLA는 현재 포털
  밖에서 관리해야 한다. 발견 항목의 경과시간과 SLA 초과를 정식으로 추적하는
  기능은 로드맵에 있다.
- 규제 신고서를 제출하지 않는다. KEV·EPSS로 악용이 확인된 취약점을 식별하는
  것과 24시간 안에 ENISA·CSIRT에 통지하는 것은 별개다. 그 절차는 조직의 몫이다.
- 보안 권고문을 게시하지 않으며, 조율된 취약점 공개(CVD) 절차를 대신 운영하지
  않는다. TRUSCA 자체의 공개 채널은
  [`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md)에
  있으나, 제품에는 제품 고유의 채널이 필요하다.
- 업데이트를 배포하지 않는다. 보안 패치를 사용자에게 안전하고 신속하며 무상으로
  전달하는 일은 제품의 책임이다.
- 설계 단계 보안(Annex I Part I)은 범위 밖이다. 그것은 제품의 공학적 성질이지,
  의존성 스캐너가 단언하는 대상이 아니다.

## 실무 점검표

CRA 프로그램 안에서 TRUSCA를 실용적으로 쓰는 방법이다.

1. 릴리즈마다 SBOM(CycloneDX 또는 SPDX)을 생성해 보관하고, 이를 문서화된
   구성요소 인벤토리로 삼는다.
2. CI 빌드 게이트를 켜서 Critical CVE·금지 라이선스가 출하되지 못하게 하고,
   Trivy DB 갱신과 자동 재매칭을 계속 돌려 출하된 SBOM에 대한 새 CVE가 자동으로
   드러나게 한다.
3. KEV와 EPSS로 분류해, Article 14 신고 기한을 좌우하는 악용 확인 항목을
   놓치지 않는다.
4. 악용 가능성 판단을 VEX로 기록해, 하류 소비자와 감사자가 원시 CVE 개수가
   아니라 정확한 상태를 보게 한다.
5. 감사 로그와 보고서 수출을 증거 추적으로 유지한다.
6. 조치 기한(조치 소요시간 SLA)은 당분간 조직의 트래커에서 관리한다. 위
   로드맵 항목을 참고한다.

## 참고

- [Regulation (EU) 2024/2847 (사이버 복원력법)](https://eur-lex.europa.eu/eli/reg/2024/2847/oj)
- [TRUSCA 비교](../comparison.md) — 정직한 기능 기준선
- [취약점 데이터 출처](./data-sources.md) — KEV·EPSS의 출처
- [VEX](../user-guide/vex.md), [SBOM 수출](../user-guide/sbom.md)
