---
id: release-verification
title: 릴리스 이미지 검증
description: 배포된 TRUSCA 컨테이너 이미지가 이 저장소에서 만들어졌음을 확인하고, 이미지 안에 담긴 SLSA provenance와 SBOM을 읽습니다.
sidebar_label: 릴리스 이미지 검증
sidebar_position: 11
---

# 릴리스 이미지 검증

TRUSCA는 릴리스마다 멀티아키텍처 이미지 세 개를 GitHub Container Registry에 배포합니다. 각 이미지는 레지스트리 목록을 믿지 않고도 확인할 수 있는 두 가지를 함께 담고 있습니다.

- **SLSA provenance와 SBOM**. 이미지 자체에 attestation 매니페스트로 붙어 있고, 아키텍처별로 어떻게 빌드했고 무엇이 들어갔는지를 기록합니다.
- **서명된 GitHub build provenance attestation**. 이미지 다이제스트를 그것을 만든 저장소·워크플로·커밋에 묶습니다. GitHub의 OIDC 신원으로 [Sigstore](https://www.sigstore.dev/)를 통해 서명하므로, 우리 쪽 키가 없어도 제3자가 확인할 수 있습니다.

이 문서는 TRUSCA를 직접 호스팅하면서 내가 받은 것이 우리가 배포한 것과 같은지 확인하려는 분을 위한 것입니다.

:::note 범위
여기서 다루는 것은 **컨테이너 이미지**입니다. TRUSCA가 여러분의 프로젝트를 스캔해 만드는 SBOM은 서명 방식이 따로 있는 별개 산출물입니다. [SBOM 서명 검증](./sbom-signature-verification.md)을 보세요. 릴리스를 자른 git 태그는 [릴리스 태그 검증](#릴리스-태그-검증)을 보세요.
:::

## 사전 준비

- Buildx를 포함한 [Docker](https://docs.docker.com/get-docker/). Docker Desktop과 최근 Docker Engine에 함께 들어 있습니다.
- `gh attestation verify`를 쓰기 위한 [GitHub CLI](https://cli.github.com/) 2.49 이상.
- `ghcr.io`로 나가는 접근. GitHub attestation 검증은 Sigstore의 투명성 로그에도 접근합니다. [폐쇄망 배포](#폐쇄망-배포)를 보세요.

## 1. 다이제스트로 고정하기

태그는 다른 이미지를 가리키도록 바뀔 수 있지만 다이제스트는 그렇지 않습니다. 태그를 한 번 풀어 다이제스트를 얻고, 그 뒤로는 다이제스트만 씁니다.

```bash
IMAGE=ghcr.io/trustedoss/trusca-backend
VERSION=0.22.5   # 검증하려는 릴리스

DIGEST=$(docker buildx imagetools inspect "${IMAGE}:${VERSION}" \
  --format '{{ json .Manifest }}' | jq -r '.digest')
echo "${IMAGE}@${DIGEST}"
```

이 다이제스트로 pull 하고, 배포를 고정하고 싶다면 compose 파일이나 Helm 값에도 태그 대신 다이제스트를 적습니다.

## 2. 누가 만들었는지 확인하기

`gh attestation verify`는 그 다이제스트에 붙은 서명된 provenance를 받아 와, 기대하는 저장소에서 나온 것인지 검사합니다.

```bash
gh attestation verify "oci://${IMAGE}@${DIGEST}" --repo trustedoss/trusca
```

성공하면 검증된 predicate와 이미지를 만든 워크플로를 출력합니다. 그 저장소의 릴리스 워크플로가 만든 다이제스트가 아니면 명령이 0이 아닌 값으로 끝납니다.

같은 저장소의 다른 워크플로가 만든 attestation까지 걸러 내려면 워크플로도 함께 고정합니다.

```bash
gh attestation verify "oci://${IMAGE}@${DIGEST}" \
  --repo trustedoss/trusca \
  --signer-workflow trustedoss/trusca/.github/workflows/release.yml
```

## 3. provenance와 SBOM 읽기

둘 다 이미지 인덱스 안에 들어 있어 레지스트리 말고는 어디에도 접근하지 않습니다.

```bash
# 아키텍처별 빌드 방식: 빌드 종류, 소스 커밋, 파라미터
docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --format '{{ json .Provenance }}'

# 아키텍처별 내용물
docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --format '{{ json .SBOM }}'
```

둘 다 플랫폼(`linux/amd64`, `linux/arm64`)을 키로 하는 객체를 돌려주므로, 실제로 돌리는 플랫폼을 골라 봅니다.

```bash
docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --format '{{ json .Provenance }}' \
  | jq '.["linux/amd64"].SLSA.buildDefinition'
```

여기의 SBOM은 베이스 레이어를 포함해 빌드된 이미지를 기술합니다. GitHub Release에 첨부되는 소스 트리 CycloneDX SBOM과는 다른 문서이고, 그쪽은 저장소 자체의 의존성 명세를 다룹니다. 둘 다 배포하며 답하는 질문이 다릅니다.

## 4. 각 검사가 증명하는 것

| 검사 | 답하는 것 | 답하지 않는 것 |
|---|---|---|
| 다이제스트 고정 (1절) | 정확히 이 바이트를 실행하고 있다 | 누가 만들었는지 |
| `gh attestation verify` (2절) | 이 다이제스트는 이 저장소의 워크플로가 알려진 커밋에서 만들었다 | 그 커밋을 믿을 수 있는지 |
| provenance (3절) | 아키텍처별로 어떻게 조립했는지 | 빌드 입력 자체가 검증됐는지 |
| 이미지 SBOM (3절) | 이미지에 어떤 패키지가 있는지 | 알려진 취약점이 있는지. 그것은 이미지를 스캔해서 봅니다 |

어느 것도 릴리스에서 무엇이 바뀌었는지 살펴보는 일을 대신하지는 않습니다. 이 검사들이 세우는 것은 받은 산출물이 우리가 만든 산출물이라는 사실입니다.

## 폐쇄망 배포

3절의 attestation은 이미지 인덱스 안에 함께 이동하므로, 내부 미러를 포함해 이미지를 pull 할 수 있는 곳이면 어디서나 동작합니다.

`gh attestation verify`는 다릅니다. 기본값은 GitHub에서 attestation을 받아 Sigstore의 공개 신뢰 자료로 검사하는 것이므로, 밖으로 나갈 수 없는 호스트에서는 그 둘을 먼저 들여와야 합니다.

릴리스가 attestation을 이미지와 함께 레지스트리에도 올리기 때문에, 이미지 referrer까지 복제한 내부 미러라면 그것을 직접 쓸 수 있습니다.

```bash
gh attestation verify "oci://${IMAGE}@${DIGEST}" --repo trustedoss/trusca --bundle-from-oci
```

Sigstore에도 닿을 수 없다면, 연결된 장비에서 신뢰 자료를 내보내 파일 두 개를 함께 옮깁니다.

```bash
# 연결된 장비에서
gh attestation trusted-root > trusted_root.jsonl
gh attestation download "oci://${IMAGE}@${DIGEST}" --repo trustedoss/trusca

# 폐쇄망 호스트에서, 옮겨 온 파일로
gh attestation verify "oci://${IMAGE}@${DIGEST}" \
  --bundle sha256:<digest>.jsonl \
  --custom-trusted-root trusted_root.jsonl \
  --repo trustedoss/trusca
```

가장 간단한 방법은 여전히 연결된 장비에서 검증하고 그 **다이제스트**를 내부로 옮기는 것입니다. 다이제스트는 보증을 그대로 지니고 다닙니다.

## 릴리스 태그 검증

앞의 이미지 검증은 워크플로가 그것을 만들었다는 것을 알려 주고, 태그는 사람이
그 릴리스를 잘랐다는 것을 알려 줍니다. 답하는 질문이 다르므로 두 번째가
필요하다면 둘 다 확인하세요.

:::note 아직 모든 릴리스에 서명이 붙지는 않습니다
태그 서명은 도입 중입니다. 릴리스 워크플로가 이미 태그마다 검사해서 서명이
없으면 알리지만, 아직 릴리스를 거부하지는 않습니다. 서명이 붙지 않은 릴리스에서는
아래 명령이 그 사실을 알려 주며, 그것이 통과로 처리하는 것보다 정직한 결과입니다.
:::

릴리스 태그는 SSH로 서명합니다. 검증에는 어느 키가 유효한지 알려 주는 저장소의
allowed-signers 파일이 필요하므로, tarball이 아니라 저장소를 클론해서 확인합니다.

```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca
git config gpg.ssh.allowedSignersFile .github/allowed_signers
git verify-tag v0.22.5
```

정상이면 서명자가 함께 표시됩니다. 확인해야 할 것은 좋은 서명이라는 말이 아니라
서명자입니다.

```
Good "git" signature for release@trusca with ED25519 key SHA256:...
```

`Good "git" signature` 뒤에 서명자가 없고 `No principal matched`가 나오면 릴리스
담당자의 키가 아닌 키로 서명된 것이므로 실패로 다루세요. allowed-signers 파일의
가치가 바로 이 구분에 있습니다. 그냥 `git verify-tag`만 하면 공격자가 방금 만든
키를 포함해 유효한 키라면 무엇이든 통과합니다.

## 패키지 목록의 `unknown/unknown` 항목

GHCR 패키지 화면은 `linux/amd64`, `linux/arm64` 옆에 `unknown/unknown` 아키텍처를 하나 더 보여 줍니다. 그 항목이 attestation 매니페스트입니다. attestation은 이미지 인덱스 안에 매니페스트로 저장되는데, 어떤 런타임도 실행 대상으로 오인하지 않도록 플랫폼을 일부러 `unknown/unknown`으로 둡니다.

레지스트리 화면의 표시 방식일 뿐 이미지가 하나 더 있는 것이 아닙니다. 태그로 pull 하든 다이제스트로 pull 하든 맞는 아키텍처로 해석됩니다. attestation을 켜기 전에 배포된 릴리스에는 이 항목이 없으므로, 목록에 이 항목이 없다면 그냥 더 오래된 이미지입니다.
