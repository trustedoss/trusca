---
id: auth-and-profile
title: 인증과 프로필
description: 이메일+비밀번호 또는 OAuth로 로그인하고, 분실한 비밀번호를 복구하며, /profile 페이지에서 연결된 신원을 관리합니다.
sidebar_label: 인증·프로필
sidebar_position: 7
---

# 인증과 프로필

<!-- docs-uat: id=auth-login kind=ui harness=login(dev@demo.trustedoss.dev,DemoTest2026!) tier=nightly -->
TRUSCA는 두 가지 대화형 로그인 방식 — **이메일 + 비밀번호**와 **OAuth**(GitHub 또는 Google) — 그리고 셀프 서비스 비밀번호 복구 흐름을 제공합니다. 이 페이지는 각 경로를 따라가고 `/profile` 페이지에서의 신원 관리를 다룹니다.

:::note 대상 독자
로그인된 모든 사용자. 본인의 신원을 관리하는 데 특별한 역할은 필요 없습니다. OAuth 버튼은 운영자가 해당 `*_CLIENT_ID` / `*_CLIENT_SECRET` 환경 변수를 구성한 경우에만 표시됩니다.
:::

## 이메일 + 비밀번호로 로그인

1. `/login`을 엽니다.
2. 이메일과 비밀번호를 입력합니다.
3. 제출합니다.

![이메일·비밀번호 입력과 OAuth 공급자 버튼이 표시된 로그인 페이지 — KO locale](/img/screenshots/user-auth-login-ko.png)

**서버 측 동작**

- 비밀번호는 가입 시점에 **bcrypt cost 12**로 해시되며, 로그인 시 후보 값을 저장된 해시와 상수 시간 비교합니다.
- 성공 시 JWT **access token(30분)**과 refresh token(**7일**, 매 사용 시 회전, 재사용 탐지로 전체 체인을 무효화)을 반환합니다.
- Refresh token은 `HttpOnly`, `Secure`, `SameSite=Lax` 쿠키에 보관됩니다. JavaScript에서는 절대 접근할 수 없습니다.
- 로그인 엔드포인트는 **IP당 1분에 5회**로 레이트 리밋이 걸려 있습니다. 초과 요청은 HTTP 429와 `Retry-After` 헤더로 응답합니다.

*"Invalid email or password"* 메시지가 보이면 이메일이 정확한지 확인 후 한 번 더 시도하세요. 메시지는 의도적으로 일반화되어 있어 공격자가 계정을 열거할 수 없습니다.

## 비밀번호를 잊어버린 경우

1. `/login`에서 **Forgot password?**를 클릭하면 `/forgot-password`로 이동합니다.
2. 계정과 연결된 이메일을 입력합니다.
3. 제출합니다. 포털은 해당 이메일에 계정이 없더라도 항상 204 No Content를 반환합니다 — 사용자 열거를 막기 위한 설계입니다.
4. 메일함을 확인합니다. 계정이 존재한다면 **"Reset your TRUSCA password"** 제목의 메시지가 약 30초 이내에 도착합니다.

리셋 링크는 **24시간 동안 유효하며 1회만 사용 가능**합니다. 만료되거나 사용된 후에는 토큰이 폐기됩니다.

![이메일 입력 + 사용자 열거 방지 제출이 적용된 비밀번호 찾기 페이지 — KO locale](/img/screenshots/user-auth-forgot-ko.png)

## 비밀번호 재설정

이메일의 링크는 `/reset-password?token=<opaque>`로 연결됩니다.

1. 새 비밀번호를 입력합니다(12자 이상, 침해된 사전 단어 금지).
2. 두 번째 필드에서 한 번 더 확인 입력합니다.
3. 제출합니다.

