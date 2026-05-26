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
| **v2.4** | 공급망 위협 탐지 & 우선순위 심화 + **DT 제거** | **DT→Trivy 단일 교체 (W6, ADR-0001)**, 악성/타이포스쿼팅 탐지, CISA KEV+통합 Risk Score, 바이너리 스캔, AI-BOM, (최하위)스니펫 | minor |

**비범위(Non-goal):** 자체 취약점 DB 구축은 비범위로 유지한다(v2.4부터는 Trivy 통합 DB = NVD+OSV+GHSA+EPSS+KEV, §7). 스니펫/full-text 출처 검출(ScanOSS급)은 이전엔 명시적 제외였으나 2026-05-24 결정으로 **v2.4 P4(최하위 우선순위·RFC 선행)**로 편입한다.

우선순위 원칙: **(1)** 공개를 막는 결함 먼저 → **(2)** DT 직접 사용자 대비 동등성 회복(v2.1) → **(2.5)** DT 인프라 비용 청산(v2.4 W6) → **(3)** 차별화 기능. 각 항목은 PR 단위로 분해하고, 핵심 보안/연동 코드는 Producer-Reviewer 패턴(`security-reviewer`)을 거친다.

> **W6 (DT 제거, 2026-05-27 결정)**: v2.1의 "DT 종속 완화" 방향을 한 단계 더 밀어, v2.4.0에서 DT를 통째로 제거하고 Trivy 단일 엔진으로 교체한다. 8 PR + shadow 7일 게이트 + v2.3.1 동결 태그. SoT는 `docs/post-ga-execution-tracker.md` §0.5 W6. 결정 근거는 [ADR-0001](./decisions/0001-replace-dt-with-trivy.md). v2.3.1은 "Final Dependency-Track Release"로 동결, backport 정책은 SECURITY.md.

---

## 1. 배경 — 경쟁 갭 요약

조사 출처: dependencytrack.org, eclipse.dev/sw360, scancode-toolkit.readthedocs.io,
scanoss.com, fossology.org, github.com/anchore(syft/grype), trivy.dev,
security.googleblog.com(OSV-Scanner V2), docs.guac.sh, docs.renovatebot.com,
oss-review-toolkit.github.io, openssf.org(OpenVEX), ntia.gov(minimum elements),
CISA 2025 SBOM 요건. 상세 인벤토리는 본 세션 핸드오프에 보존.

**기능 갭 (스니펫 제외):**
- **자동 리메디에이션 부재** — 탐지·게이트까지만, "고치는 액션" 없음 (Renovate/Dependabot/OSV-Scanner v2 대비).
- **취약점 데이터 DT 단일 의존** — DT의 2차 래퍼. EPSS·우선순위 신호가 DT 노출분에 한정. (v2.4 W6에서 Trivy 단일 교체로 청산.)
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
- **높은 최소 사양·시드 부재** — 4 vCPU/8 GB + DT 힙 4 GB, 설치 후 빈 화면. 문서 사양 근거가 제거된 ORT를 인용(stale). (v2.4 W6 후 풋프린트 -50%, 2 vCPU/4 GB 수준 가능.)

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

## 3. 마일스톤 v2.1 — Triage 신뢰성 (DT 종속 완화 + 노이즈 감소) — ✅ 구현 완료

> **후속 결정(2026-05-27, ADR-0001)**: v2.1은 "DT 종속 완화"였으나 v2.4 W6에서 **DT를 통째로 제거**한다. 본 절은 v2.1 시점의 역사 기록으로 보존.

> **상태(2026-05-24):** P0~P2 전 항목 구현 완료 — EPSS UI 1급화, VEX 소비,
> 라이브 데모(`DEMO_READ_ONLY` + GCP 야간 리셋), API 레퍼런스 호스팅
> (`/reference/api`), 프로덕션 Helm 차트(`charts/trustedoss` 0.2.0), 평가 프로파일
> (`docker-compose.eval.yml` + `scripts/eval-up.sh`) 및 `/health/ready`.
> 목표: DT를 **직접** 쓰는 사용자 대비 최소 동등성 회복 + 평가 경로 제공.
> 선행: v2.0.1. 담당: `scan-pipeline-specialist`(DT), `backend-developer`, `frontend-dev`, `security-reviewer`(VEX 소비).

