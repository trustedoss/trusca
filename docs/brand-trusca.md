# TRUSCA 브랜드 가이드 (SoT)

> 2026-06-12 제정. 제품명 리브랜딩(TrustedOSS Portal → TRUSCA)의 단일 진실.
> 적용 작업 추적은 `~/.claude/plans/cozy-humming-flask.md`(W0~W6), 결정 경위는
> 아래 §6.

## 1. 이름

| 항목 | 값 |
|------|-----|
| 제품명 (브랜드 표기) | **TRUSCA** — 항상 대문자. TRU+SCA 합성이 보이게 한다 |
| 식별자 표기 | `trusca` — 소문자. 도메인·패키지·이미지·레포·경로 |
| 유래 | **Trust + SCA** (Software Composition Analysis) |
| 한글 표기 | 트러스카 |
| 우산 관계 | TRUSCA는 TrustedOSS 이니셔티브(정책·프로세스·가이드)의 SCA 도구 |

**관계 문구 (고정)**
- EN: *TRUSCA — the SCA tool of the TrustedOSS initiative*
- KO: *TrustedOSS의 SCA 도구, TRUSCA*

**표기 규칙**
- 문장 안에서도 TRUSCA(대문자). "Trusca"·"trusca"를 산문에 쓰지 않는다(식별자 제외).
- 우산을 가리킬 때는 "TrustedOSS"(이니셔티브). 제품을 가리킬 때만 TRUSCA.
- 문서 일괄 치환 시 "TrustedOSS Portal"(제품 지칭)만 TRUSCA로 바꾸고,
  "TrustedOSS" 단독 언급(우산 지칭)은 보존한다.
- OWASP 등록 승인 후에는 공식 표기를 "OWASP TRUSCA"로 전환한다(별도 PR).

## 2. 태그라인

| 언어 | 태그라인 |
|------|----------|
| EN | Open-source SCA platform — CVEs, licenses, and SBOMs in one place |
| KO | 오픈소스 SCA 플랫폼 — CVE·라이선스·SBOM을 한곳에서 |

## 3. 도메인·네임스페이스 (확보 상태)

| 자산 | 값 | 상태 |
|------|-----|------|
| 도메인 | `trusca.dev` | W0에서 등록(Cloudflare). 데모 `demo.trusca.dev`, 문서 `docs.trusca.dev` |
| GitHub | `github.com/trustedoss/trusca` | **W4 전환 완료** — 레포 리네임(git/웹 URL은 GitHub 자동 리다이렉트, Pages 경로는 `/trusca/`로 변경·리다이렉트 없음) |
| ghcr | `ghcr.io/trustedoss/trusca-{backend,backend-worker,frontend}` | **W4 전환 완료** — v0.11.0부터 trusca-* 이름으로 게시. 구 릴리스(≤0.10.0)는 구 이름(backend/backend-worker/frontend) 유지 |
| npm / PyPI | `trusca` | 빈자리 확인(미사용, 선점 옵션) |

## 4. 로고

- **모티프**: 새 모티프(기존 shield+check 비계승). 시안 3종이
  `/dev/design-preview` "Brand" 섹션에 있음(`apps/frontend/src/pages/dev/BrandCandidates.tsx`):
  - **A Hex Check** — 패키지 육각 + 검증 체크
  - **B Scan Line** — T를 가로지르는 스캔 빔
  - **C Stacked SBOM** — 구성요소 목록 막대 + 최상단 검증
- **선정안**: **A Hex Check** (2026-06-12 확정) — 패키지 육각 + 검증 체크.
  16px 가독성이 가장 좋고 보안 도구 관례에 부합.
- **팔레트**: 기존 토큰만 — ink `#18181b`(warm near-black), paper `#fafafa`,
  accent `#2563eb`(README 배지에 이미 쓰는 블루). 새 브랜드 컬러를 만들지 않는다.
- **워드마크**: Inter semibold tracking-tight, `TRU`(ink) + `SCA`(accent).
- **적용 자산**(선정 후 교체): `docs-site/static/img/{logo,favicon}.svg`,
  `apps/frontend/public/favicon.svg`(+ `index.html` link), AppShell 접힘 레일
  모노그램.

## 5. 보존 식별자 (의도적 비변경)

아래는 우산명(trustedoss)과 의미가 일치하는 **내부 식별자**로, 리브랜딩에서
변경하지 않는다(변경 가치 0·위험 高):

- PostgreSQL user/role: `trustedoss`, `trustedoss_app`, `trustedoss_owner`
- Celery 앱명·task prefix: `trustedoss`, `trustedoss.*`
- docker compose network: `trustedoss`
- 데모 계정 이메일: `*@demo.trustedoss.dev` (벤더링 verify-specs 무수정 원칙 보호)
- RFC 7807 problem type URN: `urn:trustedoss:problem:*`

## 6. 결정 경위 (요약)

작명 기준 4개를 차례로 수렴: ① TrustedOSS 접두어 없는 단독 호명 ② OWASP 공식
등록 가능(기존 프로젝트·보안 도구와 무충돌) ③ 한글 표기·발음 쉬움 ④ SCA를
이름에 포함. 후보군(Warden·Tessera·Cairn·Lantern·OSCAN 등) 중 OSCAN은 GitHub
동명 보안 스캐너 다수로 탈락, TRUSCA는 동명 제품·도메인·패키지 전부 빈자리로
확정(2026-06-12). SKT의 TOSCA가 "이름에 SCA 포함" 발상의 참조.
