# UX 감사 중 발견된 버그·결함

> 양식: 계획서 §14. 분류 B0/B1/B2. Phase E에서 트래커 등재 후보 검토.

---

## B2 — 001: Vulnerability drawer References 가 모두 "REF" 텍스트만 (2026-05-27 07:52)
- **화면**: O6 (`drawer-vulnerability-detail.png`) — fx-maven-node / CVE-2024-45296 (path-to-regexp@0.1.7)
- **재현**:
  1. `/projects/{fx-maven-node}/?tab=vulnerabilities`
  2. 첫 finding (path-to-regexp@0.1.7 / CVE-2024-45296) 행 클릭
  3. 우측 드로어의 "References (8건)" 섹션 확인
- **기대**: 각 reference 가 (a) 클릭 가능한 URL or (b) advisory title + URL 표시
- **실제**: 8개 reference 모두 "REF" 라는 텍스트만 (실제 GitHub Advisory / CVE URL 미표시)
- **증거**: `screens/ours/drawer-vulnerability-detail.png` (우하단 References 블록)
- **추정 원인 영역**: 드로어 References 컴포넌트가 Trivy 결과의 `reference_urls` 필드를 렌더할 때 텍스트 누락 또는 i18n 키 `vulnerabilities.drawer.references.ref` 가 placeholder ("REF") 인 채 노출. `apps/frontend/src/features/vulnerabilities/` 의 drawer 컴포넌트 후보. 또는 W6-#41 Trivy persist 시 `reference_urls` 가 비어들어왔을 가능성.
- **분류 사유**: B2 — 화면은 뜨고 클릭 자체는 가능, 사용자 작업이 차단되진 않지만 advisory 추적 가치 0. 시각/데이터 결함.

---

## B2 — 002: Vulnerability drawer Summary 가 동일 단락 2번 반복 (2026-05-27 07:52)
- **화면**: O6 — fx-maven-node / CVE-2024-45296 (path-to-regexp@0.1.7)
- **재현**: 위 B2-001과 동일
- **기대**: Summary 단락 1회
- **실제**: 동일 텍스트 "path-to-regexp turns path strings into a regular expressions. In certain cases..." 가 위/아래 두 단락에 연속 표시
- **증거**: 동일 PNG, 상단 Summary 섹션
- **추정 원인 영역**: 드로어가 vulnerability.summary 와 description 을 동시에 렌더하는데 두 필드에 같은 값 — DB / Trivy result mapping 에서 같은 텍스트가 두 컬럼에 들어감. 또는 컴포넌트 측에서 동일 prop 두 번 렌더. `services/vulnerability_matching.py` (W6-#41) 또는 vuln drawer 컴포넌트 확인.
- **분류 사유**: B2 — 가독성 저하, 사용자 혼란 가능. 차단은 아님.

---

## D1 — 001: Dashboard route `/` 가 `/projects` 로 redirect (Dashboard 부재) (2026-05-27)

**버그가 아닌 design observation** — 의도된 product 결정으로 기록.

- **현재 상태**: `router.tsx:62` `<Route index element={<Navigate to="/projects" replace />} />` 코멘트에 "Dashboard was dropped in the [...]". 명시적 결정.
- **영향**:
  - 로그인 직후 사용자는 project list 로 직행 (전사 risk 포트폴리오 부재)
  - audit 화면 O1 캡처가 O2 와 byte-identical
  - 경쟁 비교: Black Duck / Snyk / Sonatype 모두 dedicated dashboard 가 있음 (per Phase B에서 확인 예정)
- **선택**:
  - (a) 이대로 유지 — project list 가 사실상 landing 으로 충분하다고 판단
  - (b) Dashboard 복귀 — multi-project tenant 에서 cross-project KPI 노출 가치
- **Phase D 판정**: Phase B (경쟁 도구 조사) 완료 후 (a)/(b) 갭 분석. 진짜 갭이면 W9 후보로 등재.
- **분류 사유**: 버그 아님. design 결정.

---

## 발견 추적

| 일자 | ID | 분류 | 상태 |
|---|---|---|---|
| 2026-05-27 07:52 | B2-001 | B2 (Non-blocking) | 신규 — Phase E에서 트래커 등재 검토 |
| 2026-05-27 07:52 | B2-002 | B2 (Non-blocking) | 신규 — Phase E에서 트래커 등재 검토 |
| 2026-05-27 07:52 | D1-001 | Design observation | Phase D에서 비교 후 판단 |
