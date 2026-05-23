# TrustedOSS Portal — Post-GA 실행 계획서 (v2.0.1 ~ v2.3)

> 작성일: 2026-05-23 | 기준 버전: v2.0.0 (GA, 2026-05-09)
> 본 문서는 GA 이후 로드맵의 **단일 진실(single source of truth)**이다.
> GA까지의 실행계획은 [`v2-execution-plan.md`](./v2-execution-plan.md)에 보존된다(완료).
> 공개용 요약 로드맵은 레포 루트 [`ROADMAP.md`](../ROADMAP.md)이며, 본 문서가 그 상세판이다.

---

## 0. TL;DR — 한 페이지 요약

GA 직후 경쟁 오픈소스(OWASP Dependency-Track, Eclipse SW360, ScanCode, ScanOSS,
Syft/Grype, Trivy, OSV-Scanner, GUAC, Renovate)와 비교한 갭 분석(2026-05-23) 결과,
TrustedOSS는 **"여러 도구를 하나의 UI로 묶는 통합 포털"**로서는 유효하나 개별 기능
깊이·공개 자산 완성도에서 뒤처진다. 이를 4개 마일스톤으로 좁힌다.

| 마일스톤 | 테마 | 핵심 산출물 | 성격 |
|---|---|---|---|
| **v2.0.1** | 공개 준비 (Docs & Web Hardening) | README/랜딩 stale 정정, 거버넌스 문서, 스크린샷·데모 노출, 비교표 | 문서/웹, 코드 무변경(patch) |
| **v2.1** | Triage 신뢰성 (DT 종속 완화 + 노이즈 감소) | EPSS UI 1급화, VEX 소비(필터), 라이브 데모, API 레퍼런스 호스팅 | minor |
| **v2.2** | 리메디에이션 & 정책 | 자동 의존성 업그레이드 추천→PR, 동적 라이선스 정책 엔진(ORT Evaluator 대체) | minor |
| **v2.3** | 공급망 무결성 & 우선순위화 | SBOM 서명/in-toto/SLSA provenance, reachability 우선순위화 | minor |

**비범위(Non-goal):** 스니펫/full-text 출처 검출(ScanOSS급)은 본 로드맵에서 **명시적으로 제외**한다(§4).

우선순위 원칙: **(1)** 공개를 막는 결함 먼저 → **(2)** DT 직접 사용자 대비 동등성 회복 →
**(3)** 차별화 기능. 각 항목은 PR 단위로 분해하고, 핵심 보안/연동 코드는 Producer-Reviewer
패턴(`security-reviewer`)을 거친다.

---

## 1. 배경 — 경쟁 갭 요약

조사 출처: dependencytrack.org, eclipse.dev/sw360, scancode-toolkit.readthedocs.io,
scanoss.com, fossology.org, github.com/anchore(syft/grype), trivy.dev,
security.googleblog.com(OSV-Scanner V2), docs.guac.sh, docs.renovatebot.com,
oss-review-toolkit.github.io, openssf.org(OpenVEX), ntia.gov(minimum elements),
CISA 2025 SBOM 요건. 상세 인벤토리는 본 세션 핸드오프에 보존.

**기능 갭 (스니펫 제외):**
- **자동 리메디에이션 부재** — 탐지·게이트까지만, "고치는 액션" 없음 (Renovate/Dependabot/OSV-Scanner v2 대비).
- **취약점 데이터 DT 단일 의존** — DT의 2차 래퍼. EPSS·우선순위 신호가 DT 노출분에 한정.
- **Reachability 우선순위화 없음** — 전수 CVE 나열 → alert fatigue.
- **공급망 무결성 미지원** — SBOM을 생성만 하고 서명/검증/provenance 없음.
- **VEX 소비 부재** — 생성만, 스캔 결과 필터링(소비) 안 함.
- **라이선스 정책 정적** — ORT Evaluator 제거 후 동적 정책 편집 불가(고정 카탈로그만).

