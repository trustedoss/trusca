# 핸드오프 — W1 신뢰 복구 완료, W2~W4 BD 정합 대기 (2026-05-26)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 "수동 테스트 발견 + Black Duck 정합 (Wave 1~4)". 이 문서는 그 세션 스냅샷이다.

## 이번 세션에 한 일

**W1 신뢰 복구 — 전부 ✅ 완료** (Black Duck 6화면 갭분석의 "스캔 결과 신뢰 루프" 3건):

- **#29** 스캔 상태 추적 — `recent_scans` 항상 노출 + 헤더 진행중 칩 *(이전 세션 작업분, 이번에 함께 커밋)*
- **#34** 리스크 점수 2축 재설계 — `services/risk_score.py` 단일 소스(밴드 기반 비포화, 밴드 내 `n/(n+4)`), Security/License 분리, `risk_score`=max(축) back-compat. Overview는 `RiskAxes`(2축 게이지). conditional 단독 "Risk 100 Critical" 버그 제거.
- **#35** DT 무경고(silent zero):
  - 운영: DT `nvd.api.enabled=true`+재시작 → NVD 미러 가동(다운로드 중, 완료까지 ~1h. **완료 후 재스캔해야 기존 프로젝트에 CVE가 잡힘**)
  - Surface A: admin/DT 페이지 vuln-DB 카운트 + 0건 경고 Alert
  - Surface B: 스캔 시점 DT vuln-DB 크기를 `scan_metadata['dt_vulnerability_count']`에 저장 → Overview 응답 `vuln_data_available`(tri-state) → Security 0·DB비었음 시 "데이터 미적재" 캐비엇

## git / CI 상태 (중요 — 비정상 워킹트리였음)

- 세션 시작 시 main 워킹트리에 **여러 세션 누적 미커밋 변경 193파일**(W1 + 대시보드·릴리스/diff·Compare·git credential 등)이 있었음. 사용자 지시로 **일괄 커밋 → main 푸시** (`7e3ccd0`).
- 그 커밋의 main CI가 **빨강**이었고(번들된 타 세션 작업 버그 3건), 모두 픽스:
  - `ruff`: `0024_project_git_credential.py` docstring E501
  - `pytest`: `test_project_list_enrichment.py` #29 계약 모순 어서션 / `test_admin_ops_api.py` DT status mock(`breaker.call` side_effect 분리 + 코드 `int()` 방어)
- **주의:** `7e3ccd0`에 frontend 루트 디버그 잔여물 `*walk*.mjs` 7개가 함께 커밋됨 → 정리 권장(`git rm`).
- **미푸시 잔여:** W1 Surface B + 위 CI 픽스 + 본 핸드오프는 백엔드 풀 스위트 green 확인 후 별도 커밋·푸시 예정.

## 다음 세션: W2~W4 (BD 정합 본체 — 전부 ⬜ 미착수)

트래커 §0.5 표 기준. 권장 순서:

- **W2 BD 정합** (핵심)
  - **#31** Components 탭 Direct/Transitive 구분 + Usage 노출
  - **#33** 취약점 조치신호(Exploitable/Solution)+CVSS 벡터 · 목록 License 리스크축 · Bulk actions
- **W3 통합/발견성**
  - **#32** 통합 Reports 센터 탭 (Notices/SBOM/Vuln/VEX 생성·이력)
  - **#30** 프로젝트 목록 행에 릴리스/스캔 수 표시
- **W4 후속/위생**: #26·#27·#19~#22 (vex_import 앵커 보안검토 · vuln 툴바 레이아웃 · 콘솔 위생/정리)

## 환경 메모

- 테스트는 컨테이너에서: `docker-compose exec -T backend python -m pytest …` (conftest가 `redis`/`postgres` 호스트명에 연결 → 호스트 직접 실행 불가). 백엔드 코드 편집 후 `docker-compose restart backend`.
- 프론트: `npm run typecheck` / `lint` / `i18n:check`(푸시 전 필수, 복수형 `_one/_other` 금지) / `npx vitest run`.
