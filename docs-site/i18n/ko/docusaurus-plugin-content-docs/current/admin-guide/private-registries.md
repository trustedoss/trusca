---
id: private-registries
title: 의존성 해석용 사설 레지스트리
description: 소스 스캔에서 cdxgen이 사설 Maven·npm·pip 레지스트리에 인증하도록 설정합니다.
sidebar_label: 사설 레지스트리 (의존성 해석)
sidebar_position: 7.5
---

# 의존성 해석용 사설 레지스트리

**소스** 스캔은 클론한 저장소에서 `cdxgen`을 실행하고, `cdxgen`은 빌드가 하는 것과
같은 방식으로 `mvn`·`npm`·`pip`를 호출해 프로젝트의 의존성 트리를 해석합니다. 이
의존성 중 일부가 사설 레지스트리(사내 Nexus/Artifactory 미러, 범위가 지정된 npm
레지스트리, 사설 PyPI 인덱스)에 있다면 워커가 그 레지스트리의 자격증명을 가지고
있어야 합니다. 없으면 해석이 중간에 실패하고, 스캔이 만든 SBOM에는 접근하지 못한
의존성이 빠집니다.

이 기능은 [사설 레지스트리](../user-guide/scans.md#사설-레지스트리)와 다른
기능입니다. 그쪽(ER3)은 **컨테이너** 스캔에서 Trivy가 **컨테이너 이미지**를 pull할
때 인증합니다. 이 문서는 **소스** 스캔에서 cdxgen이 **프로젝트 의존성**을 해석할
때 인증하는 방법을 다룹니다. 두 자격증명은 저장 방식도, 쓰이는 도구도 다릅니다.

## 규칙

다음 네 파일 중 필요한 것만 한 디렉터리 아래에 둡니다.

| 파일 | 생태계 |
|---|---|
| `settings.xml` | Maven |
| `.npmrc` | npm |
| `pip.conf` | pip |
| `.netrc` | 범용 HTTP/Git 기본 인증. 예를 들어 `cdxgen`의 Go 분석기가 호출하는 `git`/`go list`가 접근하는 사설 Go 모듈 VCS 호스트 |

네 개를 다 둘 필요는 없습니다. cdxgen은 나머지가 없어도 개의치 않습니다. 워커는 이
디렉터리를 읽기 전용으로 `/etc/trusca/registry`라는 고정 경로에 마운트합니다.
경로를 설정 가능하게 두지 않고 고정한 이유는 임의 파일 읽기로 이어지지 않게
하기 위해서입니다. 프로젝트나 스캔 요청의 어떤 값도 이 경로에 무엇이 마운트되는지
정하지 못하고, 오직 운영자의 배포 설정만 정합니다.

## 파일 넣기

### Docker Compose

`.env`에 `REGISTRY_CONFIG_HOST_PATH`를 설정하고(기본값 `./secrets/registry`) 그
경로에 파일을 둡니다.

```bash
mkdir -p ./secrets/registry
cp /path/to/settings.xml ./secrets/registry/settings.xml
cp /path/to/.npmrc ./secrets/registry/.npmrc
```

`docker-compose.yml`은 이 디렉터리를 스캔 파이프라인 워커(프로덕션은
`worker-scan`, dev는 `celery-worker`)에 읽기 전용으로 이미 마운트해 둡니다.
디렉터리가 없거나 비어 있어도 문제없습니다. 이 저장소의 cosign 키 마운트와 같은
방식입니다.

`.netrc`는 가리키는 환경변수가 없는 유일한 파일이라(아래 참고) 별도의 명시적
마운트가 한 줄 더 필요합니다. `docker-compose.yml`과 `docker-compose.dev.yml`
모두 디렉터리 마운트 바로 옆에 이 줄을 주석으로 넣어 두었습니다. 파일을 만든
뒤에만 주석을 해제하세요.

```yaml
- ${REGISTRY_CONFIG_HOST_PATH:-./secrets/registry}/.netrc:/home/trustedoss/.netrc:ro
```

호스트에 파일이 없는 채로 주석만 해제하면 아무 일도 일어나지 않는 게 아니라
문제가 생깁니다. Docker는 없는 바인드 마운트 원본을 빈 **디렉터리**로 만들어
버리고, 그 디렉터리가 해당 경로를 파일로 기대하는 모든 것을 가립니다. 파일을
먼저 만드세요.

### Helm

Secret을 직접 만들고, 차트가 스캔 워커에만 마운트하게 합니다.

```bash
kubectl create secret generic trustedoss-registry-config \
  --from-file=settings.xml=./settings.xml \
  --from-file=.npmrc=./.npmrc
```

```yaml
worker:
  scan:
    extraVolumes:
      - name: registry-config
        secret:
          secretName: trustedoss-registry-config
    extraVolumeMounts:
      - name: registry-config
        mountPath: /etc/trusca/registry
        readOnly: true
    extraEnv:
      MVN_ARGS: "--settings /etc/trusca/registry/settings.xml"
      NPM_CONFIG_USERCONFIG: /etc/trusca/registry/.npmrc
      PIP_CONFIG_FILE: /etc/trusca/registry/pip.conf
```

`worker.scan.*`은 스캔 파이프라인 워커에만 적용됩니다.
[사설 인증기관](./private-ca.md)에 쓰는, 백엔드·beat·워커 둘 다에 적용되는
차트 전체용 `env.extraVolumes`/`env.extraEnv`와는 다릅니다. `cdxgen`을 실행하지
않는 파드가 레지스트리 자격증명을 읽을 이유는 없습니다.

`.netrc`는 같은 Secret을 `subPath`를 지정해 한 번 더 마운트해서
`/etc/trusca/registry`가 아니라 `$HOME/.netrc`(이 이미지의 `HOME`은
`/home/trustedoss`)에 오도록 합니다.

```yaml
    extraVolumeMounts:
      - name: registry-config
        mountPath: /home/trustedoss/.netrc
        subPath: .netrc
        readOnly: true
```

## 도구가 그 파일을 보게 하기

디렉터리를 마운트하는 것만으로는 아무것도 바뀌지 않습니다.
`/etc/trusca/registry`를 저절로 읽는 도구는 없습니다. `settings.xml`,
`.npmrc`, `pip.conf`는 각각 해당 도구에게 어디를 볼지 알려 주는 변수가 따로
필요합니다.

| 변수 | 읽는 주체 | 효과 |
|---|---|---|
| `MVN_ARGS` | cdxgen 자체의 Maven 호출 | `mvn` 명령줄에 붙는 추가 인자입니다. `--settings /etc/trusca/registry/settings.xml`로 설정하세요. |
| `NPM_CONFIG_USERCONFIG` | npm(표준 npm 환경변수) | 대체 `.npmrc` 경로. |
| `PIP_CONFIG_FILE` | pip(표준 pip 환경변수) | 대체 `pip.conf` 경로. |

필요한 만큼 `.env`에 추가합니다.

```bash
MVN_ARGS=--settings /etc/trusca/registry/settings.xml
NPM_CONFIG_USERCONFIG=/etc/trusca/registry/.npmrc
PIP_CONFIG_FILE=/etc/trusca/registry/pip.conf
```

**`MAVEN_SETTINGS`라는 환경변수는 존재하지 않고, 설정해도 아무 효과가 없습니다.**
Maven 자체는 `~/.m2/settings.xml`이나 `-s`/`--settings` 명령줄 플래그만 읽습니다.
환경변수 형태는 없습니다. `MVN_ARGS`는 cdxgen이 대신 실행하는 `mvn` 호출에 추가
인자를 넘기는 cdxgen 자체의 방식이고, `--settings <경로>`가 바로 그 플래그입니다.
(cdxgen은 스캔 중인 `pom.xml` 옆에서 `settings.xml`을 발견하면 콘솔 출력에 이
방법을 그대로 안내합니다.)

`NPM_CONFIG_USERCONFIG`와 `PIP_CONFIG_FILE`은 cdxgen의 기능이 아닙니다. npm과
pip가 각자 문서화한, 대체 설정 파일 경로를 가리키는 환경변수입니다. cdxgen은
`npm install`·`pip install`을 호출할 때 워커의 환경을 그대로 물려줄 뿐이고, 이는
다른 서브프로세스와 다르지 않습니다.

`.netrc`는 자신을 가리키는 변수가 아예 없습니다. `.netrc`를 따르는 모든 도구
(git, go, curl 기반 페처)는 조건 없이 `$HOME/.netrc`를 읽으므로, 환경변수가
아니라 위에서 설명한 두 번째 명시적 마운트가 필요합니다.

## 알려진 한계

- **프로젝트나 팀별이 아니라 워커 하나에 자격증명 하나입니다.** 조직별로 나뉘고
  pull 대상 이미지와 매칭하는 컨테이너 이미지 레지스트리 자격증명(ER3)과 달리,
  `/etc/trusca/registry` 아래 것은 그 스캔 파이프라인 워커가 실행하는 모든 소스
  스캔에 똑같이 보입니다. 팀마다 다른 사설 레지스트리 자격증명이 필요하다면
  현재는 워커 전체가 하나를 공유합니다. 프로젝트 단위 범위 지정은 구현되어 있지
  않습니다.
- **읽기 전용이고, 스캔 입력에서 결정되지 않습니다.** 마운트 경로는 배포 시점에
  운영자가 고정합니다. 프로젝트 설정이나 API 필드, 스캔 트리거 파라미터 어느
  것도 무엇이 마운트되고 읽히는지 바꿀 수 없습니다.
- **망 분리 환경에서도 이 문서가 필요합니다.** 워커 이미지와 Trivy 취약점
  데이터베이스의 오프라인 번들(별도로 추적 중)은, 도달은 가능하지만 자격증명이
  필요한 사내 미러에 인증하는 문제와는 다릅니다. 네트워크 구성에 따라 둘 다
  필요할 수도, 이 문서만 필요할 수도 있습니다.
