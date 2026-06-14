---
id: dynamic-scan-executor
title: 동적 스캔 실행기
description: 옵트인 local_docker 실행기가 호스트 Docker 소켓으로 환경별 빌드 사이드카를 띄우는 방식과, 신뢰할 수 없는 빌드를 격리하는 보안 기본값, 온프레에서 안전하게 설정하는 법.
sidebar_label: 동적 스캔 실행기
sidebar_position: 9
---

# 동적 스캔 실행기

manifest만 읽어서는 분석할 수 없는 프로젝트가 있습니다. 예를 들어 Android 앱은 cdxgen이 해석된 의존성 그래프를 보려면 먼저 Gradle 빌드를 실행해야 합니다. **동적 스캔 실행기**는 SBOM 생성 단계(build-prep + cdxgen)를 알맞은 toolchain을 이미 갖춘 *환경별* 컨테이너 안에서 실행합니다. 빌드는 임의 코드 실행이므로, 이 문서는 대부분 격리에 관한 내용입니다.

:::note 대상 독자
**온프레·단일 테넌트** 포털을 운영하며 `.env` 편집과 `docker-compose` 실행에 익숙한 `super_admin`. 기본 실행기는 설정이 필요 없습니다 — `SCAN_EXECUTOR=local_docker`를 켤 때만 이 문서를 읽으십시오. 스캔 단위 라이프사이클은 [스캔](../user-guide/scans.md)을 참고하십시오.
:::

## 두 가지 실행기 {#two-executors}

| 실행기 | 빌드를 실행하는 주체 | 사용 시점 |
|---|---|---|
| `inprocess`(기본) | Celery 워커가 cdxgen을 워커 로컬 서브프로세스로 실행합니다(기존 동작 그대로). | 워커 이미지 안에서 빌드되는 모든 경우(npm, Maven, 워커 toolchain의 pip). |
| `local_docker` | 워커가 호스트 Docker 소켓으로 환경별 **사이드카** 컨테이너(현재 Android SDK 이미지 `sbom-scanner-android-sdk<API>`)를 띄워 그 안에서 build-prep + cdxgen을 실행하고, 생성된 CycloneDX SBOM을 수집합니다. | 워커 이미지에 없는 toolchain이 프로젝트에 필요한 온프레 환경. |

`local_docker`는 라우팅된 이미지가 없는 환경과 Docker CLI가 없는 워커에서는 `inprocess`로 폴백합니다 — 그래서 이 기능을 켜도 기존에 동작하던 스캔이 깨지지 않습니다.

