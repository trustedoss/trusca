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

:::danger `local_docker`로는 더 이상 스캔하지 않습니다
Docker API에 접근할 수 있는 워커는 이제 BomLens 엔진 작업 외에는 모두 거부하므로, 스캔 워커에서 `SCAN_EXECUTOR=local_docker`를 쓸 수 없습니다. 아래 [1단계](#give-the-worker-docker-access)를 참고하십시오. 이 문서의 나머지는 참고용으로 실행기를 설명합니다.
:::

## 두 가지 실행기 {#two-executors}

| 실행기 | 빌드를 실행하는 주체 | 사용 시점 |
|---|---|---|
| `inprocess`(기본) | Celery 워커가 cdxgen을 워커 로컬 서브프로세스로 실행합니다(기존 동작 그대로). | 워커 이미지 안에서 빌드되는 모든 경우(npm, Maven, 워커 toolchain의 pip). |
| `local_docker` | 워커가 호스트 Docker 소켓으로 환경별 **사이드카** 컨테이너(현재 Android SDK 이미지 `sbom-scanner-android-sdk<API>`)를 띄워 그 안에서 build-prep + cdxgen을 실행하고, 생성된 CycloneDX SBOM을 수집합니다. | 워커 이미지에 없는 toolchain이 프로젝트에 필요한 온프레 환경. |

`local_docker`는 라우팅된 이미지가 없는 환경과 Docker CLI가 없는 워커에서는 `inprocess`로 폴백합니다 — 그래서 이 기능을 켜도 기존에 동작하던 스캔이 깨지지 않습니다.

### 사이드카로 라우팅하는 환경 {#which-environments-are-routed}

기본값은 `SCAN_LOCAL_DOCKER_ENVS=android`로, **Android만** 사이드카로 라우팅합니다. 의도한 설정입니다. 같은 프로젝트를 all-in-one 워커와 전용 cdxgen 언어 이미지로 각각 측정해 보니 `node`, `go`, `rust`, `ruby`, `java`, `python`, `php`, `dotnet`은 컴포넌트 수가 **동일**했습니다. 워커가 이미 해당 toolchain을 갖추고 있고, cdxgen이 전이 의존성 그래프를 같은 방식으로 해석하기 때문입니다. 진짜 공백은 Android 하나뿐입니다 — 워커에 Android SDK가 없어 Gradle 플러그인이 의존성 그래프를 해석하지 못해 컴포넌트가 **0개**가 나오는 반면, SDK 사이드카는 전체 그래프를 만들어 냅니다.

따라서 다른 언어를 사이드카로 라우팅해도 온프레 환경에서 검출이 좋아지지 않습니다. 그래도 언어별 빌드를 워커와 **격리**하고 싶다면 대상을 넓히십시오.

<!-- docs-uat: id=dynamic-scan-executor-routed-envs kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
SCAN_LOCAL_DOCKER_ENVS=android,node,go,rust,ruby,java,python,php,dotnet
```

또는 환경을 하나씩 나열하는 대신 `all` 예약값으로 전부 라우팅할 수 있습니다.

<!-- docs-uat: id=dynamic-scan-executor-routed-envs-all kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
SCAN_LOCAL_DOCKER_ENVS=all
```

스캔 대상 소스를 신뢰할 수 없을 때(예: 고객 자체 코드가 아니라 검증용으로 대량으로 가져온 공개 저장소 코호트) 쓰는 설정입니다. 손으로 나열한 목록은 언어 하나만 빠뜨려도 그 빌드가 격리 없이 워커에서 임의 코드를 실행하게 되고, `all`은 올인원 이미지로 처리되는 `cpp`·`mixed`·`unknown`까지 놓치지 않고 포함합니다.

라우팅한 언어는 처음 사용할 때 cdxgen 이미지를 받습니다(언어마다 수 GB). 언어 이미지는 `CDXGEN_IMAGE_TAG`(`v12`) 태그로 고정돼 있어 미고정 이미지 예외 없이 그대로 통과합니다. Android의 `:latest`만 `SCAN_ANDROID_IMAGE_TAG` 고정이 필요합니다.

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

### 1. 워커에 Docker 접근 부여 {#give-the-worker-docker-access}

스캔을 실행하는 워커에서는 이 단계를 더 이상 적용할 수 없습니다. `local_docker` 사이드카를 실행하는 워커에는 Docker API가 필요한데, 같은 워커가 라우팅되지 않은 환경에서는 스캔 대상 프로젝트의 빌드를 실행하고, 다른 스캔 작업과 기본 큐 작업도 모두 실행합니다. Docker API에 접근할 수 있는 워커에서 스캔 대상 저장소의 코드가 실행되면 권한 있는 컨테이너를 만들어 호스트를 장악할 수 있으므로, TRUSCA는 이를 거부합니다.

- `DOCKER_HOST`가 설정되어 있거나, `/var/run/docker.sock` 또는 `/run/docker.sock`에 Docker 소켓이 마운트되어 있거나, `docker-socket-proxy` 이름이 해석되는 워커는 BomLens 엔진 작업만 실행합니다. 소스 스캔 작업을 포함한 다른 모든 작업은 그 이유와 함께 실패합니다.
- 이런 워커에서는 빌드 준비, cdxgen, govulncheck가 시작하지 않습니다.

따라서 `local_docker`를 위해 스캔 워커에 소켓을 마운트했거나 `DOCKER_HOST`를 설정한 배포는 그 접근을 제거할 때까지 스캔이 멈춥니다. `docker-compose.dev.yml`도 더 이상 소켓을 마운트하지 않습니다. 빌드를 별도 컨테이너에서 격리해 실행하려면 Docker API를 전용 워커에만 두는 BomLens 엔진을 쓰십시오([스캔 엔진](#scan-engine)). `local_docker`는 어떤 배포에도 권장하지 않으며 이후 제거됩니다.

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

- **멀티 테넌트 SaaS는 범위 밖입니다.**이 모델은 단일 테넌트가 통제하는 호스트 경계를 신뢰합니다. SaaS 경로는 대신 스캔마다 gVisor 샌드박스를 적용한 Kubernetes Job을 사용합니다 — 이 실행기가 아니라 별도의 후속 증분입니다.
- **현재 라우팅되는 환경은 Android뿐입니다.** 그 외 환경은 모두 `inprocess`로 실행되며, `local_docker`는 환경을 점진적으로 추가합니다.

## 스캔 엔진(미리 보기) {#scan-engine}

:::info 미리 보기 기능이며 기본값은 꺼짐입니다
`SCAN_ENGINE`은 미리 보기 설정입니다. compose 배포에서 BomLens 엔진을 시험해 볼 때가 아니면 설정하지 않은 채로 두십시오. Helm 차트에서는 아직 실행되지 않습니다.
:::

위의 `SCAN_EXECUTOR`는 cdxgen을 어디에서 실행할지를 정합니다. `SCAN_ENGINE`은 스캔 결과를 어느 엔진이 만들지를 정합니다. 기본값 `inprocess`는 워커가 빌드 준비, cdxgen, scancode, Trivy를 직접 실행하는 기존 방식입니다. `bomlens`는 SBOM 생성, 라이선스 탐지, 취약점 매칭을 BomLens 이미지에 맡기고, 그 결과로 하는 일(저장, 서명, 라이선스 정책, 승인, 빌드 게이트, 알림)은 TRUSCA가 그대로 맡습니다. BomLens 스캔에는 `SCAN_EXECUTOR`가 적용되지 않습니다.

### BomLens 스캔이 실행되는 순서 {#engine-how-it-runs}

워커는 소스를 가져온 뒤 컨테이너를 최대 네 개까지 하나씩 실행하고, 각 컨테이너가 끝날 때까지 기다립니다.

1. **실행 계획.** BomLens 이미지가 `MODE=PLAN`으로 소스를 읽기 전용으로 읽고 실행 계획을 씁니다. 계획에는 의존성을 해석할 cdxgen 언어 이미지(다이제스트로 고정)와 그 인자가 들어 있습니다. 진행 화면에는 준비 단계로 표시됩니다.
2. **1단계.** 계획이 지정한 언어 이미지가 의존성을 해석하고 첫 SBOM을 씁니다. 스캔 대상 프로젝트의 빌드 도구를 실행하는 컨테이너는 이것 하나뿐이고, 네트워크에 접근하는 컨테이너도 이것 하나뿐입니다.
3. **1단계가 강제 종료된 경우의 복원.** 메모리가 부족하거나 시간 한도를 넘겨 종료된 1단계는 소스 트리를 원래대로 되돌리지 못합니다. 그래서 워커가 같은 1단계를 복원 전용 모드로, 네트워크 없이 한 번 더 실행합니다.
4. **2단계.** BomLens 이미지가 SBOM을 후처리하고 라이선스를 탐지하고 Trivy를 실행합니다. 1단계의 종료 코드와 관계없이 실행합니다. 1단계가 SBOM을 남기지 못했으면 BomLens가 매니페스트를 직접 읽어 직접 의존성만 나열하고 SBOM 생성을 열화로 기록합니다. 이때 스캔은 실패하지 않고 성공으로 끝나며, 프로젝트 개요 탭에 경고가 나타납니다.

계획이 지원하지 않는 프로젝트라고 판정하면(BomLens가 SDK 이미지를 배포하지 않는 Android 프로젝트) 1단계와 복원을 건너뛰고 2단계만 실행하며, 결과는 위와 같이 열화로 기록됩니다.

그다음 워커는 결과 매니페스트를 BomLens 엔진 계약에 맞춰 검사하고(계약 메이저 버전 1만 받으며, 다른 메이저 버전이면 그 버전을 스캔 오류에 적고 거부합니다), 출력 파일마다 매니페스트에 기록된 크기와 SHA-256이 맞는지 확인한 뒤, SBOM과 Trivy 보고서를 기존 스캔과 같은 코드로 저장합니다. 프로젝트 자체 파일에서 scancode가 탐지한 라이선스(`bomlens-deep-license` 이미지)는 기존 scancode 단계와 같은 방식으로 탐지된 라이선스로 저장합니다. 다른 점이 하나 있습니다. 이 scancode 결과가 기록된 SHA-256과 맞지 않거나 올바른 JSON이 아니면 스캔 전체가 실패합니다. 엔진 출력은 무결성을 먼저 확인하기 때문입니다. 기존 방식에서는 scancode 문제가 단계 열화로만 기록됩니다. 결과가 `SCANCODE_MAX_RESULT_BYTES`보다 크면 두 방식 모두 읽지 않고 단계 열화로 기록합니다. 엔진이 시간 예산 안에 하지 못한 레지스트리 라이선스 조회는 기존 조회와 같은 개요 탭 알림에 표시되고, BomLens가 배포되지 않는 폴더(테스트, 예제, 벤치마크)로 보고 제외한 내용은 스캔 메타데이터의 `scope_filter`에 제외 패턴과 건너뛴 매니페스트 파일로 남습니다. BomLens 단계가 열화되면 다른 단계와 마찬가지로 프로젝트 개요 탭에 표시됩니다. 개요 탭에는 단계와 사유만 나옵니다. 엔진이 남긴 설명은 스캔 메타데이터의 `stage_outcomes.<단계>.engine_detail`과 워커의 `bomlens_stage_degraded` 경고 로그에 남습니다. 예를 들어 매니페스트는 있는데 컴포넌트가 나오지 않은 SBOM 생성 단계라면 찾은 매니페스트와 실패한 의존성 해석 단계(`gradle-dependencies` 등)가 적힙니다. 설명은 최대 300자까지 저장하고, 자격 증명과 제어 문자는 지웁니다. 취약점 스캔이 결과를 내지 못하면 취약점 0건으로 처리하지 않고 스캔을 실패로 처리합니다.

### Trivy 데이터베이스의 나이 {#engine-trivy-db-age}

오래된 Trivy 데이터베이스를 다루는 방식은 두 엔진이 다릅니다. 워커는 2단계에 데이터베이스가 얼마나 오래돼도 되는지 알려 줍니다. 갱신 주기 `TRIVY_DB_REFRESH_HOURS`를 일 단위로 올림한 값의 두 배에 이틀을 더한 값이고, 30일을 넘지 않습니다(기본값인 주 1회 갱신이면 16일). 나이는 데이터베이스가 만들어진 시각부터 셉니다. 데이터베이스가 이보다 오래되면 BomLens가 취약점 스캔을 열화로 기록하고, 스캔은 `the vulnerability scan degraded (unavailable)` 오류로 실패합니다. 기존 방식의 스캔은 오래된 데이터베이스로도 계속 진행하고, 그 데이터베이스가 아는 취약점을 보고합니다. 주 1회 갱신에서 한 번 실패하면 데이터베이스가 14일쯤 지난 상태가 되지만 BomLens 스캔은 계속됩니다. 두 번 연속으로 실패하면 갱신에 성공할 때까지 스캔이 실패합니다. 스캔이 실패하기 전에 `/admin/health`의 **Trivy DB** 카드가 7일에 오래됨(stale), 14일에 매우 오래됨(very stale)으로 먼저 알려 줍니다. 이런 실패가 생기면 [취약점 데이터](./vulnerability-data.md#how-the-lifecycle-works)에 따라 데이터베이스를 갱신하십시오.

### 엔진 컨테이너에 적용되는 제한 {#engine-containers}

실행 계획은 스캔 대상 소스를 읽은 컨테이너가 만든 출력입니다. 그래서 워커는 계획을 신뢰하지 않는 입력으로 다루고, 각 컨테이너를 어떻게 실행할지는 워커가 정합니다.

- **다이제스트로 고정한 이미지.** BomLens 이미지는 TRUSCA 릴리스에 고정되어 있습니다. 1단계 이미지는 `SCAN_BOMLENS_STAGE1_REPOSITORIES`에 있는 저장소에서, `SCAN_BOMLENS_STAGE1_IMAGE_PREFIX`를 설정했으면 그 접두사 아래에서 와야 하고 다이제스트가 있어야 합니다. `:latest`는 어디에서든 거부합니다. 2단계는 계획에서 이미지와 마운트 경로 두 개만 받고, 사용자, 진입점, 인자, 작업 디렉터리, 네트워크는 TRUSCA가 정합니다.
- **스캔 워크스페이스 안의 마운트만 허용.** 계획은 마운트를 역할(소스, 출력, 보조 파일, 복원 상태)로만 지정하고 호스트 경로는 지정하지 못합니다. 워커가 역할마다 해당 스캔의 워크스페이스 아래 폴더를 연결하고, 워크스페이스를 벗어나거나 심볼릭 링크를 거치는 경로는 거부합니다. 시스템 경로, Docker 소켓, TRUSCA가 직접 마운트하는 경로(`/tmp`, `/trivy-db`) 위에 마운트하려는 계획도 거부합니다. 예외는 2단계의 Trivy 데이터베이스 역할 하나뿐이며, 정확히 `/trivy-db`에 읽기 전용일 때만 받습니다. 워커는 그 자리에 자기 Trivy 데이터베이스를 연결합니다. 컨테이너는 먼저 생성만 하고, Docker 데몬이 기록한 마운트가 요청한 것과 정확히 같을 때만 시작합니다.
- **읽기 전용 루트 파일시스템.** `/tmp`만 워크스페이스의 임시 폴더로 씁니다. 1단계는 `SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT=false`로 쓰기 가능한 루트를 줄 수 있습니다(아래 표 참고).
- **위의 사이드카와 같은 격리.** `--cap-drop ALL` 뒤에 최소한의 권한만 다시 추가하고, `no-new-privileges`를 켜고, `SCAN_SIDECAR_PIDS_LIMIT`, `SCAN_SIDECAR_MEMORY`, `SCAN_SIDECAR_CPUS` 제한을 적용하며 스왑은 쓰지 않습니다.
- **네트워크 없음.** 1단계만 `SCAN_BOMLENS_NETWORK`에 연결합니다. 워커는 `host`, compose 네트워크인 `trustedoss`와 `trustedoss-docker-api`, 워커 자신이 연결된 네트워크(이름과 ID 모두 비교)를 거부합니다. 워커가 자기 네트워크를 읽지 못하면 1단계를 아예 실행하지 않습니다. 2단계는 Trivy 데이터베이스를 내려받지 않고 워커의 데이터베이스를 읽기 전용으로 읽으므로, TRUSCA의 다른 기능과 같은 취약점 데이터를 씁니다.
- **허용 목록에 있는 환경변수만 전달.** 워커의 환경변수는 넘기지 않습니다. `GOPROXY`, `GOSUMDB`, 그리고 1단계에만 넘기는 `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`(대소문자 모두) 같은 전달 대상 이름은 엔진 워커에 설정되어 있을 때 그 값을 그대로 넘깁니다. 워커 자신의 루프백 주소(`127.0.0.1`, `localhost`, `::1`)를 가리키는 프록시는 넘기지 않습니다. 컨테이너 안에서 그 주소는 컨테이너 자신이기 때문입니다. 프록시 URL에 인증 정보가 들어 있으면 스캔 대상 프로젝트의 코드를 실행하는 빌드에 그대로 전달되므로, 패키지 읽기 권한만 있는 인증 정보를 쓰십시오. 워커는 `docker create` 명령줄에 이 변수들의 이름만 적고, 값은 Docker CLI 프로세스의 환경변수로 넘기므로 프로세스 목록에는 값이 보이지 않습니다. 다만 값은 엔진 컨테이너의 환경변수에 남아, 컨테이너가 지워질 때까지 그 컨테이너에 `docker inspect`를 실행하면 보입니다.
- **종료 기한.** 엔진 컨테이너마다 스캔 ID와 종료 기한을 레이블로 붙입니다. 스캔이 더 이상 실행 중이 아니거나 기한이 지나면 엔진 워커가 컨테이너를 제거합니다. 5분마다 확인하고, 스캔을 취소하면 바로 확인합니다. 그래서 워커가 강제 종료되어 남은 컨테이너도 몇 분 안에 제거됩니다.

### 워커별 실행 범위 {#engine-workers}

Docker API로는 권한 있는(privileged) 컨테이너를 만들 수 있습니다. 그래서 Docker API에 접근하는 워커는 하나뿐이고, 그 워커는 스캔 대상 저장소의 코드를 실행하지 않습니다.

| 워커 | 큐 | Docker API | 실행하는 작업 |
|---|---|---|---|
| `worker-engine`(프로필 `bomlens-engine`) | `trustedoss.engine`만 | `docker-socket-proxy` 경유 | BomLens 스캔 작업, 엔진 컨테이너 정리 작업 |
| `worker-scan` | `trustedoss.scan`, `trustedoss.default` | 없음 | 기존 방식 스캔(빌드 준비, cdxgen, scancode, Trivy), 컨테이너·SBOM 스캔, 도달 가능성 분석(govulncheck) |
| `worker-default` | `trustedoss.default` | 없음 | 알림, 백업, 보존 기간 정리, 카탈로그 갱신 |

엔진 워커는 기존 방식(in-process) 파이프라인을 실행하지 않습니다. 소스 스캔은 큐에 들어갈 때 `SCAN_ENGINE=bomlens`였던 경우에만 엔진 워커로 가고, 엔진 워커가 받는 작업은 엔진만 실행할 수 있습니다. 워커 자체에서도 양방향을 확인합니다. Docker API에 접근할 수 있는 워커(`DOCKER_HOST` 설정, `/var/run/docker.sock` 또는 `/run/docker.sock`의 Docker 소켓, `docker-socket-proxy` 이름 해석 중 하나)는 엔진 작업만 실행하고 다른 작업은 거부합니다. 이런 워커에서는 빌드 준비, cdxgen, govulncheck도 시작하지 않습니다. 이때 작업은 실행하지 않고 그 이유와 함께 실패합니다. `worker-engine`은 Celery 원격 제어도 받지 않으므로(`CELERY_WORKER_REMOTE_CONTROL=false`) 브로커에 보낸 브로드캐스트로 이 워커를 조작할 수 없습니다. 취소된 엔진 스캔은 다음 단계에서 멈추고, 정리 작업이 그 컨테이너를 제거합니다.

### 엔진 켜기(compose) {#engine-turn-on}

`worker-engine`은 `docker-compose.yml`의 필터링 프록시를 거쳐 Docker에 접근하며, 원시 소켓은 받지 않습니다. 프록시는 컨테이너와 이미지 호출에만 응답하고 `exec`, 볼륨, 네트워크, swarm, 시스템, 빌드 호출은 거부합니다. 컨테이너 생성 요청의 본문은 검사하지 않으며, 앞 절의 제한은 워커가 적용합니다. 엔진은 워크스페이스 볼륨의 일부를 `volume-subpath`로 마운트하므로, Docker API 1.45(Docker Engine 26)보다 오래된 데몬에서는 실행하지 않습니다.

<!-- docs-uat: id=dynamic-scan-executor-engine-proxy kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 한 번만: 1단계가 패키지 레지스트리에 접근할 네트워크
# (이 네트워크의 컨테이너끼리는 서로 접근하지 못함)
docker network create --opt com.docker.network.bridge.enable_icc=false \
  trustedoss-scan-egress

# 포털의 .env (<project>는 compose 프로젝트 이름)
SCAN_ENGINE=bomlens
SCAN_WORKSPACE_VOLUME=<project>_workspace
SCAN_TRIVY_CACHE_VOLUME=<project>_trivy-cache

# 엔진 워커와 Docker API 프록시를 시작하고, API·beat·워커가 새 값을 읽게 함
docker-compose --profile bomlens-engine up -d
```

compose에서 `SCAN_BOMLENS_NETWORK` 기본값은 `trustedoss-scan-egress`입니다. 1단계가 쓰는 레지스트리로만 나가게 하려면 이 네트워크에 송신 방화벽을 적용하십시오.

:::warning `SCAN_ENGINE=bomlens`는 엔진 워커를 실행한 상태에서만 설정하십시오
`SCAN_ENGINE=bomlens`일 때 큐에 들어간 소스 스캔은 `trustedoss.engine`으로 가며, 이 큐는 `worker-engine`만 처리합니다. 엔진 워커가 없으면 스캔이 대기 상태로 남고, 대기 중인 스캔이 있는 프로젝트는 새 스캔을 시작하지 않으므로 엔진 워커를 실행하거나 대기 중인 스캔을 취소할 때까지 그 프로젝트의 스캔이 멈춥니다. `SCAN_ENGINE=bomlens`를 설정하기 전에 프로필을 시작하고, 프로필을 내리기 전에 `SCAN_ENGINE=inprocess`로 되돌리십시오.
:::

여러 TRUSCA 설치본이 Docker 데몬 하나를 함께 쓰면 설치본마다 다른 `TRUSCA_INSTANCE_ID`를 지정하십시오. 엔진 컨테이너에 이 값이 레이블로 붙고, 각 설치본의 정리 작업은 자기 컨테이너만 제거합니다.

`SCANCODE_ENABLED`를 켠 설치에서는 2단계가 scancode를 포함한 BomLens 공개 변형 이미지(`bomlens-deep-license`)를 쓰므로 자체 소스 라이선스 탐지도 그대로 실행됩니다.

범위 필터 설정도 그대로 적용됩니다. `SCAN_SCOPE_FILTER_NON_DEPLOYABLE_ENABLED=false`(또는 `SCAN_SCOPE_FILTER_ENABLED=false`)로 설정하면 BomLens가 평소 SBOM에서 빼는 테스트, 예제, 벤치마크 폴더도 포함합니다.

:::tip Docker 데몬에 사용자 네임스페이스를 적용하십시오
엔진 컨테이너는 컨테이너 안에서 root로 실행됩니다. 데몬의 `userns-remap` 설정이나 rootless Docker 데몬을 쓰면 그 root가 호스트에서는 권한 없는 사용자가 되어, 컨테이너를 탈출하더라도 접근할 수 있는 범위가 줄어듭니다. 둘 다 TRUSCA 밖의 데몬 설정이니, 호스트가 허용하면 둘 중 하나를 켜십시오.
:::

### 엔진 이미지의 라이선스 {#engine-image-licences}

엔진 이미지는 TRUSCA 이미지, Helm 차트, 오프라인 번들 어디에도 들어 있지 않습니다. 엔진 워커가 스캔에 필요할 때 이미지를 받아 각각 별도 컨테이너로 실행합니다. 이미지마다 라이선스 조건이 따로 있으며, 요약은 `THIRD_PARTY_NOTICES.md` 4절에 있습니다.

| 이미지 | 소스 | 라이선스 | 이미지 안의 고지와 소스 |
|---|---|---|---|
| `ghcr.io/sktelecom/bomlens` | https://github.com/sktelecom/bomlens | Apache-2.0 | `/usr/local/lib/sbom/notices/`의 `LICENSE`, `NOTICE`, `THIRD_PARTY_LICENSES.md` |
| `ghcr.io/sktelecom/bomlens-deep-license` | https://github.com/sktelecom/bomlens | Apache-2.0. scancode-toolkit(Apache-2.0 AND CC-BY-4.0)와 LGPL 구성요소(linux/amd64의 7-Zip, intbitset, chardet)가 추가됩니다 | 같은 고지 파일과, `/usr/share/bomlens/sources/`의 7-Zip·intbitset 소스 |
| `ghcr.io/cdxgen/*` (1단계, 실행 계획이 고름) | https://github.com/CycloneDX/cdxgen | cdxgen은 Apache-2.0이고, 나머지는 기반 이미지의 조건을 따릅니다 | 기반 이미지마다 다릅니다. 예를 들어 `cdxgen-python312`는 SUSE BCI 기반이며 SUSE BCI 라이선스가 적용됩니다 |

두 BomLens 이미지의 태그와 다이제스트는 `apps/backend/core/config.py`의 `BOMLENS_IMAGE_DEFAULT`, `BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT`에 있습니다. BomLens 릴리스가 고를 수 있는 cdxgen 이미지와 다이제스트는 BomLens 이미지 안의 `/usr/local/lib/sbom/cdxgen-images.json`에 있습니다. TRUSCA는 BomLens 펌웨어 이미지를 실행하지 않습니다.

미러링하기 전에 이미지의 라이선스 레이블을 확인하려면 다음 명령을 실행하십시오.

<!-- docs-uat: id=dynamic-scan-executor-engine-image-labels kind=shell ctx=host tier=manual waiver=needs-registry-access -->
```bash
skopeo inspect --override-os linux --override-arch amd64 \
  docker://ghcr.io/cdxgen/cdxgen-python312@sha256:<digest> | grep -i -E 'licenses|eula'
```

이 이미지들을 자체 레지스트리로 미러링해 다른 사람이 받을 수 있게 하면, 각 이미지의 라이선스 의무를 미러링한 사람이 집니다. 고지 파일을 함께 전달하는 일과, 라이선스가 요구하는 경우 대응 소스를 제공하는 일이 여기에 포함됩니다.

### 엔진 끄기 {#engine-turn-off}

두 서비스는 `restart: unless-stopped`이므로, 이후에 프로필 없이 `docker-compose up -d`를 실행해도 계속 실행되고 프록시도 Docker API를 계속 열어 둡니다. 명시적으로 중지하고 제거하십시오.

<!-- docs-uat: id=dynamic-scan-executor-engine-off kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# 포털의 .env
SCAN_ENGINE=inprocess

docker-compose up -d    # API, beat, 워커가 SCAN_ENGINE을 다시 읽음

# 엔진 워커가 작업을 마친 뒤: 남은 엔진 컨테이너 제거(워커가 멈출 때 처리 중이던
# 스캔의 컨테이너가 남을 수 있음). <instance>는 TRUSCA_INSTANCE_ID이며,
# 지정하지 않았다면 default
docker ps -aq --filter label=trusca.role=bomlens-engine \
  --filter label=trusca.instance=<instance> | xargs -r docker rm -f

docker-compose --profile bomlens-engine stop worker-engine docker-socket-proxy
docker-compose --profile bomlens-engine rm -f worker-engine docker-socket-proxy
```

이미 엔진 큐에 들어간 스캔은 엔진 워커가 다시 실행될 때까지 대기 상태로 남습니다. 취소하거나, 프로필을 한 번 더 시작해 끝까지 처리하십시오.

### 실행 확인 {#engine-verify}

<!-- docs-uat: id=dynamic-scan-executor-engine-verify-log kind=manual tier=manual -->
- `worker-engine` 로그에 감지한 생태계와 함께 `bomlens_plan`이 남고, 이어서 컨테이너마다 종료 코드와 함께 `bomlens_container_exit`가 남습니다.

<!-- docs-uat: id=dynamic-scan-executor-engine-verify-metadata kind=manual tier=manual -->
- 스캔 메타데이터에 `scan_engine`(BomLens 버전, 이미지 다이제스트, 단계별 상태)과 `scan_engine_run`(계획 상태, 1단계 종료 코드, 강제 종료 여부, 소스 복원 여부)이 기록됩니다.

### 이번 미리 보기에 없는 것 {#engine-not-yet}

- Helm 차트(Kubernetes Job 실행기는 이후 단계입니다).
- 의존성이 바뀌지 않은 경우 이전 스캔의 SBOM을 재사용하는 기능. BomLens 스캔은 항상 엔진을 실행합니다.
- 1단계의 사설 패키지 레지스트리 인증 정보(기존 파이프라인이 읽는 `/etc/trusca/registry` 파일)와 SCANOSS 단계.
- 컨테이너 생성 요청을 검사하는 프록시, 엔진 컨테이너를 별도 런타임으로 격리하는 기능.

## 환경변수 레퍼런스 {#environment-variable-reference}

다음은 `.env.example`의 **Dynamic scan executor** 섹션을 반영합니다. 모두 런타임에 `os.getenv`로 읽힙니다 — `.env`를 편집하고 워커를 재시작하십시오. 정식 레퍼런스는 [환경변수 → 스캔 파이프라인](../reference/env-variables.md#scan-pipeline)을 참고하십시오.

| 키 | 기본값 | 설명 |
|---|---|---|
| `SCAN_EXECUTOR` | `inprocess` | `inprocess`는 cdxgen을 워커 서브프로세스로 실행하고, `local_docker`는 Docker 소켓으로 환경별 사이드카를 띄웁니다(온프레 전용). |
| `SCAN_ENGINE` | `inprocess` | 미리 보기. `bomlens`로 설정하면 소스 스캔을 BomLens 엔진으로 실행합니다([스캔 엔진](#scan-engine) 참고). |
| `SCAN_BOMLENS_IMAGE` | 릴리스에 고정된 이미지 | 실행 계획과 2단계에 쓰는 BomLens 이미지입니다. 바꿀 때는 `latest`가 아닌 태그와 다이제스트를 함께 적어 `repo:tag@sha256:...` 형식으로 지정해야 합니다. |
| `SCAN_BOMLENS_DEEP_LICENSE_IMAGE` | 릴리스에 고정된 이미지 | scancode를 포함한 BomLens 변형 이미지로, `SCANCODE_ENABLED`가 켜져 있으면 2단계에 씁니다. 고정 규칙은 위와 같습니다. |
| `SCAN_BOMLENS_NETWORK` | 비어 있음(compose: `trustedoss-scan-egress`) | 패키지 레지스트리에서 의존성을 해석하는 1단계가 연결할 네트워크입니다. 비어 있으면 1단계를 거부하고, `host`, compose 네트워크 `trustedoss`와 `trustedoss-docker-api`, 워커 자신이 연결된 네트워크도 거부합니다. |
| `SCAN_BOMLENS_STAGE1_IMAGE_PREFIX` | 비어 있음 | 1단계 cdxgen 언어 이미지를 받는 위치로, 이미지 이름 앞부분 전체입니다(예: `registry.example/mirror/cdxgen/`). 비어 있으면 `ghcr.io/cdxgen/`에서 받습니다. 실행 계획에 BomLens의 `CDXGEN_IMAGE_PREFIX`로 넘기며, 다이제스트는 공개된 것을 그대로 쓰므로 미러에는 모든 플랫폼이 같은 다이제스트로 있어야 합니다. 레지스트리 호스트 뒤에 경로가 있어야 하며, cdxgen 이미지만 담은 경로로 한정하십시오(pull-through 캐시라면 `/<proxy-project>/cdxgen/`까지). [오프라인 설치 → 1단계 이미지](./offline-install.md#bomlens-stage1-images)를 참고하십시오. |
| `SCAN_BOMLENS_STAGE1_REPOSITORIES` | 비어 있음(`ghcr.io/cdxgen/`) | 계획의 1단계 이미지가 시작해야 하는 저장소 접두사 목록(쉼표 구분, 각각 `/`로 끝남)입니다. 비어 있으면 `ghcr.io/cdxgen/`을 허용하고, `SCAN_BOMLENS_STAGE1_IMAGE_PREFIX`를 설정했으면 그 접두사만 허용합니다. 이미지 접두사는 항상 허용합니다. |
| `SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT` | `true` | `false`로 바꾸면 1단계 루트 파일시스템에 쓸 수 있습니다. Python의 `pip install` 빌드 준비에 필요하지만, 그 대신 빌드가 쓰기 가능한 이미지에서 실행됩니다. |
| `SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS` | `1800` | 1단계 시간 한도입니다. 넘기면 강제 종료하고 소스를 복원합니다. |
| `SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS` | `1800` | 2단계 시간 한도입니다. 2단계가 강제 종료되면 스캔이 실패합니다. |
| `SCAN_TRIVY_CACHE_VOLUME` | 비어 있음 | 워커의 Trivy 데이터베이스가 있는 명명 볼륨으로, 2단계에 읽기 전용으로 마운트합니다(compose: `<project>_trivy-cache`). |
| `SCAN_LOCAL_DOCKER_ENVS` | `android` | 사이드카로 라우팅할 환경(쉼표 구분). 검출 공백은 `android`뿐이며, 언어별 격리가 필요하면 넓힙니다(위 참고). |
| `SCAN_DOCKER_VOLUME_STRATEGY` | `named` | `named`는 워크스페이스 볼륨만 사이드카에 마운트하고, `volumes_from`은 모든 워커 볼륨을 다시 마운트합니다(아래 ack 없이는 거부하고, BomLens 엔진은 항상 거부). `bind`는 Docker 호스트에서 직접 실행되는 워커용으로, BomLens 엔진만 읽습니다. |
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
