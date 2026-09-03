---
id: releasing
title: 릴리스
description: TRUSCA 릴리스 방식 — 이미지를 먼저 발행하고, 발행된 이미지가 정상 기동하는지 스모크 테스트로 확인할 때까지 GitHub Release는 draft로 두었다가 공개합니다.
sidebar_label: 릴리스
sidebar_position: 5
---

# 릴리스

TRUSCA 릴리스는 `vX.Y.Z` 형식의 git 태그를 push하면 시작됩니다. 나머지는
[`.github/workflows/release.yml`](https://github.com/trustedoss/trusca/blob/main/.github/workflows/release.yml)
워크플로가 처리합니다. 목표는 하나입니다. 사용자가 실제로 pull하게 될 바로 그
이미지로 설치가 되는지 확인하기 전에는 어떤 릴리스도 공개하지 않습니다.

## 게이트 개요

워크플로는 네 단계를 순서대로 실행하며, 각 단계는 앞 단계에 의존합니다.

1. **`build`** — 각 이미지(`trusca-backend`, `trusca-backend-worker`,
   `trusca-frontend`)를 amd64와 arm64 네이티브 러너에서 빌드하고 GitHub
   Container Registry에 다이제스트 단위로 push합니다.
2. **`merge`** — 이미지마다 멀티아치 매니페스트 리스트를 만들고 버전 태그를
   붙입니다(`X.Y.Z`는 불변, `X.Y`는 이동 가능 — `:latest`는 절대 쓰지 않습니다).
3. **`release`** — GitHub Release를 **draft**로 생성합니다. 릴리스 노트는
   `docs-site/docs/release-notes/X.Y.Z.md`가 있으면 그것을, 없으면 GitHub가
   자동 생성한 노트를 씁니다. 이 잡은 릴리스 자신의 소스 트리에 대한
   CycloneDX SBOM도 생성해(syft) Release 자산으로
   첨부합니다(`trusca-X.Y.Z.cdx.json`) — SCA 제품은 자기 SBOM을 함께
   내놓습니다.
4. **`release-gate`** — 방금 발행한 `X.Y.Z` 이미지를 pull해서 **프로덕션**
   `docker-compose.yml`을 기동합니다. 이때 작은 오버레이
   [`docker-compose.smoke.yml`](https://github.com/trustedoss/trusca/blob/main/docker-compose.smoke.yml)이
   backend와 frontend 포트를 노출해 Traefik/DNS/TLS 없이도 스모크를 돌릴 수
   있게 합니다. 그다음 문서화된 Quickstart first-scan 스모크를 실행합니다.
   헬스 폴링 → `create_super_admin` → 로그인 → projects API 순서입니다. 성공하면
   `gh release edit <tag> --draft=false --latest`로 Release를 공개하고, 이어서
   [`deploy-hetzner.yml`](https://github.com/trustedoss/trusca/blob/main/.github/workflows/deploy-hetzner.yml)에
   그 태그로 공개 데모를 올려 달라고 요청합니다. 이 배포는 `demo` 환경의 승인자를
   기다리므로, 릴리스가 아무도 모르는 사이에 데모 호스트까지 나가지는 않습니다.

```
build ──▶ merge ──▶ release (draft) ──▶ release-gate ──▶ 공개 (draft=false)
다이제스트   버전       GitHub Release       발행 이미지 pull    스모크 통과 시에만
push       태그        아직 숨김            + first-scan 스모크  공개 + latest
```

:::note 레지스트리에 남아 있는 `latest` 태그
0.21.0까지의 릴리스는 `latest` 이미지 태그도 함께 올렸습니다.
`docker/metadata-action`이 별도로 끄지 않으면 태그를 하나 더
붙이는데(`flavor: latest=auto`) 워크플로가 그것을 끄지 않았습니다. 지금은
꺼져 있어 새로 생기지는 않습니다. 이미 GHCR에 올라간 태그는 그냥 지울 수
없습니다. GHCR은 태그가 아니라 패키지 버전 단위로 삭제하는데, `latest`가
`0.21.0`, `0.21`과 같은 버전에 붙어 있기 때문입니다. 다음 릴리스 전에
`latest`로 버릴 매니페스트를 하나 올린 뒤 그 버전만 삭제해서 태그를 떼십시오.
그냥 두면 `latest`는 최신인 척하면서 계속 0.21.0을 가리킵니다.
:::

## 이미지를 먼저 발행하고 Release는 나중에 공개하는 이유

컨테이너 이미지는 Release가 존재하기 **전에** `build`와 `merge`에서 발행됩니다.
이는 의도된 설계입니다. 게이트는 운영자가 하는 방식 그대로 실제 발행 이미지를
pull해서 실행해봐야만 설치 가능 여부를 증명할 수 있기 때문입니다. Release는
사람에게 알리는 공지이므로, 그 증명이 끝날 때까지 draft로 붙잡아 둡니다.

## 실패 시 동작

`release-gate`의 어느 단계든 실패하면 공개 단계는 건너뜁니다. 이 단계에는
`if: always()` 가드가 없어 성공 경로에서만 실행되기 때문입니다. 결과는 이렇습니다.

- **이미지 태그는 발행된 채로 남아 pull할 수 있습니다.** `X.Y.Z`와 `X.Y`는
  `merge` 단계에서 push되었고 되돌리지 않습니다. 운영자는 그대로 pull할 수 있고,
  워크플로를 다시 돌리면 같은 이미지를 재사용합니다.
- **GitHub Release는 draft로 남습니다.** Releases 페이지에 보이지 않고, `latest`로
  표시되지 않으며, 워처에게 알림이 가지 않습니다. 이미지가 기동에 실패한 릴리스는
  아무것도 공지하지 않습니다.

복구하려면 원인을 고친 뒤 같은 태그로 워크플로를 다시 실행합니다(또는 `tag`
입력으로 수동 실행). `release` 잡은 멱등적입니다. 기존 draft는 그대로 두고,
`release-gate`가 같은 발행 이미지를 다시 pull해 스모크를 다시 돌립니다. 스모크가
통과할 때만 draft가 공개로 바뀝니다.

:::note 수동 공개
게이트가 릴리스와 무관한 이유(예: 인프라 문제)로 실패하는데 릴리스 자체는 따로
검증했다면, 메인테이너가 `gh release edit vX.Y.Z --draft=false --latest`로 직접
공개할 수 있습니다. 되도록 게이트를 고치는 편이 낫습니다.
:::

## 릴리스 절차

1. 저장소에 포함된 endoflife.date 스냅샷을 갱신해 릴리스가 최신 수명 주기 데이터를
   담게 합니다(EOL 판정은 이 파일에서 오프라인으로 스탬프됩니다).
   `apps/backend`에서 `python3 scripts/refresh_eol_snapshot.py`를 실행하고,
   갱신된 스냅샷을 릴리스 준비 변경과 함께 커밋합니다.
2. **문서 일괄 점검** — 릴리스는 문서와 함께 나갑니다. 태그 전에:
   - `docs-site/docs/release-notes/X.Y.Z.md`에 릴리스 노트를 작성합니다
     (EN + KO 미러, `sidebars.ts` 배선 포함). 내용은 `CHANGELOG.md`의
     `[Unreleased]` 섹션에서 가져오고, 가져온 항목은 새 `[X.Y.Z]` 제목
     아래로 옮깁니다.
   - `[Unreleased]` 항목을 한 번 더 훑어, **사용자 대면** 기능이 릴리스
     노트만이 아니라 해당 가이드 페이지(user-guide / admin-guide /
     ci-integration)에도 반영됐는지 확인합니다. 가이드 섹션이 없는 기능은
     릴리스 차단 사유입니다 — "문서 동행" 규칙을 가장 싸게 고칠 수 있는
     시점에 강제하는 장치입니다.
   - 새 UI 화면이 나갔다면 `make screenshots-capture`로 스크린샷을 캡처해
     가이드 섹션에서 참조합니다.
3. `.env.example`의 `IMAGE_TAG`를 `X.Y.Z`로 올립니다.
4. 차트도 함께 올립니다. `charts/trustedoss/Chart.yaml`의 `version`과 `appVersion`,
   `charts/trustedoss/values.yaml`의 `image.tag` 세 곳을 같은 커밋에서 바꿉니다.
   차트는 포털과 같은 주기로 릴리스한다고 적어 두었는데 한 번 아홉 마이너 버전이나
   벌어졌고, 그 동안 기본 `helm install`은 운영자가 `image.tag`를 직접 지정하지 않으면
   오래된 포털을 설치했습니다.
5. 태그를 push합니다. `git tag -s vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
   `-s`는 서명을 하고 `-a`를 함께 적용합니다. 옵션 없는 `git tag`는 커밋을 직접
   가리키는 ref만 만드는 경량 태그라 태그 객체가 없고, 그래서 서명을 담을 수
   없습니다. [릴리스 태그 서명](#릴리스-태그-서명)을 보세요.
6. `release-gate` 잡을 지켜봅니다. 초록색이 되면 Release가 자동으로 공개되고
   `latest`로 표시됩니다. 별도 수동 작업은 필요 없습니다.
7. 데모 배포를 승인합니다. Release가 공개되면 `demo` 환경에 대한
   `deploy-hetzner.yml` 실행이 하나 걸리고 승인자를 기다립니다. 데모를 이전
   릴리스에 그대로 두려면 승인하지 않으면 됩니다. 이렇게 걸린 실행은 시드를
   다시 만들지 않으므로, 릴리스가 `scripts/seed_demo.py`를 바꿨다면 워크플로를
   직접 `reseed: true`로 실행하십시오.

## 릴리스 태그 서명

릴리스 태그는 릴리스 담당자가 SSH로 서명합니다. `tag-signature` 잡이 모든
릴리스에서 이것을 검사하며 가장 먼저 돌기 때문에, 강제로 바뀐 뒤에는 서명이
없는 태그가 무엇이 발행되기 전에 릴리스를 멈춥니다.

:::note 아직 강제하지 않습니다
`release.yml`의 `REQUIRE_SIGNED_TAGS`가 `"false"`라서 지금은 서명이 없어도
알리기만 하고 릴리스를 계속 진행합니다. 의도한 것입니다. 서명 키가 등록되기
전에는 모든 태그가 서명되어 있지 않고, 그 상태에서 실패로 막으면 릴리스를
개선하는 것이 아니라 못 하게 만듭니다. 아래 설정을 마치고
`.github/allowed_signers`에 키가 올라간 뒤 `"true"`로 바꾸면 됩니다. 그
전환이 남은 유일한 단계입니다.
:::

### 릴리스 담당자 최초 설정

1. 서버 접속에 쓰는 키와 분리된 서명 전용 키를 만듭니다.

   ```bash
   ssh-keygen -t ed25519 -C "release@trusca" -f ~/.ssh/trusca_release
   ```

2. git이 그 키로 태그에 서명하도록 설정합니다.

   ```bash
   git config --global gpg.format ssh
   git config --global user.signingkey ~/.ssh/trusca_release.pub
   ```

3. GitHub의 Settings에서 SSH and GPG keys로 가서 공개키를 두 번 등록합니다.
   git 인증에 쓰려면 Authentication key로 한 번, 그리고 GitHub이 태그를
   `Verified`로 표시하게 하려면 Signing key로 한 번입니다. 같은 키를 별개
   항목으로 넣는 것이라 앞의 것만 등록하고 끝내기 쉽습니다.

4. `.github/allowed_signers`에 추가하고 그 파일로 PR을 엽니다. 이 파일이
   검증하는 쪽에 어느 키가 유효한지 알려 줍니다.

   ```
   release@trusca ssh-ed25519 AAAA... release@trusca
   ```

5. `release.yml`의 `REQUIRE_SIGNED_TAGS`를 `"true"`로 바꿉니다.

개인키는 릴리스 담당자의 장비를 벗어나지 않습니다. CI는 서명하지 않으므로
시크릿이 필요 없고 검증만 합니다.

### allowed-signers 파일이 필요한 이유

암호학적으로 검증되는 서명은 누군가가 서명했다는 사실만 말합니다.
`git verify-tag`는 유효한 키라면 공격자가 방금 만든 키에도
`Good "git" signature`를 출력합니다. 그것에 의미를 주는 것이
`.github/allowed_signers`입니다. 해당 항목이 없으면 git이
`No principal matched`를 덧붙이고 검사는 실패합니다. 이 파일 없이 하는 검증은
형식만 갖춘 것입니다.

## 함께 보기

- [시작하기](./getting-started.md) — dev 스택, 첫 PR.
- [Docker Compose 설치](../installation/docker-compose.md) — 게이트가 실증하는
  운영자 설치 경로.
- [Quickstart](../quickstart.md) — 게이트 스모크가 본뜬 first-scan 시나리오.