### P0 — EPSS를 UI 1급 시민으로
- **현재상태:** DT는 finding에 EPSS score/percentile을 제공하나 포털 모델/UI에 노출 여부 불명확. (v2.4 W6 후엔 Trivy 통합 DB가 EPSS 직접 제공.)
- **작업:** ① `integrations/dt`의 findings 매핑에 `epss_score`/`epss_percentile` 수집 ② `vulnerabilities`/`vulnerability_findings` 모델·스키마 확장(Alembic expand) ③ 취약점 목록/드로어에 EPSS 컬럼·정렬·필터 ④ 정책 게이트에 EPSS 임계 조건 옵션.
- **의존:** DT 버전이 EPSS 노출하는지 확인(circuit breaker 캐시에도 반영). (v2.4 W6 후 Trivy 결과의 EPSS 필드로 매핑 전환.)
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
- **마이그레이션 처리 (멀티 레플리카 주의):** v2.1에서 backend 이미지 entrypoint가 기동 시 `alembic upgrade head` 를 자동 적용(`AUTO_MIGRATE`)한다. **이는 docker-compose의 단일 backend 컨테이너 전제다.** `docker-compose up --scale backend=N` 또는 K8s에서 backend Deployment를 `replicas > 1` 로 띄우면 각 파드가 동시에 마이그레이션을 실행할 수 있는데, 이 동시 실행은 `alembic/env.py` 의 트랜잭션 범위 advisory lock(`pg_advisory_xact_lock`, security-reviewer M4/L1race)으로 직렬화되어 안전하다(두 번째 실행은 대기 후 이미 HEAD를 보고 no-op). 그래도 **Helm 차트에서는 backend 파드의 `AUTO_MIGRATE=false` 로 두고, 마이그레이션을 init-container 또는 `pre-install`/`pre-upgrade` Job(`alembic upgrade head`, 1회·owner 역할)으로 분리하는 것을 권장한다** — advisory lock은 안전망일 뿐 모든 파드에서 마이그레이션을 돌리라는 허가가 아니다.
- **마이그레이션 readiness follow-up (security-reviewer M2):** worker/beat의 `depends_on: backend(service_healthy)` 게이트는 `AUTO_MIGRATE=true` 일 때만 "스키마가 HEAD"를 보장한다(=false면 단순 기동 순서 의존). 진짜 readiness를 위해 backend에 **스키마가 HEAD인지 확인하는 `/health/ready` 엔드포인트**를 추가(현재 마이그레이션 head vs `alembic_version` 비교)하고 compose/Helm 게이트를 `service_healthy` 대신 readiness 기준으로 전환한다. (backend follow-up — `backend-developer`.)
- **DoD:** `helm install`로 단일 네임스페이스 기동. 차트 lint/template 테스트 green. 마이그레이션은 Job/init-container로 1회 실행되고 backend 파드는 자동 마이그레이션을 끈다.

### P2 — 평가용 경량 프로파일 & 시드 데이터
- **현재상태:** 최소 4 vCPU/8 GB(DT 힙 4 GB), 설치 후 빈 화면. (v2.4 W6 후 DT 제거로 2 vCPU/4 GB 자연 달성.)
- **작업:** DT 외부 연결/축소 모드의 "evaluation" compose 프로파일(저사양), 선택형 시드 데이터(샘플 프로젝트/스캔/CVE)로 첫 실행 시 제품 즉시 체감. 라이브 데모 시드와 공유. (v2.4 W6 후 축소 모드 무의미해짐 — 풀스택이 이미 저사양.)
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

## 6. 마일스톤 v2.4 — 공급망 위협 탐지 & 우선순위 심화

> 목표: 글로벌 상용(Black Duck·Snyk·Sonatype) 대비 **로드맵에 아직 없던** 차별 기능을
> best-of-breed OSS로 메운다. 탐지 범위를 패키지 멀웨어·바이너리·AI까지 넓히고, 분산된
> 위험 신호를 단일 점수로 통합한다. 선행: v2.3(단 P1은 v2.1 EPSS·v2.3 reachability에 의존).
> 담당: `scan-pipeline-specialist`, `backend-developer`, `frontend-dev`, `security-reviewer`.
> 출처: 본 세션 경쟁 분석(2026-05-24, Black Duck/Snyk/Sonatype 집중).