성공 시 `/login`으로 리다이렉트됩니다. 새 비밀번호는 bcrypt 해시되고 리셋 토큰은 소비됩니다. 이전 비밀번호로 연 세션은 더 이상 동작하지 않습니다. refresh token이 폐기되고, 변경 이전에 발급된 access token도 남은 유효 기간을 채우지 못하고 거부됩니다. 자격 증명이 유출됐다고 판단해 비밀번호를 바꾼 사람이 훔쳐간 세션이 만료되기를 기다릴 필요가 없습니다.

API 키는 영향을 받지 않습니다. 수명 주기가 따로 있는 별도 자격 증명이므로 API 키로 인증하는 빌드는 계속 동작합니다. 침해가 의심되어 비밀번호를 바꾸는 경우라면 Integrations 화면에서 키도 함께 점검하세요.

토큰이 만료되었거나 이미 사용된 경우, 페이지는 오류와 함께 `/forgot-password`로 돌아가는 링크를 표시합니다.

## OAuth로 로그인

GitHub 또는 Google이 구성되어 있다면 `/login` 페이지의 이메일 필드 아래에 해당 버튼이 표시됩니다.

1. **Continue with GitHub** 또는 **Continue with Google**을 클릭합니다.
2. 공급자 동의 화면에서 접근 요청을 승인합니다.
3. 포털로 리다이렉트되어 로그인됩니다.

**최초 OAuth 로그인** 시 공급자가 검증한 이메일로 계정이 자동 생성됩니다. 개인 팀이 자동으로 프로비저닝됩니다(`<your-handle>'s team` 형식).

**이후 로그인**은 `(provider, provider_user_id)`로 기존 신원을 조회합니다. 공급자의 `email` 필드는 매칭에 **사용되지 않습니다** — 공급자 측에서 재활용된 이메일 주소를 통한 계정 탈취를 방지합니다.

오류는 i18n으로 매핑된 메시지로 표면화됩니다. 일곱 가지 코드는 공급자 거부, 누락된 scope, 만료된 state, 반복된 state, 신원 충돌, 정지된 계정, 공급자 5xx를 다룹니다. 각 코드는 사용자에게 구체적인 복구 동작을 안내합니다.

## `/profile`에서 연결된 계정 관리

`/profile` 페이지의 **Connected Accounts** 섹션은 현재 본인 계정에 연결된 OAuth 신원을 나열합니다.

- **GitHub** — GitHub로 한 번이라도 로그인한 적이 있으면 표시.
- **Google** — Google로 한 번이라도 로그인한 적이 있으면 표시.

![프로필 페이지 — 헤더, 신원 카드, Connected Accounts 패널](/img/screenshots/user-profile-mounted.png)

Connected Accounts 패널은 본인 포털 계정에 현재 연결된 모든 외부 신원을 한눈에 보여줍니다:

![Connected Accounts — 연결된 GitHub 신원이 표시된 패널](/img/screenshots/user-profile-connected-accounts.png)

현재 릴리스에서는 비밀번호 로그인이 Connected Accounts 목록에 별도 행으로 표시되지 않습니다 — 이메일+비밀번호로 가입했거나 비밀번호 재설정을 완료했다면 묵시적으로 활성 상태입니다. OAuth만 사용 중인 계정의 비밀번호 복구는 로드맵 항목입니다. 현재는 운영자 측 `/admin/users/{id}/password-reset` 엔드포인트가 유일한 경로입니다.

각 Connected Accounts 행에는 **Unlink** 버튼이 있습니다. 포털은 본인이 로그인 수단을 모두 잃는 상황을 방지합니다(마지막 인증 수단 보호).

- 연결 해제로 인해 **로그인 수단이 하나도 남지 않게 되면**(예: OAuth 신원 하나만 있고 비밀번호도 설정되지 않은 경우) 요청은 HTTP 409로 거부되며 UI에 알림이 표시됩니다 — *"Set a password before unlinking your last OAuth identity."*
- 대안 경로는 **Forgot password**입니다. 리셋 링크를 요청해 비밀번호를 설정한 다음 `/profile`로 돌아와 연결을 해제하세요.

새 공급자 연결은 대칭적입니다. 로그아웃 후 새 공급자로 로그인하면 검증된 이메일이 기존 계정과 일치하므로 새 신원이 자동으로 추가됩니다.

