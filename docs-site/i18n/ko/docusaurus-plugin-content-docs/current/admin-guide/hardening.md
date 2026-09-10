---
id: hardening
title: 프로덕션 배포 하드닝
description: 프로덕션 트래픽을 받기 전 TRUSCA를 점검하는 체크리스트입니다. TLS, 인증, 비밀 관리, 레이트 리밋, 데이터 보호, 공급망을 다룹니다.
sidebar_label: 하드닝
sidebar_position: 1.5
---

# 프로덕션 배포 하드닝

:::note 대상 독자
프로덕션 트래픽을 앞두고 배포를 준비하거나 기존 배포를 점검하는 `super_admin` 운영자. `.env` / Helm 값, 그리고 이 페이지가 반복하지 않는 [환경 변수 참조](../reference/env-variables.md)에 익숙해야 합니다.
:::

하드닝된 TRUSCA 배포에 필요한 것 대부분은 이미 기본값으로 갖춰져 있습니다. 비밀번호 정책, 리프레시 토큰 회전, CORS 검증, 레이트 리밋 모두 설정 파일을 건드리기 전부터 켜져 있습니다. 이 페이지는 실제로 운영자가 손대야 하는 설정만 영역별로 짚고, 각 항목을 깊이 다루는 문서로 연결합니다. 새 배포를 세울 때나 기존 배포를 점검할 때 훑어보는 순서로 쓰고, 참조 페이지를 대신하는 문서로는 쓰지 마십시오.

## 네트워크와 TLS

- **`CORS_ALLOWED_ORIGINS`**는 관대한 기본값이 없는 필수 키입니다. `*`가 들어 있으면(자격 증명을 포함한 요청과 함께 쓸 수 없음) 백엔드가 부팅을 거부하고, `APP_ENV=prod`일 때 origin 중 하나라도 `http://`를 쓰면 역시 부팅을 거부합니다. SPA가 실제로 서비스되는 scheme과 host를 정확히 적으십시오. 그보다 넓게 잡지 마십시오.
- 엣지에서 TLS를 종료하십시오. 프로덕션 `docker-compose.yml`은 `DOMAIN`과 `TLS_EMAIL`로 구성되는 Let's Encrypt HTTP-01 챌린지와 함께 Traefik을 포함합니다 - [Docker Compose로 설치](../installation/docker-compose.md)를 참고하십시오. Helm 차트는 대신 Ingress + cert-manager `ClusterIssuer`를 기대합니다 - [Kubernetes에 Helm으로 설치](../installation/helm.md)를 참고하십시오.
- 네트워크가 사내 인증 기관이나 TLS를 가로채는 프록시 뒤에 있다면 인증서 검증을 끄지 마십시오. 대신 스캔 파이프라인과 포털 자체의 외부 호출이 여러분의 CA 번들을 보게 하십시오 - [사설 인증 기관](./private-ca.md)을 참고하십시오.

## 인증과 세션

- **비밀번호 정책은 설정 항목이 아닙니다.** 모든 비밀번호는 등록과 재설정 경로마다 스키마 계층에서 8자리 NIST 800-63B 하한과 흔한 비밀번호 차단 목록 검사를 통과해야 합니다. 여기서 더 강화할 것도 없고, 약화시킬 방법도 없습니다.
- **`SECRET_KEY`**는 모든 JWT에 서명합니다. 필수 키이며, `dev` 외 환경에서 32자 미만이면 부팅을 거부합니다. `openssl rand -hex 32`로 생성하고 배포마다 다른 값을 쓰십시오 - 교체하면 발급된 모든 리프레시 토큰이 무효화되므로, 교체는 일상적인 작업이 아니라 미리 알리는 의도적인 이벤트로 다루십시오.
- **`ACCESS_TOKEN_EXPIRE_MINUTES`**(기본 30)와 **`REFRESH_TOKEN_EXPIRE_DAYS`**(기본 7)는 탈취된 토큰이 쓸모 있는 기간을 정합니다. 리프레시 토큰은 사용할 때마다 회전하고 재사용 탐지가 이미 내장돼 있습니다. 기본값을 줄이면 편의를 노출 구간과 맞바꾸는 것일 뿐, 없던 취약점을 메우는 것이 아닙니다.
- **MFA**(TOTP + 복구 코드)는 모든 사용자가 본인 계정에서 직접 켤 수 있습니다. 이번 릴리스에는 조직 전체에 강제하는 토글이 없습니다. `super_admin`을 비롯한 권한이 높은 계정이 스스로 등록하도록 권장하십시오.
- **`AUTH_SELF_REGISTRATION`**은 기본값이 `true`입니다(공개 가입 폼이 열려 있습니다). 내부 배포라면 이를 끄고 대신 ID 공급자나 일괄 등록으로 계정을 발급하십시오 - 자체 가입과 SSO를 동시에 열어 두면 누군가 회사 주소로 가입한 뒤 나중에 SSO로 그 계정을 연결할 수 있습니다. `OIDC_GROUP_ROLE_MAP`이 `super_admin` 부여를 거부하는 이유와 issuer가 반드시 `https`여야 하는 이유를 포함한 전체 그림은 [환경 변수 참조의 SSO 절](../reference/env-variables.md)을 참고하십시오.
- **`LOGIN_THROTTLE_ENABLED`**(기본 켜짐)는 IP당 레이트 리밋 위에 주소별로 점점 길어지는 잠금을 추가로 겁니다. 여러 출발지에 걸쳐 흩어진 추측 시도도 한 계정을 노리면 걸립니다. 돌아오는 길은 비밀번호 재설정이나 관리자의 잠금 해제 엔드포인트뿐입니다 - [환경 변수 참조의 `LOGIN_THROTTLE_WINDOWS`](../reference/env-variables.md)를 참고하십시오.
- **`PERMISSION_CACHE_TTL_SECONDS`**는 기본값이 `0`(꺼짐)입니다. 모든 요청이 호출자의 역할과 멤버십을 매번 다시 읽습니다. 값을 올리면 그 보장을 DB 부하 감소와 맞바꾸는 것이며, 설정한 값만큼이 강등이나 비활성화가 반영되지 않을 수 있는 최대 시간이 됩니다. [Postgres 크기 산정과 커넥션 튜닝](./postgres-tuning.md)의 커넥션 예산이 실제로 여유가 필요할 때만 올리십시오.

