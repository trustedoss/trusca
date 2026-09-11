---
id: offline-install
title: 오프라인/망 분리 설치 번들
description: 이미지 전체와 Trivy DB, Helm 차트, Compose 파일을 하나의 tar로 묶어 인터넷 접근이 전혀 없는 환경에서도 설치합니다.
sidebar_label: 오프라인 설치 번들
sidebar_position: 7.6
---

# 오프라인/망 분리 설치 번들

`scripts/install.sh`는 보통 레지스트리에서 이미지를 pull하고, 워커가 처음
부팅할 때 `ghcr.io`에서 Trivy 취약점 DB를 내려받게 둡니다. 아웃바운드
네트워크 경로가 전혀 없는 호스트에서는 둘 다 불가능합니다. 이 문서는
연결된 호스트에서 설치에 필요한 모든 것을 하나의 tar로 묶는
`scripts/bundle-offline.sh`와, 망 분리 대상 호스트에서 그 tar를 소비하는
`scripts/install.sh --offline <bundle.tar>`를 다룹니다.

이 문서는 [사설 레지스트리](./private-registries.md)(도달은 가능하지만
자격증명이 필요한 의존성 미러에 cdxgen을 인증시키는 문제)나
[취약점 데이터](./vulnerability-data.md)의 Path A/B(배포의 나머지 부분은 이미
네트워크에 접근할 수 있는 상태에서 Trivy DB *만* 미러링하거나 수동으로
채우는 문제)와는 다른 문제를 다룹니다. 대상 호스트가 레지스트리든 Trivy DB
엔드포인트든 무엇이든 전혀 접근할 수 없을 때 이 문서를 씁니다.

## 번들에 들어가는 것

| 구성요소 | 소스 | 항상 포함되는가? |
|---|---|---|
| `docker-compose.yml`이 pull하는 이미지 6개 | `docker save` | 예 |
| Trivy 취약점 DB 스냅샷 | `trivy --download-db-only` | 최선 노력 - 빌드 호스트에 `trivy`가 없거나 다운로드가 실패하면 생략됨 |
| Trivy Java DB 스냅샷 | `trivy --download-java-db-only` | 최선 노력 - 있으면 더 좋은 부가 요소. 없어도 jar-Maven 좌표 매칭에만 영향 |
| `charts/trustedoss/` | 복사 | 예 |
| `docker-compose.yml`, `docker-compose.dev.yml`, `.env.example` | 복사 | 예 |

여섯 개 이미지는 traefik, postgres, redis, `trusca-backend`,
`trusca-backend-worker`, `trusca-frontend`입니다. 이 기능이 시작된 이슈는
"이미지 네 개"라고 적었지만, 짐작이 아니라 `docker-compose.yml`을 실측한
결과 compose 스택이 실제로 pull하는 이미지 참조는 여섯 개입니다
(backend-worker 이미지는 `worker-scan`·`worker-default`·`beat` 세 서비스가
재사용하지만, save·load할 이미지로는 여전히 하나입니다). 망 분리된
`docker-compose up`은 이 여섯 개가 전부 있어야 하고, 하나라도 없으면
그 지점에서 멈추므로 번들은 여섯 개를 모두 담습니다.

## 1. 연결된 호스트에서 번들 만들기

빌드 서버, 개발자 노트북, 인터넷 아웃바운드가 있는 배스천 등 `docker`가(가능하면
`trivy`도) 설치돼 있고 `ghcr.io`에 접근할 수 있는 곳에서 실행합니다.

```bash
bash scripts/bundle-offline.sh
```

`IMAGE_REGISTRY`/`IMAGE_TAG`는 저장소 루트에 `.env`가 있으면 거기서
기본값을 가져오고(`install.sh`·`backup.sh`와 같은 관례), 없으면
`docker-compose.yml` 자체의 기본값으로 대체됩니다. 필요하면 명시적으로
오버라이드합니다.

```bash
bash scripts/bundle-offline.sh --tag 0.22.0 --registry ghcr.io/trustedoss
```

출력 경로는 `-o`/`--output`으로 바꿀 수 있습니다.

```bash
bash scripts/bundle-offline.sh -o /mnt/usb/trusca-offline-bundle.tar
```

스크립트는 로컬에 없는 이미지가 있으면 먼저 pull한 뒤 여섯 개를 모두
`docker save`로 하나의 tar에 담고, Trivy DB(및 최선 노력으로 Java DB)를
스크래치 캐시 디렉터리로 내려받고, Helm 차트와 세 개의
Compose/env 파일을 복사한 다음 전체를 하나의 출력 tar로 묶습니다. 진행하는
동안 각 구성요소의 크기를 출력하고, 마지막에 최종 tar의 내역과 크기를
출력합니다.

