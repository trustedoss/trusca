# 우리 화면 1차 관찰 노트 — 2026-05-27 (Phase A)

각 화면을 1~5줄로. 정보 밀도·눈에 띄는 패턴·미흡 사항. 점수는 Phase C에서.

## O1 — Dashboard (`/`)
- **존재하지 않음** — `/`가 `/projects`로 redirect (router.tsx:62, "Dashboard was dropped").
- 캡처 파일 `dashboard.png`는 `project-list.png`와 byte-identical.
- **경쟁 비교 대상**: BD/Snyk/Sonatype 모두 dedicated dashboard 존재. 자세한 평가는 Phase D §gap에서.

## O2 — Project List (`/projects`)
- 상단 2 카드: Vulnerability severity + License classification 분포 (전체 프로젝트 기준)
- 헤더 우측: Register project (primary action 명확)
- 검색 / Status / Sort by 인라인 toolbar — 모달 없음 (CLAUDE.md design spec 부합)
- 행: name + Rel/Scn 메타 + 시간 + status badge + Scan 버튼 (40px compact 행, 18개 가시)
- 잘됨: density, scannable, 한 화면에 많은 정보
- 미흡: 우측 끝 Scan 버튼이 emoji 같은 아이콘으로 겹쳐 보임 (캡처 우하단 작은 이미지) — Vite HMR overlay 일 수도. 운영 환경 확인 필요.

## O3 — Project Detail — Overview
- breadcrumb + 큰 제목 + UUID + Version selector + Scan 버튼
- 8 탭: Overview · Versions · Components · Vulnerabilities · Source · Compliance · Reports · Settings (W4 IA 정합 — 11→8 정리 후)
- 4 카드: Project info (description/repo/branch/visibility) + Build gate (Pass + critical/forbidden counts) + Vulnerability severity (by component) + License classification (by component)
- 하단 Recent scans 테이블 (5건)
- 잘됨: 매우 dense, primary KPI 즉시 가시
- 관찰: Build gate "Pass" badge 디자인 깔끔. severity/license 차트가 horizontal bar — Phase C에서 차트 인터랙션 평가

## O4 — Project Detail — Components
- 상단 동일 2 차트 + by-component scope
- 필터: 검색 / Dependency type segmented (All/Direct/Transitive) / Usage select
- 컬럼: COMPONENT (name + purl) + TYPE badge + VERSION + LICENSE + POLICY badge + USAGE badge + SEVERITY + CVES
- W4 #190 lockfile fallback 효과로 TYPE/USAGE 채워짐 (이전 NULL)
- 잘됨: BD 수준 dense 인벤토리, purl 정밀 노출, JetBrains Mono 적용
- 관찰: 9 컬럼 → 1440px width 에서 빡빡함, horizontal scroll 없음 확인

## O5 — Project Detail — Vulnerabilities
- 상단 차트: Vulnerability severity (by finding) — Critical/High/Medium/Low/Info/Unknown
- 매우 풍부한 필터: Search / Status / Reachability / EPSS / VEX + VEX export(OpenVEX/CycloneDX) + Import VEX
- 컬럼: 체크박스 + CVE ID + COMPONENT (name@version) + SEVERITY + CVSS + EPSS + REACHABILITY + STATUS
- W4 #191 follow-up 효과로 component@version 컬럼 채워짐
- bulk action: 체크박스 헤더 + 행 (실제 bulk action bar 노출은 행 선택 시점 — 캡처에선 안 보임)
- 잘됨: SCA 핵심 화면, 필터 매우 풍부, sortable column header (3-state cycle from W4-B-prep)
- 미흡: 정렬 화살표 visible affordance 약함 (헤더 hover 전엔 sortable 인지 어려움 — Phase C에서 정밀 평가)

## O6 — Drawer (Vulnerability detail)
- 슬라이드 드로어 오른쪽 절반 (page nav 안 함, 컨텍스트 보존)
- 구조: CVE ID + Status / Severity / CVSS / Reachability / Discovered badges + Summary + Recommended upgrade + References + (스크롤하면 더)
- 잘됨: 컨텍스트 보존, 적절한 정보 깊이
- **B2 버그 발견 2건** — `bugs-found.md` 참조:
  1. References 섹션이 모두 "REF" 텍스트만 (URL/title 미표시)
  2. Summary 단락이 동일 내용 2번 (paragraphic duplication)

## O7 — Project Detail — Reports
- 좌: Generate 4 카드 (NOTICE/SBOM/Vulnerability PDF/VEX) — 각 카드 action 버튼이 도메인 탭으로 deeplink (`Go to Compliance`, `Jump to SBOM downloads`, `Download PDF report`, `Go to Vulnerabilities`)
- 우: Recent activity 테이블 (빈 상태 — fixture 에서 다운로드 안 한 상태) + Type filter
- 하단 (캡처 폴드): SBOM downloads section
- 잘됨: 생성/이력 분리, 빈 상태 메시지 명확 ("No downloads yet. Use the shortcuts on the left to generate...")
- 관찰: Generate 카드들이 직접 생성하지 않고 도메인 탭으로 이동하는 점은 호불호 — Phase D에서 평가

## O8 — Scans Queue (`/scans`)
- 헤더 + 5 status tab (Running/Queued/Succeeded/Failed/All)
- 컬럼: PROJECT + KIND badge + STATUS badge + STARTED + DURATION + ACTIONS
- WebGoat (Maven)이 Failed로 표시됨 — scan-bench 에서 발견된 W8-#46 의 흔적
- 잘됨: dense queue view, status tab 분류 명확
- 관찰: ACTIONS 컬럼 비어 보임 — Running/Queued 상태가 아니라 hide 되는 듯 (Phase C 평가)

## KO 캡처 — 핵심 3장
- 모든 사이드바·헤더·필터·badge·컬럼 한국어 정확 (i18n 정합)
- ko/dashboard.png (= ko/project-list 효과): "취약점 심각도" / "라이선스 분류" / "프로젝트 등록" / "스캔" — 자연스러운 KO
- 데이터 값 자체는 EN(영문 fixture 이름) 유지 — 정상
- "심각도" "높음" "중간" "허용" 등 도메인 용어 일관

---

## 캡처 자체에 관한 메타 관찰
- 1440×900 @ 2x DPR PNG — 평균 350~520KB (적절, docs reuse 충분)
- 풀-페이지 캡처는 대부분 viewport와 사이즈 유사 (스크롤 영역이 길지 않음 — 부피 잘 관리됨)
- 드로어는 풀-페이지 의미 없어 skip — 정상
- 캡처 도중 console error 별도 점검 안 함 — Phase B 후 별도 sweep 권장
