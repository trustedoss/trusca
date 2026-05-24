# Session Handoff — 2026-05-24 — 경쟁 분석(글로벌/국내/FOSSLight) → v2.4 로드맵 수립

## 1. 무엇을 했나
- **경쟁 제품 조사** (웹 검색 기반) — 글로벌 상용·글로벌 오픈소스·국내(상용+오픈소스) 3분류.
- **FOSSLight 심층 비교** — 우리와 가장 직접 겹치는 경쟁자(오픈소스+국내출신+통합허브).
- **갭 도출 (Black Duck/Snyk/Sonatype 집중)** → 우리 로드맵에 **없던** 신규 기능 4+1개 도출.
- **로드맵 반영** — `ROADMAP.md` + `docs/post-ga-roadmap.md`에 **신규 마일스톤 v2.4** 추가. **PR #139 머지됨**(main `db806fe`).

## 2. 경쟁 구도 요약 (레포에 없는 지식 — 보존용)
우리 포지셔닝("상용급 SCA를 Apache-2.0 오픈소스·셀프호스팅으로")은 3개 전선에서 경쟁:
- **글로벌 상용(벤치마크)**: Black Duck(감사급 라이선스·바이너리·스니펫·KnowledgeBase), Snyk(개발자친화·reachability·Risk Score·Fix PR·악성탐지), Sonatype Lifecycle(정책엔진·바이너리fingerprint·**Repository Firewall 악성차단**), Mend(reachability), JFrog Xray, FOSSA(라이선스·on-prem $150K+), Checkmarx/Veracode/GitHub AS/GitLab, 신흥(Endor Labs·Socket·Aikido·Cycode).
- **글로벌 오픈소스(포지션 정면)**: **Eclipse SW360**(컴포넌트 메타데이터 허브 — 가장 유사) + **OWASP Dependency-Track**(우리가 번들로 쓰면서 동시 경쟁). 단품: Trivy/Grype+Syft/OSV-Scanner/Dependency-Check, ScanCode/FOSSology, ORT.
- **국내**: **래브라도랩스**(IRIS/SCA, 자체특허 CENTRIS·VUDDY 코드클론탐지 우위, 미국진출, AI탐지), **Sparrow**(지티원, SAST통합, 국정원 NIS-SBOM), **코드마인드 Hatter SCA**(이글루 자회사), **레드펜소프트 XSCAN**(소프트캠프 자회사, 바이너리 리버스엔지니어링), 소프트플로우(Black Duck 유통), SLEXN(Mayhem 유통).
- **FOSSLight**(LG전자, 2014사내→2021오픈소스, OpenChain): Hub(Java/Spring) + Scanner(Source[ScanCode]/Binary/Dependency/Prechecker). **핵심 차별점 = Hub가 AGPL-3.0**(우리 분류상 "금지" 라이선스 → 기업 수정/SaaS화 부담). 우리는 Apache-2.0(permissive)가 최강 카드. FOSSLight 우위: 바이너리·소스스캔 깊이·성숙도. 우리 우위: 라이선스·모던UX·DT기반 지속 CVE재탐지·EPSS/VEX·Trivy컨테이너·모던 CI게이트.

## 3. 결정 사항 / 변경된 가정
- **신규 마일스톤 v2.4 채택** (우선순위 순): P0 악성/타이포스쿼팅 탐지 · P1 CISA KEV+통합 Risk Score · P2 바이너리 스캔(Syft) · P3 AI-BOM(cdxgen ML-BOM) · **P4 스니펫(ScanOSS) 최하위**.
- **스니펫 검출 → 비범위에서 v2.4 P4로 편입** (이전 명시 제외였음). 단 **착수 전 `docs/rfc/snippet-detection.md` RFC 필수**(KB 호스팅·외부 핑거프린트 전송 동의·게이트 정책). 기술: ScanOSS(클라 `scanoss-py` MIT + 엔진 GPL-2.0 **별도 컨테이너 격리** → Apache 오염 없음), KB는 osskb.org(PoC)/자체(`minr`, 온프렘). "AI생성코드 매칭"은 스니펫과 동일 기술(별도 엔진 불필요).
- **단일 risk_score**(0–100): CVSS·EPSS·KEV·fixed_version·depth 가중합 — v2.1 EPSS·v2.3 reachability를 묶는 캡스톤.
- **바이너리 P2 비범위**: 수정·재컴파일 바이너리 핑거프린팅(Black Duck/VUDDY급)은 OSS로 불가, "알려진 바이너리 식별"까지만.
- **악성탐지 데이터 소스**: OSSF malicious-packages + OSV `MAL-` + Levenshtein 타이포 휴리스틱. 자체 취약점 DB는 여전히 비범위(DT 집계 유지).

