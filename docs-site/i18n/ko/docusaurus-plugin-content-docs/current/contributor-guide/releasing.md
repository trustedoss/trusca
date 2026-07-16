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
   자동 생성한 노트를 씁니다.
4. **`release-gate`** — 방금 발행한 `X.Y.Z` 이미지를 pull해서 **프로덕션**
   `docker-compose.yml`을 기동합니다. 이때 작은 오버레이
   [`docker-compose.smoke.yml`](https://github.com/trustedoss/trusca/blob/main/docker-compose.smoke.yml)이
   backend와 frontend 포트를 노출해 Traefik/DNS/TLS 없이도 스모크를 돌릴 수
   있게 합니다. 그다음 문서화된 Quickstart first-scan 스모크를 실행합니다.
   헬스 폴링 → `create_super_admin` → 로그인 → projects API 순서입니다. 성공하면
   `gh release edit <tag> --draft=false --latest`로 Release를 공개합니다.

```
build ──▶ merge ──▶ release (draft) ──▶ release-gate ──▶ 공개 (draft=false)
다이제스트   버전       GitHub Release       발행 이미지 pull    스모크 통과 시에만
push       태그        아직 숨김            + first-scan 스모크  공개 + latest
```

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

1. 벤더링된 endoflife.date 스냅숏을 갱신해 릴리스가 최신 수명 주기 데이터를
   담게 합니다(EOL 판정은 이 파일에서 오프라인으로 스탬프됩니다).
   `apps/backend`에서 `python3 scripts/refresh_eol_snapshot.py`를 실행하고,
   갱신된 스냅숏을 릴리스 준비 변경과 함께 커밋합니다.
2. `docs-site/docs/release-notes/X.Y.Z.md`에 릴리스 노트를 두고, `.env.example`의
   `IMAGE_TAG`를 `X.Y.Z`로 올립니다.
3. 태그를 push합니다. `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. `release-gate` 잡을 지켜봅니다. 초록색이 되면 Release가 자동으로 공개되고
   `latest`로 표시됩니다. 별도 수동 작업은 필요 없습니다.

## 함께 보기

- [시작하기](./getting-started.md) — dev 스택, 첫 PR.
- [Docker Compose 설치](../installation/docker-compose.md) — 게이트가 실증하는
  운영자 설치 경로.
- [Quickstart](../quickstart.md) — 게이트 스모크가 본뜬 first-scan 시나리오.