**문서/웹 갭:**
- README가 심각하게 stale (상태=Pre-alpha ↔ 웹=GA 2.0.0, ORT/Excel 등 사실 불일치).
- 랜딩 페이지 stale + 시각 자산(스크린샷/GIF/다이어그램/데모) 0개.
- 거버넌스 문서 부재(GOVERNANCE/MAINTAINERS/SUPPORT/CODEOWNERS).
- 라이브 데모 인스턴스 없음, API 레퍼런스 미호스팅, 비교표 없음.

**배포/설치 갭 (글로벌 대비, 2026-05-23 확인):**
- **컨테이너 이미지 미게시** — compose는 게시 이미지를 pull하나 레지스트리 publish 워크플로우 부재 → 외부 설치 불가(공개 차단).
- **저마찰 경로 부재** — DT는 compose 파일 하나만 `curl`. 우리는 전체 repo clone + `install.sh` 필요.
- **Compose V1 강제** — 글로벌 표준 V2(V1은 2023 EOL). 신규 사용자 마찰.
- **Helm chart 미완성** — 0.1.0 scaffold(자인: Ingress/TLS 없음, DB/Redis 외부 전제), ArtifactHub/OCI 미게시.
- **높은 최소 사양·시드 부재** — 4 vCPU/8 GB + DT 힙 4 GB, 설치 후 빈 화면. 문서 사양 근거가 제거된 ORT를 인용(stale).

---

## 2. 마일스톤 v2.0.1 — 공개 준비 (Docs, Web & Distribution Hardening)

> 목표: "오픈소스 공개 첫인상"과 "설치 자체"를 막는 결함 제거. 제품 기능 코드는 변경하지
> 않으나(patch), 배포 게시(CI/인프라)는 포함한다 — 이미지가 게시되지 않으면 공개해도 설치가 불가하기 때문.
> 선행조건 없음. 가장 먼저 수행. 담당: `devops-engineer`(배포) + `doc-writer`(문서) + `frontend-dev`(랜딩).
>
> **상태(2026-05-23): 구현 완료.** 아래 P0~P2 모두 코드/문서 반영 + `docs-site` 빌드(EN/KO)·
> `actionlint`·`shellcheck`·`docker-compose config` 통과. **남은 것은 운영 1회 작업뿐** —
> 첫 `v2.0.1` 태그 cut → `trustedoss` org의 Actions packages:write 권한 + ghcr 패키지 public 설정.
> 그 전까지 `install-uat.yml`의 image-pull job은 `continue-on-error`로 둔다.

### P0 — 컨테이너 이미지 게시 & 저마찰 설치 (공개 차단)
- **현재상태:** `docker-compose.yml`은 `trustedoss/backend:2.0.0` 등을 **pull**하고 `install.sh`도 `docker-compose pull`을 하지만, **레지스트리 게시 워크플로우가 없다**(`ci.yml`의 `build-push-action`은 `load: true` 스캔용, `push` 없음). → 외부 사용자가 `docker-compose up` 하면 image pull 실패 = **설치 불가**.
- **작업:** ① `release.yml` — 태그 push 시 backend/worker/frontend를 **멀티아키(amd64+arm64)로 ghcr.io 게시** ② docker-compose 이미지 네임스페이스를 `ghcr.io/trustedoss/...`로 정렬(`.env.example` `IMAGE_TAG` · Helm `appVersion` 동기) ③ **경량 설치 경로** — 레포 clone 없이 `docker-compose.yml` + `.env.example`만 `curl`로 받아 기동(DT의 단일-compose 설치 동등) ④ **Compose V2(`docker compose`) 호환** — 배포 산출물은 V1/V2 모두 동작(로컬 개발의 V1 제약과 분리) ⑤ `install-uat`를 게시 이미지 pull 경로로 검증.
- **DoD:** 빈 호스트에서 레포 clone 없이 `docker-compose up` 한 번으로 기동. amd64/arm64 모두 pull 성공.