## 정상 동작 확인

<!-- docs-uat: id=auth-header-profile-visible kind=ui harness=headerProfileVisible tier=nightly -->
- 비밀번호 로그인 후 글로벌 바에 이니셜과 지금 작업 중인 팀이 표시됩니다.

### 팀 전환

두 개 이상의 팀에 속해 있으면 글로벌 바의 팀 이름이 메뉴가 됩니다. 다른 팀을 고르면 새 프로젝트가 생성될 팀이 바뀌고, 그 선택은 새로 고침 후에도 유지됩니다.

다만 어떤 화면도 걸러 내지 **않습니다**. 대시보드와 프로젝트 목록은 어떤 팀을 선택하든 소속으로 닿는 범위 전부를 보여 줍니다 — 조용히 페이지를 좁히는 컨트롤은 실수로 켜 둔 채 알아차리기 어렵기 때문입니다. 화면을 좁힐 때는 그 화면의 필터를 씁니다.

소속 팀이 하나면 이름이 평범한 라벨로만 표시되고, 소속이 없으면(예: 시드된 최고 관리자) 아무것도 표시되지 않습니다.
<!-- docs-uat: id=auth-oauth-provider-listed kind=manual tier=manual -->
- OAuth 로그인 후 `/profile`에 사용한 공급자가 나열됩니다.
<!-- docs-uat: id=auth-unlink-disables-last kind=ui harness=profileUnlinkGithub tier=nightly -->
- 연결 해제 후 해당 행이 사라지며, 남은 행의 **Unlink** 버튼은 본인을 잠그게 될 경우 비활성화됩니다.

## `/about`에서 보는 라이선스와 고지

사이드바의 **제품 정보**는 이 설치본이 무엇이고 어떤 라이선스 고지를 함께
배포하는지 보여줍니다. 관리자 권한은 필요하지 않습니다. 라이선스 고지는 설치본을
쓰는 모든 사용자가 볼 수 있어야 하는 내용입니다.

이 화면은 제품 이름, 실행 중인 버전, SPDX 라이선스 식별자, 저작권자, 공개된 소스
코드 주소를 보여줍니다. 그 아래 세 개의 탭이 고지 파일 원문을 그대로 담습니다.

| 탭 | 파일 | 내용 |
|---|---|---|
| 라이선스 | `LICENSE` | TRUSCA 자체에 적용되는 라이선스(Apache-2.0). |
| 고지 | `NOTICE` | Apache-2.0 라이선스 4(d)항이 요구하는 TRUSCA의 저작권 고지. |
| 서드파티 고지 | `THIRD_PARTY_NOTICES.md` | 소스 트리에 포함한 서드파티 자료와 컨테이너 이미지에 번들한 도구의 귀속 정보. |

같은 파일 세 개가 모든 컨테이너 이미지의 `/licenses/` 경로와 Helm 차트에도 들어
있습니다. 셸에 접근할 수 있는 운영자는 UI 없이도 읽을 수 있습니다. 제품 안에도
둔 이유는 TRUSCA를 직접 설치해 운영하고 외부 망이 끊긴 환경도 지원하기 때문입니다.
그런 환경에서는 GitHub 링크를 따라가는 방법이 통하지 않습니다.

어떤 탭이 파일이 없다고 표시하면 이미지를 빌드할 때 파일이 빠진 것입니다. 포털이
아니라 릴리스 산출물을 확인하세요.

## 함께 보기

- [알림](./notifications.md) — 프로젝트 이벤트가 어떻게 본인에게 도달하는지.
- [통합](./integrations.md) — 비대화형 클라이언트를 위한 API Key.
- [사용자 및 팀](../admin-guide/users-and-teams.md) — 동일 신원의 admin 관점.
- [컴포넌트와 라이선스](./components-and-licenses.md) — TRUSCA 자체가 아니라 스캔 대상 코드의 라이선스.
