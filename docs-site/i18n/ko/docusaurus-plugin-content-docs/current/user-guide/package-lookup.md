---
id: package-lookup
title: 패키지 조회
description: 패키지를 끌어오기 전에 라이선스와 알려진 보안 권고를 확인하고, 사내에서 이미 쓰고 있는지 봅니다.
sidebar_label: 패키지 조회
sidebar_position: 6
---

# 패키지 조회

[검색](./search.md)은 이미 스캔한 것만 다룹니다. 패키지 조회는 그 앞 단계의 질문에 답합니다: *애초에 이 패키지를 끌어와도 되는가*. 외부 패키지 카탈로그([deps.dev](https://deps.dev))에 직접 물어보므로, 여기서 한 번도 빌드에 닿은 적 없는 패키지도 라이선스와 알려진 보안 권고를 보여줍니다.

명령 팔레트(**⌘K** / **Ctrl+K**, "외부에서 패키지 조회")나 `/packages/lookup`에서 엽니다.

에어갭 배포에서는 관리자가 `EXTERNAL_PACKAGE_LOOKUP_ENABLED=false`로 이 기능을 끌 수 있습니다. 매번 시간 초과되는 조회를 보여주는 대신 진입점 자체가 사라집니다.

## 조회하기

생태계를 고르고 정확한 패키지 이름을 입력한 뒤 조회합니다. 카탈로그는 정확히 일치하는 것만 답하고 부분 일치나 유사 검색은 지원하지 않으므로, 이름을 잘못 쓰거나 생태계를 잘못 고르면 비슷한 이름을 제시하는 대신 찾지 못했다고 답합니다.

npm, PyPI, Maven, Go, crates.io, NuGet 6개 생태계를 지원합니다.

결과에는 패키지의 현재 버전, 라이선스, 알려진 보안 권고 건수와 각 ID가 나타납니다. 여기서 찾지 못했다고 해서 안전하다는 뜻은 아닙니다. 그 정확한 이름과 생태계로는 카탈로그에 기록이 없다는 뜻일 뿐입니다.

## 사내 사용 현황

모든 결과는 그 패키지를 사내 어딘가에서 이미 쓰고 있는지도 함께 알려줍니다. 어떤 프로젝트가, 어떤 버전으로 쓰고 있는지까지 나타납니다. 목록이 비어 있으면 아직 어느 프로젝트도 끌어오지 않았다는 뜻이므로, 이 패키지에 대한 사용 전 신청은 중복이 아닙니다.

이 매칭은 텍스트 검색이 아니라 패키지 식별자(버전 없는 purl)에 대한 정확한 일치입니다. 이름이 비슷한 다른 패키지가 실수로 여기 뜨는 일은 없습니다.

## 사용 신청하기

[사용 전 신청](./approvals.md#intake-requests)이 켜진 배포에서는, 결과에 패키지가 미리 채워진 채로 신청을 여는 버튼이 함께 나타납니다.

## 확인 방법

<!-- docs-uat: id=external-package-lookup-api kind=api auth=viewer url=/v1/external-packages?ecosystem=npm&name=lodash expect=status:200 tier=nightly -->
1. `GET /v1/external-packages?ecosystem=npm&name=lodash`는 `found`, `licenses`, `advisory_count`, `internal_projects`를 포함한 200을 반환합니다.
2. 카탈로그가 모르는 패키지는 오류가 아니라 `found: false`를 담은 200을 반환합니다.
3. 요청 제한: 패키지 조회는 분당 10회, 보안 권고 조회는 분당 20회(둘 다 인증된 사용자 단위).

## 함께 보기

- [검색](./search.md) — 이미 스캔에서 찾은 결과의 CVE·GHSA 보안 권고 상세를 보려면.
- [승인](./approvals.md) — 사용 신청 다음 단계.