:::warning 온프레·단일 테넌트 전용
`local_docker`는 워커에 호스트 Docker 소켓 접근을 부여하는데, 이는 **호스트를 root 등가로 제어**하는 권한입니다. 스캔하는 저장소는 신뢰할 수 없는 입력입니다 — 악의적인 `build.gradle`이나 Gradle 플러그인이 빌드의 일부로 실행됩니다. 멀티 테넌트나 인터넷에 노출된 배포에서는 절대 켜지 마십시오. 멀티 테넌트 SaaS 경로는 별도의 샌드박스 모델입니다([한계](#limitations) 참고).
:::

## 빌드를 격리하는 요소 {#what-contains-the-build}

다음 기본값은 실행기가 코드에서 적용하므로, 설정하지 않아도 적용됩니다. 사이드카가 신뢰할 수 없는 빌드 코드를 실행하기 때문에 존재합니다.

- **볼륨 범위.** 전략은 기본 `named`입니다. 사이드카는 `SCAN_WORKSPACE_VOLUME`(스캔 트리)**만** 마운트하고 그 외에는 마운트하지 않습니다. 대안인 `volumes_from`은 cosign SBOM 서명키를 포함한 *모든* 워커 볼륨을 신뢰할 수 없는 빌드에 다시 마운트합니다. `SCAN_VOLUMES_FROM_ACK=1`과 `SCAN_WORKER_CONTAINER`를 모두 설정하지 않으면 거부됩니다. `named`로 두십시오.
- **capability.** 사이드카는 `--cap-drop=ALL`로 실행하고 최소 집합(`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`)만 다시 추가하며, 빌드가 권한을 상승시킬 수 없도록 `--security-opt no-new-privileges`를 함께 적용합니다.
- **리소스 한도.** `--memory 4g`, `--cpus 2`, `--pids-limit 4096`이 기본으로 켜져 있습니다. 신뢰할 수 없는 빌드가 호스트를 OOM에 빠뜨리거나 fork bomb으로 공격할 수 없습니다.
- **환경변수.** 사이드카는 `HOME`, `FETCH_LICENSE`, `CDXGEN_DEBUG_MODE`만 받습니다. 워커 시크릿은 전달되지 않습니다.
- **이미지 핀.** `:latest` 태그는 거부됩니다(핵심 규칙 #9). `SCAN_ANDROID_IMAGE_TAG`를 semver(`v1.0.0`)나 `sha256:<digest>`로 핀하십시오. 로컬 개발에서만 `SCAN_ALLOW_UNPINNED_IMAGE=1`이 이 거부를 해제합니다.
- **시크릿 마스킹.** 사이드카 stderr에 나타나는 PEM private key 블록은 해당 줄이 스캔 로그에 도달하기 전에 마스킹됩니다.

## 켜는 법(옵트인) {#turn-it-on}

워커 이미지에는 Docker CLI가 이미 포함되어 있으므로, 결정할 것은 워커가 Docker에 도달하는 방식과 사이드카 네트워크를 격리하는 방식뿐입니다.

### 1. 워커에 Docker 접근 부여 — 프록시 경유 {#give-the-worker-docker-access}

워커에 원시 `/var/run/docker.sock`을 마운트할 수도 있지만, 권장 경로는 워커를 **docker-socket-proxy**로 우회시켜 워커가 실제로 필요한 Docker API verb만 호출하도록 하는 것입니다.

`local-docker` compose 프로파일로 프록시를 기동하고(`docker-compose.dev.yml`에 정의되어 있습니다), 워커의 `DOCKER_HOST`를 프록시로 지정한 뒤, 워커에서 원시 소켓 마운트를 **제거**하십시오.

<!-- docs-uat: id=dynamic-scan-executor-proxy kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 스택과 함께 프록시 기동
docker-compose --profile local-docker up -d

# 포털의 .env — 워커를 프록시로 지정
DOCKER_HOST=tcp://docker-socket-proxy:2375
```

프록시는 `containers/*`와 `images/*`만 허용하며 `exec`, `swarm`, `networks`, `volumes`는 차단합니다.

:::caution 프록시는 create payload를 검사하지 못합니다
docker-socket-proxy는 워커가 호출할 수 있는 API *verb*를 통제하지만, 컨테이너 생성 요청의 본문은 검사하지 않습니다 — `privileged: true`나 추가 bind 마운트를 거부하지 못합니다. 띄워진 컨테이너를 실제로 제약하는 것은 위의 사이드카 하드닝(cap-drop, no-new-privileges, named 볼륨)이므로, 프록시 뒤에서도 이 하드닝은 필수입니다.
:::

### 2. 사이드카 egress 격리 {#isolate-sidecar-egress}

Gradle 빌드는 패키지 레지스트리(Google Maven, Maven Central)에 도달해야 하므로 사이드카는 인터넷 egress를 유지합니다 — 다만 `postgres`, `redis`, 백엔드에는 도달할 수 없어야 합니다. `SCAN_SIDECAR_NETWORK`를 전용 `scan-egress` 네트워크(compose에 정의)로 설정해 사이드카를 앱 서비스와 **분리된** 네트워크에 두십시오.

<!-- docs-uat: id=dynamic-scan-executor-network kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 포털의 .env — <project>는 compose 프로젝트 prefix
SCAN_SIDECAR_NETWORK=<project>_scan-egress
```

`SCAN_SIDECAR_NETWORK`를 설정하지 않으면 사이드카가 기본 브리지에 올라가 내부 서비스에 도달할 수 있고, 워커가 시작 시 **경고**를 로그에 남깁니다. 프로덕션에서는 방화벽이나 egress 프록시로 필요한 레지스트리(Google, Maven)로만 egress를 더 제한하십시오.

### 3. 워크스페이스 볼륨과 이미지 핀 {#pin-the-workspace-volume-and-image}

`SCAN_WORKSPACE_VOLUME`을 compose-prefixed 볼륨명으로 설정하고, 사이드카 이미지를 고정 버전으로 핀하십시오.

<!-- docs-uat: id=dynamic-scan-executor-pins kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 포털의 .env
SCAN_EXECUTOR=local_docker
SCAN_WORKSPACE_VOLUME=trustedoss-portal_scan-workspace   # compose-prefixed 볼륨명
SCAN_ANDROID_IMAGE_TAG=v1.0.0                            # semver 또는 sha256:<digest>
```

`.env` 편집 후 워커를 재시작하십시오 — 실행기 키는 런타임에 `os.getenv`로 읽힙니다.

## 동작 확인 {#verify-it-worked}

<!-- docs-uat: id=dynamic-scan-executor-verify-routing kind=manual tier=manual -->
1. Android 프로젝트에 소스 스캔을 트리거합니다. 워커 로그에 실행기가 in-process cdxgen 서브프로세스가 아니라 사이드카로 라우팅되는 것이 보입니다(SBOM 단계 시작 시 해석된 이미지 태그와 `local_docker` 전략이 로그에 남습니다).
<!-- docs-uat: id=dynamic-scan-executor-verify-sidecar kind=manual tier=manual -->
2. 스캔이 실행되는 동안 `docker ps --filter label=trusca.role=scan-sidecar`가 사이드카를 정확히 하나 표시합니다. `docker inspect <id>`로 들여다보면 `CapDrop: [ALL]`, 유일한 마운트인 named 워크스페이스 볼륨, `scan-egress` 네트워크가 확인됩니다.
<!-- docs-uat: id=dynamic-scan-executor-verify-sbom kind=manual tier=manual -->
3. 스캔이 비어 있지 않은 컴포넌트 수와 함께 `succeeded`에 도달하고, 완료 후 사이드카가 `docker ps`에서 사라집니다.

## 한계 {#limitations}

:::warning hard kill 후 고아 사이드카
정상 종료 시 실행기는 자신의 사이드카를 제거합니다. 워커가 hard `SIGKILL`로 종료되면 사이드카가 고아로 남을 수 있습니다. 고아는 `trusca.role=scan-sidecar` 라벨로 식별해 수동으로 제거하십시오. 자동 reaper는 후속 증분입니다.
:::

- **멀티 테넌트 SaaS는 범위 밖입니다.** 이 모델은 단일 테넌트가 통제하는 호스트 경계를 신뢰합니다. SaaS 경로는 대신 스캔마다 gVisor 샌드박스를 적용한 Kubernetes Job을 사용합니다 — 이 실행기가 아니라 별도의 후속 증분입니다.
- **현재 라우팅되는 환경은 Android뿐입니다.** 그 외 환경은 모두 `inprocess`로 실행되며, `local_docker`는 환경을 점진적으로 추가합니다.

## 환경변수 레퍼런스 {#environment-variable-reference}

다음은 `.env.example`의 **Dynamic scan executor** 섹션을 반영합니다. 모두 런타임에 `os.getenv`로 읽힙니다 — `.env`를 편집하고 워커를 재시작하십시오. 정식 레퍼런스는 [환경변수 → 스캔 파이프라인](../reference/env-variables.md#scan-pipeline)을 참고하십시오.

| 키 | 기본값 | 설명 |
|---|---|---|
| `SCAN_EXECUTOR` | `inprocess` | `inprocess`는 cdxgen을 워커 서브프로세스로 실행하고, `local_docker`는 Docker 소켓으로 환경별 사이드카를 띄웁니다(온프레 전용). |
| `SCAN_DOCKER_VOLUME_STRATEGY` | `named` | `named`는 워크스페이스 볼륨만 사이드카에 마운트하고, `volumes_from`은 모든 워커 볼륨을 다시 마운트합니다(아래 ack 없이는 거부). |
| `SCAN_WORKSPACE_VOLUME` | — | `named`에 필요: compose-prefixed 워크스페이스 볼륨명(예: `trustedoss-portal_scan-workspace`). 미설정 시 in-process로 폴백합니다. |
| `SCAN_WORKSPACE_MOUNT` | `/tmp/trustedoss` | 사이드카 내부의 워크스페이스 볼륨 마운트 지점(프로덕션: `/workspace`). |
| `SCAN_WORKER_CONTAINER` | — | `volumes_from`에 필요: 워커 컨테이너에 대한 명시적 참조. |
| `SCAN_VOLUMES_FROM_ACK` | — | `1`로 설정하면 `volumes_from`의 과다 공유를 수용합니다. 권장하지 않습니다 — cosign 서명키를 빌드에 노출합니다. |
| `SCAN_SIDECAR_PIDS_LIMIT` | `4096` | 사이드카의 프로세스 한도(fork bomb 방지). |
| `SCAN_SIDECAR_MEMORY` | `4g` | 사이드카의 메모리 상한(호스트 OOM 방지). |
| `SCAN_SIDECAR_CPUS` | `2` | 사이드카의 CPU 한도. |
| `SCAN_SIDECAR_CAP_DROP` | `ALL` | 최소 집합을 다시 추가하기 전에 제거하는 Linux capability. |
| `SCAN_SIDECAR_CAP_ADD` | `CHOWN,DAC_OVERRIDE,FOWNER,SETGID,SETUID` | build-prep을 위해 다시 추가하는 최소 capability. |
| `SCAN_SIDECAR_NETWORK` | — | 사이드카용 격리 egress 네트워크(권장: `<project>_scan-egress`). 미설정 시 기본 브리지를 쓰고 시작 경고를 남깁니다. |
| `CDXGEN_IMAGE_TAG` | `v12` | cdxgen 언어 이미지 태그. |
| `CDXGEN_ALLINONE_IMAGE` | `ghcr.io/cyclonedx/cdxgen:v12.5.0` | 혼합·미상 환경용 올인원 이미지. |
| `SCAN_ANDROID_IMAGE_PREFIX` | `ghcr.io/sktelecom/sbom-scanner-android-sdk` | Android 사이드카 이미지 prefix이며 API 레벨이 뒤에 붙습니다. |
| `SCAN_ANDROID_IMAGE_TAG` | `v1.0.0` | Android 이미지의 핀된 semver 또는 `sha256:<digest>`. `:latest`는 거부됩니다. |
| `SCAN_ALLOW_UNPINNED_IMAGE` | — | `1`로 설정하면 `:latest` 태그를 허용합니다. 개발 전용. |
| `SCAN_ANDROID_API_DEFAULT` | `34` | 프로젝트가 선언하지 않을 때 사용하는 Android `compileSdk` 폴백. |
| `CDXGEN_SPEC_VERSION` | `1.5` | cdxgen이 생성하는 CycloneDX 스펙 버전(`1.6`이면 CycloneDX 1.6). 두 실행기 모두에 적용됩니다. |
| `CDXGEN_FETCH_LICENSE` | `false` | `true`면 cdxgen이 컴포넌트 라이선스를 해석합니다(느려짐). 두 실행기 모두에 적용됩니다. |

## 관련 문서 {#see-also}

- [스캔](../user-guide/scans.md) — 스캔 단위 라이프사이클과 진행 화면
- [스캔 보존](./scan-retention.md) — 이 스캔이 생성한 SBOM을 보존·회수하는 방식
- [환경변수 → 스캔 파이프라인](../reference/env-variables.md#scan-pipeline)