### P1 — README 현행화 (`README.md`)
- **현재상태:** 상태=Pre-alpha, `License classification (ORT rules)`, `Excel / PDF reports`,
  Quick Start `after Phase 0 PR #2`, Contributing `land in Phase 0 PR #4`, `charts/ (Phase B)` — 전부 stale.
- **작업:** ① 상태 배지·문구를 GA 2.0.0으로 ② `ORT rules`→`scancode + 분류 카탈로그` ③ `Excel/PDF`→`취약점 PDF 보고서(구현) + Excel/컴플라이언스 PDF(로드맵)` ④ Quick Start를 실제 동작 명령으로 ⑤ Contributing/Layout/Helm을 현재 사실로 ⑥ `docs/`→`docs-site/`.
- **DoD:** README의 모든 기능 주장이 코드와 일치. 깨진 Phase 참조 0건.

### P1 — 랜딩 페이지 현행화 (`docs-site/src/components/HomepageFeatures/index.tsx` + locales)
- **현재상태:** license 카드 `ORT rulesets classify licenses`, SBOM 카드 `Excel and PDF reports are on the v2.x roadmap` — stale.
- **작업:** license/SBOM 카드 문구를 코드 사실로 정정. EN/KO `homepage.*` 번역 키 동시 갱신.
- **DoD:** 랜딩 카피가 코드와 일치. `npm run build` EN/KO green.

### P1 — 시각 자산 노출
- **작업:** README에 핵심 스크린샷 3~4장(대시보드/취약점/SBOM/승인) 임베드 + 공개 문서 사이트(GitHub Pages) 링크. 랜딩 히어로에 제품 화면 1장 + (선택) 워크스루 GIF. 기존 `docs-site/static/img/screenshots/`(40장) 재활용.
- **DoD:** README/랜딩에서 설치 없이 제품을 시각적으로 파악 가능.

### P2 — 거버넌스/기여 문서
- **작업:** `GOVERNANCE.md`(의사결정·메인테이너 권한·승급 기준), `MAINTAINERS.md`, `SUPPORT.md`(질문 경로), `.github/CODEOWNERS`(리뷰 자동 배정), `.editorconfig` 추가. 단일 메인테이너 리스크를 거버넌스 명문화로 완화.
- **DoD:** OpenSSF Best Practices 배지 self-assessment 통과 수준의 메타 완비.

### P2 — 비교표 & 포지셔닝 페이지 (`docs-site/docs/intro.md` 또는 신규 `comparison.md`)
- **작업:** "vs 상용(Black Duck/Snyk) / vs DT 단독 / vs SW360"를 기능 매트릭스로. 강점(통합 포털·RBAC·EN/KO·승인 워크플로우)과 현 한계(자동 리메디에이션·reachability 로드맵)를 정직하게.
- **DoD:** 평가자가 적합성을 30초 내 판단 가능.

---

## 3. 마일스톤 v2.1 — Triage 신뢰성 (DT 종속 완화 + 노이즈 감소)

> 목표: DT를 **직접** 쓰는 사용자 대비 최소 동등성 회복 + 평가 경로 제공.
> 선행: v2.0.1. 담당: `scan-pipeline-specialist`(DT), `backend-developer`, `frontend-dev`, `security-reviewer`(VEX 소비).

### P0 — EPSS를 UI 1급 시민으로
- **현재상태:** DT는 finding에 EPSS score/percentile을 제공하나 포털 모델/UI에 노출 여부 불명확.
- **작업:** ① `integrations/dt`의 findings 매핑에 `epss_score`/`epss_percentile` 수집 ② `vulnerabilities`/`vulnerability_findings` 모델·스키마 확장(Alembic expand) ③ 취약점 목록/드로어에 EPSS 컬럼·정렬·필터 ④ 정책 게이트에 EPSS 임계 조건 옵션.
- **의존:** DT 버전이 EPSS 노출하는지 확인(circuit breaker 캐시에도 반영).
- **DoD:** CVSS 외 EPSS 기반 정렬·게이트 가능. 단위+E2E green, EN/KO.

