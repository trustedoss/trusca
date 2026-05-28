---
id: sbom-signature-verification
title: SBOM 서명 검증 (cosign)
description: TrustedOSS Portal의 SBOM 서명 번들을 내려받아 cosign verify-blob으로 외부에서 검증하고, key-based·keyless 서명을 위한 운영자 키를 설정합니다.
sidebar_label: SBOM 서명 검증
sidebar_position: 10
---

# SBOM 서명 검증 (cosign)

모든 소스 스캔은 CycloneDX SBOM을 [cosign](https://docs.sigstore.dev/cosign/overview/)으로 서명하고 in-toto / SLSA provenance attestation을 생성합니다. 이 문서는 소비자가 서명 산출물을 내려받아 포털 **외부**에서 검증하는 방법과, 운영자가 서명 키를 설정하는 방법을 설명합니다.

:::note 대상 독자
두 부류입니다.

- **검증자**(1~4절) — TrustedOSS SBOM을 소비하며 그것이 변조되지 않았고 알려진 배포에서 서명되었음을 증명하려는 모든 사람. 셸 사용과 CLI 바이너리 설치 능력을 가정합니다.
- **운영자**(5절) — 포털을 배포하고 서명 키를 소유하는 사람. Linux + Docker Compose 숙련과 [환경 변수](./env-variables.md) 이해를 가정합니다.
:::

## 사전 조건

검증을 위해서는:

1. 프로젝트가 속한 팀에서 최소 **Developer** [역할](./glossary.md#rbac-역할)을 가진 TrustedOSS 계정. 서명 엔드포인트는 SBOM 내보내기와 동일한 접근 제어를 재사용하므로 외부인은 `404`를 봅니다.
2. 프로젝트에 **성공한(succeeded)** 스캔이 하나 이상 있고, 그 스캔을 실행한 배포에 서명이 설정되어 있어야 합니다([5절](#5-운영자-키-설정) 참고). 서명되지 않은 스캔에는 서명 산출물이 없습니다.
3. 검증을 수행하는 머신에 [cosign](https://docs.sigstore.dev/cosign/installation/) 설치([2절](#2-cosign-설치) 참고).

## 1. SBOM에 서명하는 이유

[SBOM](./glossary.md#sca-핵심)은 릴리스 *안에 무엇이 들어 있는지*를 소비자에게 알려 줍니다. **서명**은 SBOM만으로는 답할 수 없는 두 가지를 추가로 답합니다.

- **무결성(integrity)** — 배포가 생성한 후 SBOM 바이트가 변경되었는가? 정확한 바이트에 대한 서명은 모든 변조를 탐지합니다.
- **출처(provenance)** — SBOM이 *어떻게*, 누구에 의해 생성되었는가? [in-toto](https://in-toto.io/) / [SLSA](https://slsa.dev/) provenance attestation은 빌드 플랫폼 식별자와 버전을 기록합니다.

이는 [행정명령 14028](https://www.cisa.gov/topics/cyber-threats-and-advisories/cybersecurity-best-practices/secure-by-design/sbom), [CISA 2025 SBOM 최소 요소](https://www.cisa.gov/sbom), [NTIA 최소 요소](https://www.ntia.gov/page/software-bill-materials)가 요구하는 공급망 보안 기대치입니다 — 소비자는 산출물이 도착한 경로를 신뢰하지 않고도 진위를 검증할 수 있어야 합니다.

### key-based vs keyless

cosign은 두 가지 신뢰 모델을 지원합니다. TrustedOSS는 둘 다 지원하며, 자체 호스팅·온프레미스·에어갭 배포에서는 **key-based가 기본값**입니다.

| 모델 | 배포가 서명하는 방식 | 검증자에게 필요한 것 | 사용 시점 |
|---|---|---|---|
| **key-based**(기본) | cosign 키페어. 개인 키가 서명하고 공개 키를 배포 | `cosign.pub`(공개 키) | 자체 호스팅·온프레미스·에어갭 — 인터넷 의존성 없음 |
| **keyless**(옵트인) | OIDC 신원에 바인딩된 단기 [Fulcio](https://docs.sigstore.dev/certificate_authority/overview/) 인증서. 서명은 [Rekor](https://docs.sigstore.dev/logging/overview/)에 기록 | Fulcio **인증서** + 기대 **신원** + **OIDC 발급자** | OIDC 공급자와 Sigstore 인스턴스로의 아웃바운드 접근이 있는 CI 기반 배포 |

검증 명령은 두 모델이 다릅니다 — 둘 다 [4절](#4-검증)에서 다룹니다.

:::info best-effort 서명
서명은 best-effort입니다. 워커에 cosign 바이너리가 없거나, 키가 설정되지 않았거나, cosign이 실패하면 스캔은 그래도 **성공**하지만 SBOM은 서명되지 않은 채로 남습니다(구조화된 경고가 로깅됩니다). 서명되지 않은 스캔에는 서명 산출물이 없으므로 다운로드 엔드포인트는 `404`를 반환합니다 — [트러블슈팅](#트러블슈팅) 참고.
:::

## 2. cosign 설치

검증을 수행하는 머신에 cosign을 설치합니다. cosign **v2.x**를 권장합니다(아래 명령은 v2 CLI를 가정합니다).

```bash
# macOS (Homebrew)
brew install cosign

# Linux (바이너리)
curl -sSfL -o cosign \
  "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64"
chmod +x cosign && sudo mv cosign /usr/local/bin/

# 설치 확인
cosign version
```

다른 플랫폼은 [cosign 설치 가이드](https://docs.sigstore.dev/cosign/installation/)를 참고하십시오.

## 3. 서명 산출물 다운로드

서명 표면은 항상 프로젝트의 **가장 최근 성공 스캔(latest succeeded)** 을 기술합니다 — [SBOM 내보내기](../user-guide/sbom.md)가 제공하는 스캔과 동일하므로 서명된 바이트와 내보낸 SBOM이 정확히 일치합니다.

### 번들(권장)

**서명 번들**은 오프라인 검증에 필요한 모든 것을 담은 단일 zip이므로 개별 파일보다 권장합니다. zip은 다음을 포함합니다.

| 파일 | 항상 포함? | 용도 |
|---|---|---|
| `sbom-<slug>.cdx.json` | 예 | CycloneDX SBOM — 서명된 바이트 |
| `sbom-<slug>.cdx.json.sig` | 예 | SBOM에 대한 detached cosign 서명 |
| `cosign.pub` | key-based만 | cosign 공개 키(key-based 검증) |
| `sbom-<slug>.cdx.json.cert.pem` | keyless만 | Fulcio 서명 인증서(keyless 검증) |
| `sbom-<slug>.intoto.jsonl` | attestation 성공 시 | in-toto / SLSA provenance attestation |
| `sbom-<slug>.attest.cert.pem` | keyless + attestation | attestation용 Fulcio 인증서 |
| `VERIFY.md` | 예 | 번들 내용에 맞춘 검증 안내 |

`<slug>`는 프로젝트의 URL slug이며, zip 자체의 이름은 `sbom-signature-<slug>.zip`입니다.

API에서 내려받습니다(플레이스홀더 치환):

```bash
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://<your-domain>/v1/projects/${PROJECT_ID}/sbom/signature-bundle"
```

`-OJ`는 서버가 제공하는 이름(`sbom-signature-<slug>.zip`)으로 파일을 저장합니다. `<your-domain>`은 배포 호스트로, `${PROJECT_ID}` / `${TRUSTEDOSS_API_KEY}`는 실제 값으로 치환하십시오.

:::tip VERIFY.md를 먼저 읽으십시오
각 번들에는 *해당* 번들의 **정확한** 파일명을 사용하는 명령이 담긴 `VERIFY.md`가 들어 있습니다(key-based vs keyless, attestation 포함 여부 반영). 번들 내용과 이 문서가 어긋날 때는 `VERIFY.md`를 신뢰하십시오 — 실제로 받은 산출물로부터 생성됩니다.
:::

### 개별 산출물

파일 하나만 필요하면 각각 별도 엔드포인트로도 제공됩니다. 모두 동일한 Developer 접근 권한을 요구하며, 가장 최근 성공 스캔의 산출물을 반환합니다(없으면 `404`).

| 엔드포인트 | 반환 | 비고 |
|---|---|---|
| `GET /v1/projects/{project_id}/sbom/signature` | `sbom-<slug>.cdx.json.sig` | detached 서명 |
| `GET /v1/projects/{project_id}/sbom/public-key` | `cosign.pub` | key-based 배포 전용. keyless면 `404` |
| `GET /v1/projects/{project_id}/sbom/certificate` | `sbom-<slug>.cdx.json.cert.pem` | keyless 배포 전용. key-based면 `404` |
| `GET /v1/projects/{project_id}/sbom/attestation` | `sbom-<slug>.intoto.jsonl` | SLSA provenance attestation |
| `GET /v1/projects/{project_id}/sbom/attestation-certificate` | `sbom-<slug>.attest.cert.pem` | keyless attestation 전용 |

SBOM 자체는 기존 [SBOM 내보내기 엔드포인트](../user-guide/sbom.md#api에서-다운로드)(`GET /v1/projects/{project_id}/sbom?format=cyclonedx-json`)에서 가져옵니다.

:::note 공개 자료만 제공
이 엔드포인트들은 **공개** 산출물만 제공합니다 — SBOM, 서명, Fulcio 인증서, attestation, cosign **공개** 키. 개인 서명 키와 그 비밀번호는 포털이 읽거나 반환하거나 로깅하지 않습니다. 공개 키 엔드포인트는 개인 키처럼 보이는 것은 제공을 거부합니다.
:::

## 4. 검증

번들의 압축을 풀고, 배포의 서명 모델에 맞는 명령을 실행합니다.

```bash
unzip sbom-signature-<slug>.zip -d sbom-verify
cd sbom-verify
```

### key-based(기본)

```bash
cosign verify-blob \
  --key cosign.pub \
  --signature sbom-<slug>.cdx.json.sig \
  sbom-<slug>.cdx.json
```

성공 시 다음이 출력됩니다.

```
Verified OK
```

### keyless(Fulcio)

keyless 검증은 서명자의 **신원**과 **OIDC 발급자**를 추가로 고정합니다 — 운영자가 공개한 값(예: CI 워크플로 신원과 발급자 URL)으로 치환하십시오.

```bash
cosign verify-blob \
  --certificate sbom-<slug>.cdx.json.cert.pem \
  --certificate-identity <expected-identity> \
  --certificate-oidc-issuer <expected-issuer> \
  --signature sbom-<slug>.cdx.json.sig \
  sbom-<slug>.cdx.json
```

성공 시 `Verified OK`를 출력합니다. 불일치 — SBOM 바이트 변조, 잘못된 키/인증서, 일치하지 않는 신원 — 시 0이 아닌 코드로 종료하며 다음과 같은 오류를 냅니다.

```
Error: verifying blob: invalid signature when validating ASN.1 encoded signature
```

`Verified OK`는 SBOM 바이트가 온전하며 **또한** 이 배포의 서명 신원으로 서명되었음을 의미합니다.

### provenance attestation 확인

번들에 `sbom-<slug>.intoto.jsonl`이 있으면 페이로드를 디코딩하여 SBOM이 어떻게 생성되었는지 읽습니다 — in-toto Statement는 빌드 플랫폼 `builder.id` / `builder.version`과 SBOM 생성 컨텍스트를 담습니다.

```bash
jq -r '.payload' sbom-<slug>.intoto.jsonl | base64 -d | jq .
```

attestation을 (디코딩만이 아니라) 암호학적으로 검증하려면:

```bash
# key-based
cosign verify-blob-attestation \
  --key cosign.pub \
  --bundle sbom-<slug>.intoto.jsonl \
  sbom-<slug>.cdx.json

# keyless
cosign verify-blob-attestation \
  --certificate sbom-<slug>.attest.cert.pem \
  --certificate-identity <expected-identity> \
  --certificate-oidc-issuer <expected-issuer> \
  --bundle sbom-<slug>.intoto.jsonl \
  sbom-<slug>.cdx.json
```

### 검증 확인

- `cosign verify-blob`이 `Verified OK`를 출력하고 `0`으로 종료합니다.
- `jq`가 attestation 페이로드를 디코딩하고 `predicate.builder.id`가 운영자가 설정한 빌더(`SLSA_BUILDER_ID`)와 일치합니다.
- SBOM을 다시 내려받으면 byte-identical 파일이 생성되므로(내보내기는 byte-stable) 동일 서명으로 다시 검증됩니다.

  ```bash
  sha256sum sbom-<slug>.cdx.json
  # → verify-blob 중 cosign이 보고한 digest와 일치
  ```

## 5. 운영자 키 설정

이 절은 포털을 배포하는 사람을 위한 것입니다. 한 가지 모델을 선택하십시오.

### key-based(기본)

1. 번들된 헬퍼로 cosign 키페어를 생성합니다. `cosign.key`(암호화된 개인 키)와 `cosign.pub`(공개 키)를 쓰고 `.env` 배선을 출력합니다.

   ```bash
   bash scripts/cosign-keygen.sh --out ./secrets/cosign
   ```

   cosign은 개인 키를 저장 시 암호화할 비밀번호를 묻습니다(또는 export된 `COSIGN_PASSWORD`를 읽습니다). cosign이 PATH에 없으면 cosign을 포함하는 워커 컨테이너 안에서 헬퍼를 실행하십시오.

   ```bash
   docker-compose run --rm worker bash scripts/cosign-keygen.sh
   ```

2. 개인 키 비밀번호를 앱의 Fernet 키로 암호화하여 `.env`에 평문이 아닌 ciphertext로 둘 수 있게 합니다. 서명 시 앱이 복호화하는 것과 동일한 키를 쓰도록 워커 **안에서** 실행하십시오.

   ```bash
   docker-compose run --rm worker \
     python -c "import sys;from core.crypto import encrypt_secret;print(encrypt_secret(sys.argv[1]))" 'YOUR_KEY_PASSWORD'
   ```

   비밀번호 없는 키도 허용됩니다 — `COSIGN_KEY_PASSWORD_ENCRYPTED`를 비워 두십시오.

3. 키를 `.env`에 배선합니다(워커는 `COSIGN_KEYS_HOST_PATH`를 `/cosign`에 read-only로 마운트합니다).

   ```bash
   COSIGN_KEYLESS=false
   COSIGN_KEY_PATH=/cosign/cosign.key
   COSIGN_KEY_PASSWORD_ENCRYPTED=<2단계의 ciphertext 붙여넣기>
   COSIGN_KEYS_HOST_PATH=./secrets/cosign
   ```

4. **`cosign.pub`을 검증자에게 배포하십시오** — 릴리스 옆에 게시하거나 신뢰할 수 있는 경로로 전달합니다. 검증자는 [공개 키 엔드포인트](#개별-산출물)에서도 받을 수 있지만, 대역 외 사본이 있으면 포털 접근 없이도 검증할 수 있습니다.

:::warning 개인 키를 보호하십시오
`cosign.key`는 배포의 서명 권한입니다. 버전 관리에서 제외하고(번들된 `.gitignore`가 `secrets/`를 제외), 워커에 **read-only**로 마운트하며, 안전하게 백업하십시오. 포털은 개인 키나 그 비밀번호를 읽거나 반환하거나 로깅하지 않습니다 — 운영 도구도 그래야 합니다.
:::

### keyless(옵트인)

keyless 서명은 키페어가 **필요 없습니다**. cosign이 자체 OIDC 신원(앰비언트 CI 토큰 또는 설정된 공급자)을 구동하고 서명을 Rekor에 기록합니다. 다음을 설정합니다.

```bash
COSIGN_KEYLESS=true
```

**사설** Sigstore 배포의 경우 워커 환경에 `COSIGN_OIDC_ISSUER`, `SIGSTORE_FULCIO_URL`, `SIGSTORE_REKOR_URL`도 설정하십시오. 검증자가 `cosign verify-blob`에 `--certificate-identity` / `--certificate-oidc-issuer`를 전달할 수 있도록 **기대 신원**과 **OIDC 발급자**를 검증자에게 공개하십시오.

### provenance 빌더 신원

attestation은 provenance에 빌더 신원을 새깁니다. 검증자가 provenance를 알려진 빌더에 고정할 수 있도록 다음을 설정하십시오.

| 키 | 기본값 | 용도 |
|---|---|---|
| `SLSA_BUILDER_ID` | 벤더 중립 URI | provenance `builder.id`에서 이 빌드 플랫폼을 명명하는 URI |
| `TRUSTEDOSS_VERSION` | 번들된 포털 버전 | `builder.version`과 SBOM 생성 컨텍스트에 새겨짐 |

전체 키 목록과 런타임 의미는 [환경 변수 → cosign signing](./env-variables.md)을 참고하십시오.

## 트러블슈팅

### 서명 엔드포인트에서 `404` — 스캔이 서명되지 않음

서명 엔드포인트는 프로젝트의 가장 최근 성공 스캔에 해당 종류의 산출물이 없을 때 `404`를 반환합니다. 흔한 원인:

- **서명이 설정되지 않음** — 배포가 cosign 키 없이(`COSIGN_KEY_PATH` 미설정), `COSIGN_KEYLESS=false`로 실행됨. 키를 설정하고([5절](#5-운영자-키-설정)) 스캔을 다시 실행하십시오.
- **워커에 cosign 바이너리 없음** — 워커 이미지만 cosign을 포함합니다. 커스텀 워커가 제거했을 수 있습니다. 워커 로그에서 `cosign_not_found` / 서명 경고를 확인하십시오.
- **아직 성공 스캔 없음** — 스캔을 실행하고 `Completed`에 도달할 때까지 기다리십시오.

`404` 본문은 RFC 7807 `application/problem+json` 봉투이며 `detail`이 조치 가능한 이유를 명시합니다.

### `/public-key`는 `404`인데 `/certificate`는 동작(또는 그 반대)

이는 정상이며 배포가 사용하는 모델을 알려 줍니다.

- **key-based** → `/sbom/public-key`가 `cosign.pub`을 반환하고 `/sbom/certificate`는 `404`. `--key cosign.pub`으로 검증.
- **keyless** → `/sbom/certificate`가 Fulcio 인증서를 반환하고 `/sbom/public-key`는 `404`. `--certificate …`로 검증.

번들은 올바른 파일을 자동으로 선택하므로 번들을 선호할 또 하나의 이유입니다.

### `cosign verify-blob`이 "invalid signature"로 실패

SBOM 바이트가 서명과 맞지 않습니다. 둘 다 **같은** 번들에서 다시 내려받고(새로 내보낸 SBOM과 오래된 서명을 섞지 마십시오), SBOM을 재포맷·재저장하지 않았는지 확인하십시오(내보내기는 byte-stable이라 어떤 편집도 서명을 깨뜨립니다). keyless에서는 `--certificate-identity` / `--certificate-oidc-issuer`가 운영자가 공개한 값과 일치하는지도 확인하십시오.

### 다운로드에서 `413`

산출물 또는 번들이 배포에 설정된 다운로드 크기 한도를 초과했습니다. 이는 서버 측 가드이며 클라이언트가 고칠 수 있는 오류가 아닙니다 — 운영자에게 문의하십시오.

## 참고

- [SBOM](../user-guide/sbom.md) — 서명 대상 SBOM 내보내기
- [용어집](./glossary.md) — SBOM·SCA·VEX·RBAC 역할 정의
- [환경 변수](./env-variables.md) — `COSIGN_*` 및 `SLSA_*` 키
- [API 레퍼런스 (Redoc)](pathname:///reference/api) — 생성된 엔드포인트 계약
- [이슈 보고](https://github.com/trustedoss/trustedoss-portal/issues/new/choose) — 검증이 예기치 않게 실패할 때