## 레이트 리밋과 남용 방지

CPU를 쓰거나 세션을 여는 공개 엔드포인트에는 처음부터 `slowapi` 레이트 리밋이 걸려 있습니다 - 등록, 로그인, 리프레시, 비밀번호 재설정, 티켓 상태 새로 고침, 그룹 검색, 스캔 트리거, 두 웹훅 수신 엔드포인트까지 전부입니다. 기본값을 포함한 전체 목록은 [환경 변수 참조](../reference/env-variables.md)에 있습니다. 다시 살펴볼 가치가 있는 것은 트래픽 패턴이 브라우저 앞의 사용자 한 명과 다른 경우입니다.

- CI 러너나 NAT 뒤의 사무실 네트워크가 여러 사용자를 대신해 한 주소에서 스캔을 트리거하거나 토큰을 새로 고치면, 남용을 겨냥한 IP당 제한에 걸릴 수 있습니다. 이럴 때는 제한을 끄는 대신 해당 항목의 값을 올리십시오.
- **`RATELIMIT_DISABLED`**는 테스트 전용 비상 스위치입니다(공유 IP를 쓰는 CI 러너가 e2e 스위트를 돌리다 로그인 제한에 걸리지 않게 하려고 존재합니다). API나 관리 화면에는 이 값을 바꿀 방법이 없으므로, 프로덕션 프로세스에 이 값이 들어가는 경로는 우연히 섞여 들어간 환경 변수뿐입니다 - 만약 그렇게 설정되면 백엔드의 모든 `slowapi` 제한이 한 번에 꺼집니다. 실제 트래픽을 받는 배포에는 이 값이 비어 있는지 확인하십시오.
- **`WEBHOOK_MAX_BODY_BYTES`**와 **`WEBHOOK_RATE_LIMIT`**은 서명 검증 전에 인증되지 않은 호출자가 부담시킬 수 있는 비용을 제한합니다. 두 수신 엔드포인트 모두 서명을 검증하기 전에 본문을 읽어 버퍼링하기 때문입니다. [웹훅](../ci-integration/webhooks.md)을 참고하십시오.
- **`WEBSOCKET_MAX_CONNECTIONS_PER_USER`** / **`WEBSOCKET_MAX_CONNECTIONS_GLOBAL`**은 WebSocket 게이트웨이를 같은 방식으로 제한합니다. 공유 레지스트리를 기준으로 강제하므로 백엔드 프로세스 수와 무관하게 상한이 유지됩니다.
- 팀별 **스캔 동시 실행 상한**은 사용자별 트리거 제한과 별개로 한 팀의 버스트가 공유 Celery 워커 풀을 독점하지 못하게 막습니다 - 공식과 산정 방법은 [Docker Compose - 스캔 용량](../installation/docker-compose.md#scan-capacity-sizing-and-scaling)을, 사용자별 절반은 환경 변수 참조의 `SCAN_TRIGGER_RATE_LIMIT`을 참고하십시오.

## 비밀, 키, 최소 권한

- **`GITHUB_APP_ENCRYPTION_KEY`**는 이 배포가 저장하는 모든 비밀을 암호화합니다. 이름과 달리 GitHub App 개인키와 웹훅 시크릿, 프로젝트별 git 자격 증명, 사설 레지스트리 비밀번호, 프로젝트 웹훅 시크릿까지 전부 걸립니다. 첫 부팅 전에 생성해 두고, 급하게 교체해야 하는 순간이 오기 전에 [암호화 키 교체](./encryption-key-rotation.md)를 미리 읽어 두십시오.
- cdxgen용 사설 레지스트리 자격 증명(Maven `settings.xml`, `.npmrc`, `pip.conf`, `.netrc`)은 호스트 디렉터리(`REGISTRY_CONFIG_HOST_PATH`)에서 읽기 전용으로 마운트되며, `.env`에 평문으로 들어가지 않습니다. [의존성 해석용 사설 레지스트리](./private-registries.md)를 참고하십시오.
- API 키는 해시(HMAC) 처리되고, 범위가 지정되며, 서비스 계정이나 CI 통합별로 독립적으로 폐기할 수 있습니다 - 발급과 회전은 [API 키](./api-keys.md)를 참고하고, CI 파이프라인끼리 키 하나를 공유하기보다 통합마다 별도 키를 쓰십시오.
- GitHub App 연결은 팀마다 등록하며, 개인키는 저장 시 암호화되고 접근 범위는 App이 기술적으로 닿을 수 있는 모든 저장소가 아니라 설치(installation) 단위로 좁혀집니다 - [GitHub App 연결](./github-app.md)을 참고하십시오.
- Postgres 자체는 L1 런타임/오너 역할 분리(백엔드와 Celery 런타임은 `DATABASE_URL_APP`, 마이그레이션 전용으로 `DATABASE_URL_OWNER`)를 통해 백엔드 프로세스가 뚫려도 자신의 데이터베이스에 대해 DDL을 실행할 수 없게 합니다. 옵트인 기능입니다(강제하려면 `REQUIRE_DB_ROLE_SEPARATION=true`, 기본은 꺼짐이며 단일 역할이 기본 설치의 단순한 구성과 맞기 때문입니다) - 배포 방식에 따라 [Docker Compose로 설치](../installation/docker-compose.md) 또는 [Kubernetes에 Helm으로 설치](../installation/helm.md)의 역할 분리 설명을 참고하십시오.
- 백업은 기본적으로 평문 SQL입니다. 보관 정책상 저장 시 암호화가 필요하면 [백업과 복원 - 암호화된 백업](./backup-and-restore.md)을 참고하십시오.
- 삭제 요청(right to erasure)은 `super_admin` 두 명의 승인을 거쳐 감사 기록은 남긴 채 사용자의 개인 데이터를 익명화하는 절차를 따릅니다 - [사용자 익명화](./user-anonymisation.md)를 참고하십시오.
- **`/metrics`**는 기본적으로 꺼져 있습니다(`METRICS_ENABLED=false`, 꺼져 있음을 신호로 노출하지 않도록 403이 아니라 404로 응답합니다). 이를 켜고 그 엔드포인트가 사내 네트워크 밖에서도 닿는다면 **`METRICS_TOKEN`**을 설정하십시오 - 토큰이 비어 있으면 그 경로에 닿을 수 있는 누구나 스크레이핑할 수 있습니다.

## 데이터 보호와 외부 호출(egress)

몇몇 보강 단계는 설계상 공개 레지스트리나 피드를 호출합니다. 그중 어느 것도 기본값에서는 패키지 이름, 에코시스템, 권고 id 이상을 보내지 않습니다. 소스 코드나 사내 메타데이터는 전송하지 않으며, 각각에 에어갭 배포나 네트워크가 제한된 배포를 위한 끄기 옵션이 있습니다.

- `SCANOSS_ENABLED`(소스 트리에 통째로 복사해 넣은 OSS 코드를 핑거프린트로 식별)는 기본이 **꺼짐**입니다. 여기서 유일하게 이름 이상을 보내는 단계이므로 옵트아웃이 아니라 옵트인입니다.
- `LICENSE_FETCH_ENABLED`와 `EXTERNAL_PACKAGE_LOOKUP_ENABLED`(레지스트리 라이선스 조회와 deps.dev 카탈로그 검색)는 기본이 **켜짐**이며, 각각 고정된 공개 호스트에 패키지 식별자만 보냅니다.
- `EOL_REFRESH_ENABLED`는 기본이 **꺼짐**입니다(릴리스에 함께 담긴 로컬 end-of-life 스냅샷은 이 값 없이도 동작합니다). `KEV_REFRESH_ENABLED`와 `MALICIOUS_REFRESH_ENABLED`도 각각 CISA KEV와 알려진 악성 패키지 피드에 대해 같은 방식으로 동작합니다.

일반적인 인터넷 연결 설치에서는 이 값들을 건드릴 필요가 없습니다. 공개 인터넷에 전혀 닿아서는 안 되는 배포라면, Trivy DB 자체를 미러링하는 방법까지 함께 다루는 [취약점 데이터 - Air-gapped 운영](./vulnerability-data.md#air-gapped)을 참고하십시오.

## 공급망과 패치

- CI 빌드 게이트는 Critical CVE와 금지된 라이선스가 있으면 무조건 빌드를 막고, 기본값에서는 알려진 악성 패키지가 있어도 막습니다(`GATE_MALICIOUS_ENABLED=true`) - 악성 패키지에는 올라갈 정직한 버전이 없으므로, 다른 `GATE_*` 항목을 느슨하게 조정하더라도 이 항목만은 켜진 채로 둡니다. 선택적인 EPSS 임계값(`GATE_EPSS_THRESHOLD`)을 더하면 악용 가능성을 네 번째 기준으로 추가할 수 있습니다. [빌드 게이트 참조](../reference/glossary.md)를 참고하십시오.
- About 화면(그리고 켜져 있다면 `/metrics`)은 실행 중인 이미지의 버전, 커밋, 빌드 시각을 보여 줍니다. 호스트가 실제로 릴리스된 패치를 받았는지, 아니면 예전에 캐시된 이미지를 그대로 쓰고 있는지 확인하는 가장 빠른 방법입니다.
- 컨테이너 이미지는 아직 cosign으로 서명되지 않고, 릴리스 태그도 아직 GPG로 서명되지 않습니다. 이 기능이 나오기 전까지는 커밋 SHA와 릴리스 워크플로가 출력하는 이미지 다이제스트로 릴리스를 검증하십시오. 전체 공개 정책과 현재 검증 방법은 [`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md)를, 패치 릴리스를 얼마나 자주 적용할지는 [업그레이드 주기](../best-practices/upgrade-cadence.md)를 참고하십시오.

## 정상 동작 확인

- `docker-compose -f docker-compose.yml logs --tail=50 backend | grep backend_starting`로 백엔드가 여러분의 `.env`로 부팅을 통과했는지 확인하십시오 - 거부된 `CORS_ALLOWED_ORIGINS`나 길이가 모자란 `SECRET_KEY`는 성능이 떨어진 채로 뜨는 대신 프로세스 자체를 크래시시키므로, 컨테이너가 떠 있다는 것 자체가 두 검사를 통과했다는 뜻입니다.
- 개발자 도구를 켠 브라우저에서 포털의 공개 URL을 열어, API 응답의 `Access-Control-Allow-Origin`이 `*`가 아니라 여러분의 정확한 origin으로 찍히는지 확인하십시오.
- `curl -I https://<your-domain>/metrics`는 일부러 켜지 않는 한 `404`를 돌려주고, `METRICS_TOKEN`을 설정한 채 토큰 없이 호출하면 본문 대신 `401`/`403`을 돌려줍니다.
- 실습용 환경에서 `/auth/register`에 뻔히 약한 비밀번호(`password123`)로 가입을 시도해 422로 거부되는지 확인하십시오.

## 트러블슈팅

### `.env`를 고친 직후 백엔드가 바로 종료됩니다

부팅 로그에서 구체적인 `RuntimeError` 메시지를 확인하십시오 - 와일드카드 CORS origin, `prod`에서 쓴 `http://` origin, 32자 미만 `SECRET_KEY` 모두 더 약한 상태로 뜨는 대신 의도적으로 빠르게 실패합니다. [검증](../reference/env-variables.md)을 참고하십시오.

### CI 러너나 공유 IP 사무실이 실제 공격자라면 닿지 않을 레이트 리밋에 걸립니다

`RATELIMIT_DISABLED`로 손대지 말고 해당 엔드포인트의 구체적인 `*_RATE_LIMIT` 환경 변수를 올리십시오 - 그 변수는 백엔드 프로세스 전체보다 좁은 범위가 없습니다.

### `GITHUB_APP_ENCRYPTION_KEY`가 실제로 무엇을 덮는지 헷갈립니다

GitHub App 키만이 아니라 암호화된 컬럼 전부입니다. 정확한 목록은 [암호화 키 교체](./encryption-key-rotation.md)를 참고하십시오.

## 함께 보기

- [환경 변수](../reference/env-variables.md) - 이 페이지가 추려낸 원본, 키별 전체 참조.
- [Postgres 크기 산정과 커넥션 튜닝](./postgres-tuning.md) - 프로덕션급 배포의 나머지 절반인 커넥션 예산.
- [암호화 키 교체](./encryption-key-rotation.md)
- [사설 인증 기관](./private-ca.md)
- [의존성 해석용 사설 레지스트리](./private-registries.md)
- [API 키](./api-keys.md)
- [GitHub App 연결](./github-app.md)
- [사용자 익명화](./user-anonymisation.md)
- [백업과 복원](./backup-and-restore.md)
- [온콜 런북](./oncall-runbook.md)