### P1 — VEX 소비(스캔 결과 필터링)
- **현재상태:** VEX **export**만. 외부 VEX 임포트로 finding 자동 억제 불가.
- **작업:** OpenVEX/CycloneDX VEX 문서 업로드 → `vulnerability_findings.status`를 `not_affected`/`suppressed`로 자동 매핑(근거·justification 보존, 감사로그 기록). VEX 소비/생성 왕복 일관성 테스트.
- **의존:** 기존 `vuln_finding_status` enum(VEX 7-state) 재사용.
- **DoD:** 외부 VEX로 노이즈 억제 가능. adversarial 입력(부정 VEX) 파싱 테스트 포함(untrusted-input 규칙).

### P1 — 라이브 데모 인스턴스
- **현재상태:** GCP terraform 존재하나 데모 미상시 운영. 호스팅은 Hetzner 권고(비용 우위) 검토 중.
- **작업:** 시드 데이터(샘플 프로젝트/스캔/취약점) + read-mostly 데모 계정 + 자동 일일 리셋. README/랜딩에서 연결.
- **DoD:** 설치 없이 핵심 화면 체험 가능. 데모 데이터 격리·리셋 검증.

### P2 — API 레퍼런스 호스팅
- **작업:** FastAPI OpenAPI를 Docusaurus에 통합(docusaurus-openapi 또는 정적 Redoc). `reference/api-overview.md`를 실 스펙과 연결. 기존 OpenAPI drift 게이트(Tier N) 활용.
- **DoD:** 공개 문서에서 전체 API 탐색 가능.

### P2 — Helm chart 프로덕션화 (`devops-engineer`)
- **현재상태:** `charts/trustedoss` 0.1.0 scaffold — Ingress/TLS 없음, DB/Redis 외부 전제, postgres/redis/frontend 템플릿 부재, ArtifactHub/OCI 미게시.
- **작업:** Ingress + cert-manager TLS, 번들/외부 DB·Redis 선택, frontend/beat 포함 전체 템플릿, values 문서화, **OCI(ghcr) 차트 게시 + ArtifactHub 등록**. v2.0.1의 이미지 게시에 의존.
- **DoD:** `helm install`로 단일 네임스페이스 기동. 차트 lint/template 테스트 green.

### P2 — 평가용 경량 프로파일 & 시드 데이터
- **현재상태:** 최소 4 vCPU/8 GB(DT 힙 4 GB), 설치 후 빈 화면.
- **작업:** DT 외부 연결/축소 모드의 "evaluation" compose 프로파일(저사양), 선택형 시드 데이터(샘플 프로젝트/스캔/CVE)로 첫 실행 시 제품 즉시 체감. 라이브 데모 시드와 공유.
- **DoD:** 2 vCPU/4 GB 호스트에서 평가 기동 + 시드 1커맨드.

---

## 4. 마일스톤 v2.2 — 리메디에이션 & 정책

> 목표: "발견 → 행동" 루프 완성 + 정적 라이선스 정책 한계 해소.
> 선행: v2.1. 담당: `backend-developer`, `scan-pipeline-specialist`, `security-reviewer`(GitHub 쓰기 토큰).

### P0 — 자동 의존성 업그레이드 추천 → PR
- **현재상태:** finding에 `fixed_version`은 보유. 추천·자동수정 액션 없음.
- **작업(단계):**
  - **2.2-a 추천 엔진:** finding의 `fixed_version` + cdxgen 의존성 그래프로 "최소 안전 업그레이드" 계산(severity/EPSS/depth 기준). 취약점 드로어·게이트 코멘트에 "권장 버전" 노출.
  - **2.2-b 자동 PR(옵트인):** GitHub App/토큰으로 manifest 수정 PR 자동 생성(생태계별 어댑터 우선순위: npm/pip/maven). dry-run 우선, 쓰기 권한은 `security-reviewer` 검증 필수.
- **의존:** GitHub 연동(현 webhook 수신 → 쓰기 토큰 확장).
- **DoD:** 최소 1개 생태계에서 권장 버전 PR 생성. 보안 리뷰 통과. 옵트인·감사로그.

