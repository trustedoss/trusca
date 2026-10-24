---
id: private-ca
title: 사설 인증기관
description: 사내 인증기관이나 TLS를 가로채는 프록시를 쓰는 환경에서 스캔과 데이터 갱신이 동작하게 합니다.
sidebar_label: 사설 인증기관
sidebar_position: 7
---

# 사설 인증기관

조직이 자체 인증기관을 운영하거나, TLS를 끊고 다시 서명하는 프록시를 두고 있다면,
포털이 그 인증기관을 신뢰하기 전까지 바깥으로 나가는 연결이 검증에 실패합니다.
대개 저장소를 내려받는 단계에서 스캔이 멈추거나, 취약점 데이터 갱신이 되지 않는
증상으로 나타납니다.

이 문서는 그 경우를 다룹니다. 바깥으로 나가는 연결 자체가 없는
[망 분리 환경](./vulnerability-data.md#air-gapped)과는 다릅니다.

## 두 단계이고 순서가 있습니다

먼저 인증서를 컨테이너 안에 넣고, 그다음 도구들이 그 파일을 보게 합니다.

하나만 해서는 동작하지 않습니다. 두 번째가 틀리기 쉬운 쪽인데, 도구마다 읽는
변수가 다르고 같은 변수라도 도구마다 뜻이 다르기 때문입니다.

## 인증서를 넣습니다

backend, 두 워커, beat 컨테이너에 읽기 전용으로 마운트합니다. Docker
Compose는 `docker-compose.yml` 옆에 덮어쓰기 파일을 두면 됩니다.

```yaml
# docker-compose.override.yml
services:
  backend:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
  worker-scan:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
  worker-default:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
  beat:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
```

Compose가 `docker-compose.override.yml`을 자동으로 읽으므로, 업그레이드가
`docker-compose.yml`을 교체해도 마운트 설정은 그대로 남습니다.
`bomlens-engine` 프로필을 쓴다면 `worker-engine`에도 같은 항목을 추가합니다.
이 서비스는 그 프로필로 시작하지 않으면 실행되지 않습니다.

이 파일에는 사내 인증기관과 공용 루트 인증서를 함께 이어 붙여 둡니다. 이유는
다음 절에 있습니다.

```bash
cat /path/to/corp-root-ca.pem /etc/ssl/certs/ca-certificates.crt \
  > /etc/ssl/corp/ca-bundle.pem
```

### Helm으로 설치했다면

Secret을 직접 만들고, 차트가 그것을 마운트하고 변수를 설정하게 합니다.

```bash
kubectl create secret generic corp-ca --from-file=ca-bundle.pem=/etc/ssl/corp/ca-bundle.pem
```

```yaml
env:
  extraVolumes:
    - name: corp-ca
      secret:
        secretName: corp-ca
  extraVolumeMounts:
    - name: corp-ca
      mountPath: /etc/ssl/corp-ca.pem
      subPath: ca-bundle.pem
      readOnly: true
  extraEnv:
    SSL_CERT_FILE: /etc/ssl/corp-ca.pem
    NODE_EXTRA_CA_CERTS: /etc/ssl/corp-ca.pem
    REQUESTS_CA_BUNDLE: /etc/ssl/corp-ca.pem
    GIT_SSL_CAINFO: /etc/ssl/corp-ca.pem
```

`subPath`가 있어야 인증서가 그 경로에 파일로 올라옵니다. 없으면 디렉터리가
덮어써서 위 변수들이 가리키는 파일이 존재하지 않습니다. 마운트는 backend와
beat와 워커 둘에 붙고, 바깥으로 나가지 않는 frontend와 Redis에는 붙지 않습니다.

번들 규칙은 같습니다. 사내 인증기관과 공용 루트 인증서를 한 파일에 담습니다.
이유는 다음 절에 있습니다.

## 도구들이 그 파일을 보게 합니다

`.env`에 다음을 넣습니다.

```bash
SSL_CERT_FILE=/etc/ssl/corp-ca.pem
NODE_EXTRA_CA_CERTS=/etc/ssl/corp-ca.pem
REQUESTS_CA_BUNDLE=/etc/ssl/corp-ca.pem
GIT_SSL_CAINFO=/etc/ssl/corp-ca.pem
```

`docker-compose.yml`이 이 값들을 backend, 워커들, beat에 전달하며, 바꾼
뒤에는 `docker-compose up -d`로 다시 시작하면 반영됩니다. `docker-compose`를
실행하는 셸에서 export한 값도 컨테이너에 전달되며, `.env`의 값보다 우선합니다.
두 곳 어디에도 없는 변수는 빈 값으로 설정되지 않고 컨테이너에서 빠집니다. 이
차이가 중요한
이유는, `GIT_SSL_CAINFO`가 빈 값이면 모든 저장소 내려받기가 실패하고
`SSL_CERT_FILE`이 빈 값이면 포털이 어떤 인증기관도 신뢰하지 않게 되기
때문입니다.

파일 하나에 이름이 넷인 이유는 도구마다 읽는 것이 다르기 때문입니다. 넷을 다
설정하는 것이 짧은 답이고, 아래 표는 파이프라인의 일부만 실패할 때 어느 것이
빠졌는지 찾기 위한 것입니다.

| 변수 | 읽는 쪽 | 효과 |
|---|---|---|
| `SSL_CERT_FILE` | Trivy, cosign, govulncheck, 그리고 포털 자신의 HTTPS 호출 | Go 도구에서는 더해집니다. 시스템 디렉터리를 계속 읽기 때문입니다. 포털 자신에게는 갈아치우기입니다. |
| `SSL_CERT_DIR` | 위와 같은 도구들 | 해시 이름이 붙은 인증서 디렉터리입니다. 파일 대신 쓸 수 있습니다. |
| `NODE_EXTRA_CA_CERTS` | cdxgen | 더해집니다. 내장 루트 인증서가 그대로 남습니다. |
| `REQUESTS_CA_BUNDLE` | scancode, scanoss | 이 둘에는 더해집니다. 포털 자신의 호출은 이 변수를 보지 않습니다. |
| `GIT_SSL_CAINFO` | `git clone` | 더해집니다. git은 `SSL_CERT_FILE`도 `CURL_CA_BUNDLE`도 읽지 않습니다. |
| `GIT_SSL_CAPATH` | `git clone` | 위 항목의 디렉터리 형태입니다. |

두 줄은 다시 읽어 볼 만합니다.

먼저 `SSL_CERT_FILE`은 포털 자신의 연결에서 신뢰 집합을 통째로 갈아치웁니다.
취약점 데이터 갱신, 라이선스 조회, Slack과 Teams 알림, GitHub와 GitLab 호출,
티켓 웹훅이 모두 하나의 HTTP 클라이언트를 지나가는데, 그 클라이언트는 이 파일만
읽어 신뢰 집합을 만듭니다. 사내 인증기관만 담긴 파일을 가리키면 공용 endpoint가
전부 검증되지 않고, 그동안 Trivy와 cdxgen은 시스템 저장소를 계속 보기 때문에
정상으로 남습니다. 그래서 인증서 문제가 아니라 데이터 갱신 장애처럼 보입니다.
위처럼 공용 루트 인증서를 같은 파일에 이어 붙이면 생기지 않습니다.

다음으로 `git clone`은 자기 변수 둘만 읽습니다. 다른 단계는 되는데 저장소를
내려받는 단계만 실패한다면 빠진 것은 `GIT_SSL_CAINFO`입니다.

## 프록시를 거쳐 나가는 경우 {#outbound-proxy}

프록시 설정은 `.env`에 넣지 않습니다. `docker-compose.yml`은 프록시 변수를
`.env`에서 전달하지 않습니다. 대신 `docker-compose`를 실행하는 사용자의 Docker
클라이언트 설정 파일 `~/.docker/config.json`에 `proxies` 항목을 둡니다.

```json
{
  "proxies": {
    "default": {
      "httpProxy": "http://proxy.corp.example:3128",
      "httpsProxy": "http://proxy.corp.example:3128",
      "noProxy": "localhost,127.0.0.1,backend,frontend,postgres,redis,docker-socket-proxy,.corp.example"
    }
  }
}
```

Compose는 이 값을 자기가 만드는 모든 컨테이너에 `HTTP_PROXY`, `HTTPS_PROXY`,
`NO_PROXY`와 그 소문자 이름으로 함께 넣습니다. 두 가지가 모두 필요한 이유는
도구마다 읽는 쪽이 다르기 때문입니다. `git clone`은 libcurl을 거치는데,
libcurl은 대문자 `HTTP_PROXY`를 무시하고 `http_proxy`만 읽습니다. Python으로
만든 포털 자신의 호출과 scancode, scanoss는 소문자를 먼저 봅니다. Trivy, cosign
같은 Go 도구는 대문자를 먼저 봅니다. 이 파일을 바꿔도 이미 있는 컨테이너에는
반영되지 않으므로 `docker-compose up -d --force-recreate`로 다시 만듭니다.

프록시 설정을 배포 디렉터리에 함께 두고 싶다면 `docker-compose.override.yml`에
같은 여섯 이름을 값과 함께 적습니다.

```yaml
# docker-compose.override.yml
x-proxy-env: &proxy-env
  HTTP_PROXY: http://proxy.corp.example:3128
  HTTPS_PROXY: http://proxy.corp.example:3128
  NO_PROXY: localhost,127.0.0.1,backend,frontend,postgres,redis,docker-socket-proxy,.corp.example
  http_proxy: http://proxy.corp.example:3128
  https_proxy: http://proxy.corp.example:3128
  no_proxy: localhost,127.0.0.1,backend,frontend,postgres,redis,docker-socket-proxy,.corp.example

services:
  backend:
    environment: *proxy-env
  worker-scan:
    environment: *proxy-env
  worker-default:
    environment: *proxy-env
  beat:
    environment: *proxy-env
```

Compose는 이 값을 각 서비스의 기존 환경에 더하며, 기존 환경을 대체하지
않습니다. 인증서 단계에서 만든 덮어쓰기 파일이 이미 있다면 그 파일의 서비스에
`environment` 항목을 추가합니다.

`NO_PROXY`는 스택 안의 연결이 프록시로 가지 않게 합니다. 예시의 서비스 이름은
서로 통신하는 컨테이너들이고, `docker-socket-proxy`는 `bomlens-engine` 프로필을
쓸 때만 필요합니다. 프록시가 닿지 못하는 사내 Git 호스트, 레지스트리, 패키지
미러도 추가합니다. 앞에 점을 붙이면 도메인 전체가 해당됩니다.

TLS를 다시 서명하는 프록시라면 그 인증기관도 필요하며, 이 문서의 나머지 부분이
그 설정입니다. Helm에서는 같은 여섯 이름을 `env.extraEnv`에 넣습니다.

## 잘 됐는지 확인합니다

API와 각 워커와 beat가 각자 자기 신뢰 집합을 부팅할 때 남기고, 어느 쪽인지
이름을 함께 적습니다. 이 문서가 다루는 줄은 전부 `tls_trust`로 시작하므로
그것으로 거릅니다. 워커 로그는 대부분 스캔 진행 출력이라, 거르지 않으면 줄이
남아 있어도 찾지 못합니다.

```bash
docker-compose logs backend worker-scan worker-default beat | grep tls_trust
```

```
tls_trust.outbound  process=api     authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
tls_trust.outbound  process=worker  authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
tls_trust.outbound  process=worker  authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
tls_trust.outbound  process=beat    authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
```

모두 확인합니다. `process=worker` 줄은 워커마다 하나씩 나옵니다. Compose는
서비스마다 환경을 따로 주므로 한쪽에만 설정하고 다른 쪽을 빠뜨리는 일이 흔하고,
그중 워커가 가장 중요합니다. 스캐너들이 거기서 바깥으로 나가기 때문입니다.
`process=worker` 줄이 `source=bundled`로 남아 있으면 그 워커에는 설정이 전달되지
않은 것입니다.

`authorities`는 포털 자신의 HTTPS 호출이 받아들일 인증기관 수이고,
`bundled_authorities`는 포털이 기본으로 담고 있는 수입니다. 앞의 값이 더 크면
사내 인증기관이 공용 루트에 더해진 것이고, 대부분의 환경에서 그것이 원하는
상태입니다.

파일이 공용 루트를 대체해 버렸다면 경고가 뒤따릅니다.

```
tls_trust.public_roots_dropped  authorities=1 bundled_authorities=120 ...
```

위에서 설명한 실수입니다. 공용 루트 인증서를 번들에 함께 넣거나, 자체 인증기관만
신뢰하는 것이 의도한 설정이라면 무시하면 됩니다. 바깥으로 나가는 곳이 사내뿐인
환경에서는 그것이 맞는 설정입니다.

파일 대신 `SSL_CERT_DIR`을 쓰면 `authorities`가 `null`로 나옵니다. 디렉터리로
만든 신뢰 저장소는 필요할 때마다 인증서를 읽으므로 셀 수 있는 값이 없습니다.
0으로 적으면 없는 것처럼 보이므로 그렇게 하지 않았습니다.

## 알려진 한계

Docker 데몬이 수행하는 컨테이너 이미지 내려받기는 포털 설정 밖입니다. 사내
레지스트리에서 이미지를 가져오지 못해 컨테이너 스캔이 실패한다면, 그 호스트의
데몬에 인증서를 설치해야 합니다. 포털의 환경변수는 거기까지 닿지 않습니다.