### P0 — 악성 패키지 / 타이포스쿼팅 탐지 (1순위)
- **배경:** Snyk(malicious package 탐지) + **Sonatype Repository Firewall**(의심 컴포넌트 자동 격리)의 플래그십 기능. 공급망 공격(npm/pypi 멀웨어 급증) 대응의 1차 방어선인데 우리엔 부재.
- **현재상태:** 알려진 CVE만 탐지. 멀웨어/타이포스쿼팅 신호 없음.
- **작업:**
  - **OSSF Malicious Packages**(`github.com/ossf/malicious-packages`) + **OSV.dev `MAL-` 피드**를 Celery로 주기 동기화 → 탐지된 PURL과 대조(v2.4 W6 후 `vulnerability_findings` persist 패턴 재사용).
  - **타이포스쿼팅 휴리스틱:** 생태계별 인기 패키지 top-N 대비 Levenshtein 거리 + 신규/저신뢰 패키지 플래그.
  - 새 finding type `malicious` → 취약점 목록·드로어 노출 + **빌드 게이트 즉시 차단**(Critical 동급).
  - (확장) 스캔 시점 "firewall 모드"(탐지 시 스캔 실패). 진짜 프록시 차단(Sonatype급)은 후속 백로그.
- **의존:** 기존 cdxgen PURL, `vulnerability_findings` persist 패턴(v2.4 W6 후 Trivy 결과 동일 모델).
- **DoD:** npm/pypi 최소 2개 생태계에서 멀웨어/타이포 탐지 + 게이트 차단. adversarial 입력 테스트(untrusted-input 규칙). `security-reviewer` 통과.

### P1 — CISA KEV + 통합 Risk Score (2순위)
- **배경:** Snyk **Risk Score**(CVSS 외 12+ 요소를 단일 점수로). 우리는 위험 신호가 분산 → alert fatigue.
- **현재상태:** CVSS 위주. EPSS(v2.1)·reachability(v2.3)가 들어오지만 하나의 순위 점수로 통합 안 됨.
- **작업:**
  - **CISA KEV** 무료 피드 동기화 → "known-exploited" 플래그. (v2.4 W6 후 Trivy 통합 DB가 KEV 자체 제공 — 별도 동기화 PR 불필요 시 W6 후 본 항목 단축 가능. §9 컨틴전시 정합.)
  - `risk_score` 계산 서비스: `CVSS·EPSS·KEV 여부·fixed_version 유무·depth(직접/전이)` 가중 합산(0–100) + 점수 분해("왜 이 점수") 표시.
  - 정렬 컬럼 + 게이트 임계 + 드로어 근거.
- **의존:** v2.1(EPSS)·v2.3(reachability)를 묶는 **캡스톤**이라 그 뒤 권장. KEV 자체는 선행 없이 가능.
- **DoD:** 단일 `risk_score`로 정렬·게이트. EN/KO, 단위+E2E green.

### P2 — 바이너리 스캐닝 (OSS-in-binary) (3순위)
- **배경:** Black Duck·Sonatype + 국내 FOSSLight·레드펜 XSCAN·래브라도. 국내 경쟁에서 자주 요구.
- **현재상태:** Trivy로 컨테이너 OS 패키지만. 빌드 산출물/펌웨어 내 OSS 미식별.
- **작업:** **Syft binary classifier** + Trivy `fs`/`rootfs` 모드를 새 스캔 종류 "binary"로 추가 → SBOM 생성 후 기존 스캔 파이프라인 주입(v2.4 W6 후엔 cdxgen→Trivy 동일 배관 재사용).
- **비범위:** 수정·재컴파일 바이너리 핑거프린팅(Black Duck/VUDDY급)은 OSS로 불가 — 명시.
- **DoD:** 알려진 바이너리(openssl/busybox 등) 식별 → 컴포넌트·취약점 연결.

### P3 — AI-BOM (AI 모델/데이터셋 컴포넌트) (4순위)
- **배경:** Sonatype(AI 컴포넌트 분석 Forrester 1위)·래브라도(SCA에 AI 모델 탐지). 신흥 규제 영역.
- **현재상태:** 없음.
- **작업:** **cdxgen의 CycloneDX ML-BOM** 활성화 → torch/transformers/HuggingFace 모델 참조 탐지, `machine-learning-model` 컴포넌트 타입 + 모델/데이터셋 라이선스 뷰.
- **DoD:** 최소 1개 ML 프로젝트에서 모델 컴포넌트·라이선스 노출.