### P1 — 동적 라이선스 정책 엔진 (ORT Evaluator 대체)
- **현재상태:** 고정 카탈로그 + forbidden 카테고리 게이트만. UI 정책 편집 불가.
- **작업:** ① per-team/per-org 라이선스 정책 모델(허용/조건부/금지 + 예외 + SPDX expression 룰) ② 정책 편집 Admin UI ③ 정책 게이트가 동적 룰 평가 ④ 라이선스 텍스트/의무 카탈로그 보강(SW360/ClearlyDefined 참고).
- **DoD:** 코드 변경 없이 팀이 정책을 편집. SPDX expression 평가에 adversarial 입력 테스트.

---

## 5. 마일스톤 v2.3 — 공급망 무결성 & 우선순위화

> 목표: 규제 정렬(CISA 2025 / SLSA) + 노이즈 추가 감소. 선행: v2.2.
> 담당: `scan-pipeline-specialist`, `backend-developer`, `security-reviewer`, `devops-engineer`.

### P1 — SBOM 서명 / provenance
- **작업:** cosign으로 SBOM 서명, in-toto attestation + SLSA provenance 생성·검증, 다운로드 시 서명 동봉. CISA 2025 신규 필수(component hash, tool/generation context)·NTIA 7요소 충족 점검. SPDX 3.0 Security profile 대응 검토.
- **DoD:** 서명된 SBOM 다운로드·외부 검증(cosign verify) 가능.

### P2 — Reachability 우선순위화
- **작업:** call-graph/dataflow 기반 "실제 도달 가능한 취약점" 표시(언어별 점진 도입; Go govulncheck 등 오픈 도구 우선). finding에 reachability 신호 추가 → 정렬·게이트.
- **리스크:** 언어별 정확도 편차 큼 → 베스트에포트로 명시, 단계적 확대.
- **DoD:** 최소 1개 언어에서 reachable/unreachable 구분 노출.

---

## 6. 비범위 (Non-goals)

- **스니펫/full-text 출처 검출 (ScanOSS급)** — 사용자 결정으로 본 로드맵에서 제외. declared(cdxgen) + detected(scancode) 라이선스 검출 범위를 유지한다. 향후 재검토 시 별도 RFC.
- **자체 취약점 DB 구축** — DT 집계를 계속 활용(EPSS/VEX로 보완). DT를 대체하지 않는다.
- **SSO/OIDC, Jenkins 네이티브 플러그인** — 기존 로드맵 항목으로 유지하되 본 4개 마일스톤 외 백로그.

---

## 7. 의존성 & 순서

```
v2.0.1 (문서/웹, 선행 없음)
   └─> v2.1 (EPSS·VEX소비·데모·API문서)
          └─> v2.2 (리메디에이션·정책엔진)   [GitHub 쓰기토큰 선행]
                 └─> v2.3 (서명/provenance·reachability)
```

- v2.0.1은 코드 무변경이라 다른 작업과 병렬 가능.
- 자동 PR(2.2-b)·SBOM 서명(2.3)·VEX 소비(2.1)는 핵심 보안 경로 → 머지 전 `security-reviewer` 필수.
- 모든 모델 변경은 Alembic forward-only + expand→migrate→contract(NOT NULL/삭제 시).

## 8. 리스크 & 컨틴전시

- **DT가 EPSS/필요 필드를 노출 안 함** → DT 버전 상향 또는 OSV.dev 보조 소스 도입(2차 데이터 소스 다양화 효과 겸함).
- **자동 PR 권한 오남용** → 옵트인·최소 권한·dry-run 기본·감사로그, 보안 리뷰 게이트.
- **reachability 정확도** → 베스트에포트 라벨, 언어 단계 확대, 과신 방지 UX.
- **1인 메인테이너 대역폭** → v2.0.1(문서/웹)으로 기여자 유입 경로부터 확보 후 기능 마일스톤 진행.