중간 단계가 실패하면(디스크 부족, `docker save` 오류, 다운로드 중 네트워크
끊김) 스크립트는 스크래치 디렉터리를 정리하고 부분적으로 만들어진 출력을
지웁니다 - 최종 경로에 잘린 tar를 남겨서 다음 실행이 그것을 정상 번들로
오인하게 두지 않습니다.

:::tip 릴리스마다 다시 만들기
번들은 하나의 `IMAGE_TAG`에 묶여 있습니다. 망 분리 환경에서 설치하거나
업그레이드하려는 버전마다 새로 번들을 만드세요 - 재사용할 수 있는
"latest" 번들은 없습니다.
:::

## 2. 망 분리 구간을 넘겨 전송하기

여러분의 망 분리 환경이 쓰는 승인된 경로(USB, 사내 아티팩트 저장소, 관리되는
파일 전송 절차)로 tar와 그 `.sha256` 사이드카 **둘 다** 복사합니다. 번들은
빌드에 쓰인 이미지 태그와 아키텍처 외에는 빌드 머신에 대한 의존성이 없습니다
- 대상과 같은 CPU 아키텍처(amd64/arm64)의 호스트에서 만드세요.

:::danger 신뢰할 수 있는 번들만 설치하세요
`install.sh --offline`은 tar 안에 든 것을 그대로 `docker load`해서 일반
설치와 같은 권한으로 실행합니다 - 샌드박스가 없습니다. 아래 3단계의
체크섬은 번들을 이미 가진 *뒤에* 생기는 전송 중 손상이나 변조를 잡아낼 뿐,
처음부터 신뢰하면 안 될 출처의 번들 문제를 해결해 주지 않습니다.
`--offline`은 직접 만든 번들이나, 출처를 다른 방법으로 확인할 수 있는
번들(내가 관리하는 사내 빌드 파이프라인, 직접 물어볼 수 있는 신뢰하는
동료)에만 실행하세요 - 출처를 확인할 수 없는 번들에는 절대 쓰지 마세요.
:::

## 3. 망 분리 대상에서 설치하기

저장소를 대상 호스트에 clone하거나 복사한 뒤(번들은 Compose 파일의 편의용
복사본을 담고 있지만, `scripts/install.sh` 자체는 그래도 그 호스트에
있어야 합니다) 다음을 실행합니다.

```bash
bash scripts/install.sh --offline /path/to/trusca-offline-bundle-0.22.0.tar
```

완전히 무인으로 진행되는 망 분리 설치를 원하면 `--no-prompt`와 함께
씁니다(`--no-prompt`가 읽는 `INSTALL_*` 비대화형 값 전체 목록은
[UAT 체크리스트](./../installation/uat-checklist.md) 참고).

```bash
bash scripts/install.sh --no-prompt --offline /path/to/trusca-offline-bundle-0.22.0.tar
```

`--offline`을 지정하면 `install.sh`는 다음을 합니다.

1. 번들을 `.sha256` 사이드카에 대해 검증합니다(체크섬이 안 맞거나 사이드카가
   없으면 즉시 중단됩니다 - 검증 안 된 번들로는 아래 어떤 단계도 진행하지
   않습니다). 그리고 무엇도 풀기 전에 tar 안의 모든 멤버 경로가 경로 조작이나
   절대경로 형태인지 확인합니다.
2. 번들을 스크래치 디렉터리에 풀고 여섯 개 이미지를 `docker load`한 뒤, 번들
   자체의 `MANIFEST.txt`가 나열한 모든 이미지가 실제로 로컬 Docker 데몬에
   있는지 확인합니다 - 이 단계가 `docker-compose pull`을 완전히 대체하므로,
   일반 설치 흐름의 어떤 부분도 레지스트리에 접근하지 않습니다.
3. `.env`의 `IMAGE_REGISTRY`/`IMAGE_TAG`를 번들의 `MANIFEST.txt`가 기록한
   값으로 고정합니다 - `.env.example`의 기본값(또는 `INSTALL_*` 오버라이드)이
   번들이 실제로 빌드된 태그와 같을 이유가 없고, `docker-compose.yml`은
   방금 로드된 이미지로 정확히 해석돼야 하지, 어긋난 태그를 네트워크에서
   다시 pull하면 안 됩니다.
4. 번들에 Trivy DB 스냅샷이 있으면, 스택이 시작되기 전에 거기서
   `trivy-cache` 네임드 볼륨을 채웁니다(`worker-scan` 서비스에 대해
   `docker-compose run`을 실행하는 방식이라, 이 배포의
   `COMPOSE_PROJECT_NAME`이 무엇이든 올바른 볼륨에 안착합니다). 그리고
   `.env`에 `TRIVY_DB_BOOTSTRAP_ON_START=false`를 설정해, 재시작마다
   워커가 도달할 수 없는 `ghcr.io`를 반복해서 재시도하지 않게 합니다.