### P4 — 스니펫 / AI생성코드 출처 매칭 (ScanOSS) — **최하위 우선순위**
- **배경:** Black Duck snippet/AI 코드 탐지. **이전 비범위였으나 2026-05-24 결정으로 최하위로 편입.**
- **현재상태:** declared(cdxgen) + detected(scancode) 라이선스만. 출처 스니펫/AI코드 매칭 없음.
- **접근:** **ScanOSS** — 클라이언트 `scanoss-py`(MIT) import + 엔진(GPL-2.0)은 **별도 컨테이너로 격리**(Trivy와 동일 패턴 → Apache-2.0 오염 없음). KB는 osskb.org(PoC) / 자체 KB(온프렘, `minr`).
- **트레이드오프:** KB가 진짜 해자(자체 호스팅 무거움) · 외부 핑거프린트(WFP 해시, 원본 아님) 전송 · 퍼지 매칭 노이즈.
- **선행(필수):** 별도 **RFC** `docs/rfc/snippet-detection.md`에서 KB 호스팅 정책·외부 전송 동의·게이트 통합을 결정한 뒤에만 착수.
- **DoD:** RFC 승인 → PoC(osskb.org, opt-in)에서 스니펫 출처·라이선스 노출.

---

## 7. 비범위 (Non-goals)

- ~~**스니펫/full-text 출처 검출 (ScanOSS급)**~~ — 이전 비범위였으나 2026-05-24 결정으로 **v2.4 P4(최하위 우선순위)로 편입**(§6). 착수 전 별도 RFC(`docs/rfc/snippet-detection.md`)가 선행 조건이다.
- **자체 취약점 DB 구축** — v2.4부터는 Trivy 통합 DB(NVD+OSV+GHSA+EPSS+KEV)를 활용. 자체 NVD/OSV 미러는 운영 부담 대비 이득 없음(ADR-0001).
- **SSO/OIDC, Jenkins 네이티브 플러그인** — 기존 로드맵 항목으로 유지하되 본 4개 마일스톤 외 백로그.

---

## 8. 의존성 & 순서

```
v2.0.1 (문서/웹, 선행 없음)
   └─> v2.1 (EPSS·VEX소비·데모·API문서)
          └─> v2.2 (리메디에이션·정책엔진)   [GitHub 쓰기토큰 선행]
                 └─> v2.3 (서명/provenance·reachability)
                        └─> v2.4 (악성탐지·KEV+RiskScore·바이너리·AI-BOM·스니펫P4)
```

- v2.0.1은 코드 무변경이라 다른 작업과 병렬 가능.
- **v2.4 내부 순서는 P0→P4 우선순위 그대로.** P0(악성 탐지)·P2(바이너리)는 v2.1~v2.3 의존이 없어 조기 병행 가능하나, P1(Risk Score)은 v2.1 EPSS·v2.3 reachability를 묶는 캡스톤이라 후행. P4(스니펫)는 RFC 승인 전까지 착수 금지.
- 자동 PR(2.2-b)·SBOM 서명(2.3)·VEX 소비(2.1)·**악성 탐지(2.4 P0)·스니펫(2.4 P4)**은 핵심 보안/외부연동 경로 → 머지 전 `security-reviewer` 필수.
- 모든 모델 변경은 Alembic forward-only + expand→migrate→contract(NOT NULL/삭제 시).

## 9. 리스크 & 컨틴전시

- **DT가 EPSS/필요 필드를 노출 안 함** → DT 버전 상향 또는 OSV.dev 보조 소스 도입(2차 데이터 소스 다양화 효과 겸함). **(v2.4 W6 후 무효 — Trivy 통합 DB가 EPSS·KEV 직접 노출.)**
- **Trivy 매칭이 DT 대비 누락(v2.4 W6)** → shadow 7일 게이트(일치율 ≥95%, 조기 종료 가능: 3일 연속 ≥95% + 누적 ≥30건/생태계 ≥3종)로 사전 감지. 미달 시 Plan B(Trivy+OSV-Scanner) 또는 Plan C(DT 유지+Trivy 보조)로 회귀. 상세 ADR-0001.
- **자동 PR 권한 오남용** → 옵트인·최소 권한·dry-run 기본·감사로그, 보안 리뷰 게이트.
- **reachability 정확도** → 베스트에포트 라벨, 언어 단계 확대, 과신 방지 UX.
- **악성 탐지 오탐(v2.4 P0)** → 멀웨어/타이포 휴리스틱은 명확한 출처(OSSF/OSV `MAL-`) 우선, 휴리스틱 단독 차단은 경고로 분리 + waiver/억제 경로 제공.
- **스니펫 KB 의존(v2.4 P4)** → osskb.org 외부 전송은 WFP 해시(원본 아님)지만 셀프호스팅·주권 메시지와 일부 상충 → opt-in 기본 + 온프렘 자체 KB 경로 문서화. RFC에서 결정.
- **1인 메인테이너 대역폭** → v2.0.1(문서/웹)으로 기여자 유입 경로부터 확보 후 기능 마일스톤 진행.