## 4. 현재 상태
- `main` HEAD = `db806fe` (#139). v2.4 마일스톤이 `docs/post-ga-roadmap.md §6`(상세) + `ROADMAP.md`(요약)에 머지 반영. 섹션 재번호(비범위 7/의존성 8/리스크 9).
- 미머지 작업 없음. v2.4는 **계획만 수립, 구현 미착수**.
- 잔여: 원격 브랜치 `docs/roadmap-v2.4`가 머지 후에도 남음(삭제 거부됨, 무해 — 직접 정리 가능).
- 선행 마일스톤(v2.0.1 이미지 게시, v2.1 VEX소비/데모/Helm 등)은 이전 세션 핸드오프(`2026-05-24-post-ga-v2.0.1-v2.1.md`) 기준 그대로 대기.

## 5. 다음 세션이 할 일
1. **[v2.4 P0] 악성/타이포스쿼팅 탐지 착수** — PR 단위: ① OSSF malicious-packages + OSV `MAL-` 동기화 Celery 태스크 → ② `malicious` finding 모델/마이그레이션(expand) → ③ 빌드 게이트 통합(Critical 동급 차단) → ④ UI 노출(목록·드로어). 타이포 휴리스틱은 별도 PR. **핵심 보안/외부연동 → 머지 전 `security-reviewer`(Producer-Reviewer) 필수.** adversarial 입력 테스트(untrusted-input 규칙).
2. **(선행 우선이면)** 이전 핸드오프의 v2.1 VEX 소비/평가경로가 v2.4보다 앞선다 — 로드맵 순서상 v2.1~v2.3이 v2.4보다 선행. 사용자와 착수 순서 확인.
3. **[조건부] 스니펫 P4** — 만약 추진 결정 시 코드 아닌 RFC부터(`docs/rfc/snippet-detection.md`).

## 6. 주의 · 블로커
- **v2.4 내부 순서 = P0→P4 그대로.** P1(Risk Score)은 v2.1 EPSS·v2.3 reachability 캡스톤이라 후행. P4(스니펫)는 RFC 승인 전 착수 금지.
- **자율 머지 시 main CI 확인** 원칙 유지 → [[feedback-autonomous-merge-ci-check]].
- 원격 브랜치/태그 등 destructive 작업은 사용자 명시 승인 필요(이번 세션에서 브랜치 삭제 거부됨).

## 7. 다음 세션 시작 지시문 (복붙용)
> TrustedOSS Portal post-GA 진행. main=#139(db806fe). `docs/post-ga-roadmap.md`가 단일 진실 — 2026-05-24 경쟁분석(Black Duck/Snyk/Sonatype)으로 **마일스톤 v2.4**(악성탐지·KEV+RiskScore·바이너리·AI-BOM·스니펫P4)를 추가했다. 다음으로 **v2.4 P0(악성/타이포스쿼팅 탐지)**를 PR 단위로 분해해 착수하자: OSSF malicious-packages+OSV `MAL-` 동기화 태스크 → `malicious` finding 모델/마이그레이션 → 빌드 게이트 통합 → UI 순서. Pipeline+Producer-Reviewer(security-reviewer 필수), adversarial 입력 테스트 포함. ※ 단, 로드맵상 v2.1(VEX소비)~v2.3이 v2.4보다 선행이므로 착수 순서를 먼저 확인할 것.
