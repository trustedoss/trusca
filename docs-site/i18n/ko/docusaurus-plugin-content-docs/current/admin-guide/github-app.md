---
id: github-app
title: GitHub App 연결
description: 팀별로 GitHub App을 등록하고 개인 키를 암호화 저장하며, 프로젝트를 옵트인해 세분화된 설치별 접근을 부여합니다.
sidebar_label: GitHub App 연결
sidebar_position: 7
---

# GitHub App 연결

**GitHub App**은 TrustedOSS가 세분화된 저장소별 접근에 사용하는 자격증명입니다. 예를 들어 곧 추가될 자동 리메디에이션(의존성 버전 상향 PR 자동 생성) 흐름에서 사용됩니다. PAT(Personal Access Token)와 달리 GitHub App은 다음과 같습니다.

- **설치 가능** — 조직/저장소 단위로 설치하고 세분화된 권한(`contents` + `pull_requests: write`)을 부여합니다.
- **단기 토큰** — TrustedOSS는 작업마다 새 설치 액세스 토큰을 발급하며, 장기 비밀은 서버 밖으로 나가지 않습니다.
- **멀티테넌트** — 각 팀이 자신의 App을 독립적으로 등록·관리합니다.

:::note 대상
`team_admin`은 자기 팀의 자격증명을 등록·폐기하고 설치를 연결합니다. `super_admin`은 모든 팀을 관리할 수 있습니다. `developer`는 자기 팀 자격증명을 조회만 할 수 있고 변경은 불가합니다.
:::

## 저장되는 항목

등록된 자격증명은 하나의 팀에 속한 행이며 다음을 보관합니다.

| 필드 | 저장 형태 | 비고 |
|------|-----------|------|
| `app_id` | 평문 | GitHub App 숫자 id (App JWT의 `iss`). |
| `app_slug` | 평문(선택) | 사람이 읽는 App slug. |
| **개인 키(PEM)** | **Fernet 암호문** | App 개인 키. 등록 시 1회만 입력받으며 어떤 엔드포인트도 **반환하지 않습니다.** |
| **Webhook 시크릿** | **Fernet 암호문**(선택) | App Webhook HMAC 시크릿. |

평문 개인 키는 **등록 요청 본문에서만** 받으며 PostgreSQL에 기록되기 **전에** 암호화됩니다. 어떤 조회 엔드포인트도 키나 암호문을 반환하지 않으며, 응답에는 메타데이터와 `has_private_key` / `has_webhook_secret` 불리언만 담깁니다.

## 저장 시 암호화

개인 키와 Webhook 시크릿은 저장 전에 **Fernet**(AES-128-CBC + HMAC-SHA256)으로 암호화되고, 단일 토큰 발급 작업 동안에만 메모리에서 복호화됩니다.

암호화 키는 런타임에 결정됩니다.

1. **`GITHUB_APP_ENCRYPTION_KEY`** — URL-safe base64로 인코딩된 32바이트 Fernet 키. **프로덕션에서는 반드시 설정하세요.** 다음으로 생성합니다.

   ```python
   from cryptography.fernet import Fernet
   print(Fernet.generate_key().decode())
   ```

2. 미설정 시 `SECRET_KEY`에서 결정론적으로 키를 **파생**해 로컬/개발 환경이 추가 설정 없이 동작합니다. 파생 키 사용 시 구조화된 `WARNING` 로그가 남습니다.

:::warning 신중한 회전
파생 키는 `SECRET_KEY`와 운명을 공유합니다. 전용 `GITHUB_APP_ENCRYPTION_KEY` 없이 운영하면 **`SECRET_KEY`를 회전할 때 저장된 모든 GitHub App 자격증명을 복호화할 수 없게 됩니다** — App을 모두 재등록해야 합니다. 프로덕션에서는 독립적으로 회전 가능한 전용 `GITHUB_APP_ENCRYPTION_KEY`를 설정하세요.
:::

복호화할 수 없는 자격증명(키가 회전된 경우)은 사용 시 깔끔한 오류로 표면화되며, 키가 절대 누출되지 않습니다.

## 감사 로그

자격증명 등록·폐기·재연결은 `audit_logs` 행을 남깁니다. `private_key_encrypted`와 `webhook_secret_encrypted` 컬럼은 감사 diff에서 **`***`로 마스킹**되어 자격증명 자료가 감사 추적에 남지 않습니다.

폐기는 API Key와 동일하게 **소프트 삭제**(`revoked_at` 설정)입니다. 자격증명은 즉시 사용 불가가 되지만 행은 포렌식 조회를 위해 보존됩니다.

## 설치 옵트인

자격증명만으로는 TrustedOSS가 프로젝트 저장소를 다룰 권한이 생기지 않습니다. 팀이 명시적으로 **설치(계정/저장소)를 TrustedOSS 프로젝트에 연결**해야 합니다. 옵트인 대상 프로젝트는 자격증명과 **같은 팀** 소유여야 하며, 팀 간 연결은 거부됩니다.

## 엔드포인트

모든 엔드포인트는 JWT 인증을 요구하며 오류 시 RFC 7807 `application/problem+json`을 반환합니다. 접두사: `/v1/github-app-credentials`.

| 메서드 | 경로 | 역할 | 용도 |
|--------|------|------|------|
| `POST` | `/v1/github-app-credentials?team_id=…` | `team_admin` | 자격증명 등록(201). 개인 키 미반환. |
| `GET` | `/v1/github-app-credentials` | 멤버 | 호출자가 볼 수 있는 자격증명 목록. |
| `GET` | `/v1/github-app-credentials/{id}` | 멤버 | 단일 자격증명 메타데이터 조회. |
| `DELETE` | `/v1/github-app-credentials/{id}` | `team_admin` | 폐기(소프트 삭제). 멱등. |
| `POST` | `/v1/github-app-credentials/{id}/installations` | `team_admin` | 설치 연결/옵트인. 재연결 시 멱등. |
| `GET` | `/v1/github-app-credentials/{id}/installations` | 멤버 | 자격증명의 설치 목록. |
| `DELETE` | `/v1/github-app-credentials/{id}/installations/{installation_id}` | `team_admin` | 설치 연결 해제. 멱등. |

비멤버가 자격증명 id를 탐색하면 `403`이 아닌 `404`(존재 숨김)를 받아 id 열거를 막습니다.

## 관련 설정

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `GITHUB_APP_ENCRYPTION_KEY` | _(`SECRET_KEY`에서 파생)_ | 저장 시 자격증명 암호화용 Fernet 키. |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST 베이스. GitHub Enterprise Server는 재정의. |