5. 나머지 마법사는 일반 설치와 동일하게 진행됩니다 - 단계별
   기동, alembic 마이그레이션, 슈퍼관리자 부트스트랩은 그대로입니다.

번들에 Trivy DB가 없다면(빌드 호스트에 `trivy`가 없었거나 다운로드가
실패한 경우) `install.sh`는 워커의 일반 온라인 부트스트랩을 그대로 켜 둔
채 둡니다 - 그 호스트가 다시 네트워크에 접근할 수 있게 되거나,
[취약점 데이터의 Path B](./vulnerability-data.md#air-gapped)를 따라 캐시를
따로 채우기 전까지는 취약점 매칭을 쓸 수 없습니다.

## Helm/Kubernetes

번들은 Helm 기반 배포를 위해 `charts/trustedoss/`를 담고 있지만,
`docker load`는 그 호스트 하나의 로컬 Docker 데몬만 채웁니다 - Kubernetes
클러스터에는 도움이 되지 않습니다. kubelet은 워크스테이션의
`docker load`가 아니라 각자의 컨테이너 런타임을 통해 레지스트리에서
이미지를 pull하기 때문입니다. 망 분리 환경에서 Helm으로 설치하려면
모든 노드가 접근할 수 있는 사내 레지스트리가 추가로 필요하고, `helm
install` 전에 여섯 개 이미지를 그 레지스트리로 올려야 합니다(번들을
로컬에서 `docker load`하고, 각 이미지를 사내 레지스트리용으로 재태깅한
뒤 `docker push`). 그러면 차트의 `image.repository` 값들이 그 사내
레지스트리를 가리키게 되는데, 이는 [사설 레지스트리](./private-registries.md)
문서가 컨테이너 스캔 자격증명 경로에서 설명하는 것과 같은 방식입니다.

## 알려진 한계

- **`.sha256` 사이드카는 필수이고 선택이 아닙니다.** `install.sh --offline`은
  번들과 맞는 사이드카 없이는 진행을 거부합니다 - 건너뛰는 플래그는 없습니다.
  tar만 있고 사이드카를 잃어버렸거나 함께 옮기지 못했다면, 대상 호스트에서
  직접 `sha256sum bundle.tar > bundle.tar.sha256`으로 다시 만드세요. 다만
  이건 그 tar가 방금 만든 체크섬과 내부적으로 일치한다는 것만 증명할 뿐,
  `bundle-offline.sh`가 원래 만들어 낸 그 번들이라는 걸 증명하진 않으니, 위
  2단계의 규칙대로 이미 신뢰하는 번들에만 이렇게 하세요.
- **호스트 하나의 Docker 데몬만.** 대상에서 `docker load`는 로컬 Docker
  데몬만 채웁니다. 다중 노드 배포(Helm/Kubernetes, 또는 `docker load`를
  실행한 것과 다른 컨테이너 런타임을 쓰는 Compose 호스트)에서는 모든
  노드에 이미지를 전달할 방법이 여전히 필요합니다 - 위 Helm/Kubernetes
  절을 참고하세요.
- **아키텍처를 탑니다.** 설치하려는 것과 같은 CPU 아키텍처(amd64/arm64)에서
  번들을 만드세요. `docker save`는 로컬 데몬이 pull한 그대로를 담을 뿐,
  멀티 아키텍처 매니페스트를 담지 않습니다.
- **Trivy DB는 최선 노력이지 보장이 아닙니다.** `trivy`가 없는 빌드
  호스트나 `ghcr.io/aquasecurity/trivy-db` 자체에 접근할 수 없는
  호스트도 유효한 번들을 만들어 내지만, Trivy DB 스냅샷만 빠진
  번들입니다. tar 안의 `MANIFEST.txt`에 포함 여부가 기록됩니다.
- **압축하지 않습니다.** 이미지와 Trivy DB는 이미 내부적으로 압축돼
  있으므로, 번들은 `.tar.gz`가 아니라 평범한 `tar`입니다. 수 기가바이트짜리
  콘텐츠를 다시 압축하는 데 CPU를 태우는 것에 비해 얻는 크기 이득이
  작기 때문입니다.
- **저장소 자체는 여전히 필요합니다.** 번들은 세 개의 Compose/env 파일의
  편의용 복사본을 담고 있지만, `scripts/install.sh`와
  `scripts/bundle-offline.sh` 자체는 번들 안에 들어 있지 않습니다.
  망 분리 환경이 허용하는 방법(소스 tar, 사내 Git 미러, 번들과 같은 전송
  경로)으로 저장소를 대상 호스트에 가져오세요.
