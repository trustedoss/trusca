# scancode 메타-오탐 진단 — dogfood self-scan

> 작성: 2026-05-31 · 세션: license-override 마무리 묶음 (작업 C)
> 결론: **advisory 수용 + 문서화**, per-project scan-exclude 기능은 백로그(ROADMAP § Backlog) 등재.
> 제품 코드 변경 없음.

---

## 1. 현상

dogfood self-scan(`.github/workflows/dogfood-scan.yml` — 우리 레포를 우리 `actions/scan`으로
ephemeral dev compose에서 self-scan)이 source 스캔의 scancode 단계
(`apps/backend/tasks/scan_source.py` Stage 4)에서 **우리 자신의 라이선스 분류 코드·카탈로그
문서·테스트 fixture를 실제 라이선스 텍스트로 오검출**한다. 이 파일들은 SPDX id
(`GPL-3.0-only`, `AGPL-3.0-only` 등)를 **데이터로 나열**하기 때문이다.

대표 오탐원:
- `apps/backend/tasks/scan_source.py` — `_LICENSE_CATEGORY_DEFAULTS`(SPDX id → 분류 하드코딩 dict)
- `apps/backend/tests/e2e/golden/baselines/scancode-*.json` — scancode baseline(SPDX id 나열)
- 라이선스 분류를 설명하는 카탈로그/문서

scancode 탐지 라이선스는 단순 노이즈가 아니다 — `_persist_detected_licenses`로 first-party
컴포넌트에 부착되고, `_auto_create_conditional_approvals`(Stage 4.5)가 conditional 라이선스를
법무 검토 큐에 등록하며, first-party 라이선스 인벤토리에 반영된다. 즉 **self-scan의 라이선스
인벤토리·승인 큐를 오염**시키고, forbidden 분류가 부착되면 게이트 신호까지 흐릴 수 있다.

> 참고: pyphen 게이트 fail은 별개 이슈다 — cdxgen이 PyPI classifier 3종(GPL/LGPL/MPL 택1)을
> GPL 단일로 축소한 입력 손상이며, PR #321(component-scoped waive)로 사람이 보정한다.

## 2. 진단 — 스캔 범위를 좁힐 레버가 있는가

**없다.** per-project / per-scan path-ignore 메커니즘이 어느 계층에도 존재하지 않는다:

| 계층 | 확인 | 결과 |
|------|------|------|
| Project 모델 | `apps/backend/models/scan.py` | `exclude_paths` / `ignore_globs` 컬럼 없음 |
| ScanCreate 스키마 | `apps/backend/schemas/scan.py` | `scan_metadata`에 exclude 옵션 없음 |
| 스캔 트리거 API | `POST /v1/projects/{id}/scans` | 범위 제어 파라미터 없음 |
| scan-action 입력 | `actions/scan/action.yml` | exclude/ignore/path 입력 없음 |
| dogfood 워크플로우 | `.github/workflows/dogfood-scan.yml` | scan-action에 범위 입력 전달 안 함(불가) |

유일한 제외는 scancode 어댑터에 **하드코딩된** `EXCLUDED_DIR_NAMES`
(`apps/backend/integrations/scancode.py`)뿐이다 — vendored deps / build output / VCS 메타데이터
(node_modules, vendor, dist, .git 등)만 제외하고 **`tests` / `docs`는 포함하지 않는다**.

### 왜 전역 scancode 제외는 금지인가
`EXCLUDED_DIR_NAMES`에 `tests` / `docs`를 추가하면 **모든 고객 스캔**에서 그 디렉토리의 라이선스
검출이 빠진다. 고객이 `tests/` fixtures 아래 GPL 코드를 vendoring한 경우는 정당하게 탐지돼야
하므로, 전역 제외는 제품 회귀다.

### dir 제외로도 못 거르는 잔여분
설령 `tests` / `docs`를 제외하더라도 **라이선스 카탈로그 *소스 코드* 자체**
(`scan_source.py`의 `_LICENSE_CATEGORY_DEFAULTS` SPDX 리터럴)는 디렉토리 단위 제외로 거를 수
없다(그 디렉토리 전체를 제외할 수는 없으므로). 이는 **자신의 라이선스 카탈로그를 코드로 들고 다니는
SCA 도구가 자기 자신을 스캔할 때의 고유 특성**이며, 상용 SCA 도구도 self-scan 시 동일하게 겪는다.

## 3. 결정 (사용자 인터뷰 2026-05-31)

**advisory 수용 + 문서화.** dogfood self-scan의 이 오탐은 "라이선스 카탈로그를 코드로 가진 SCA
도구를 자기 자신에 돌릴 때의 알려진 특성"으로 수용한다. dogfood는 외부/고객 대상이 아니라 우리
CI 연동을 검증하는 내부 거버넌스 점검이고(결과는 step summary + artifact, 외부 미전송), 게이트는
이미 `fail_on_gate=false` advisory로 운용된다(dogfood 메모리 참조).

제품 코드는 **변경하지 않는다**(전역 scancode 제외 금지, 카탈로그 소스 오탐은 본질적).

## 4. 백로그 — per-project / per-scan scan-exclude

제대로 된 해결은 **사용자 지정 제외 경로** 기능이다(ROADMAP § Backlog 등재). 고객에게도 유용한
표준 SCA 기능(생성물 / 테스트 fixtures / vendored 트리를 first-party 라이선스 검출에서 제외)이며,
다층 구현이 필요해 이번 "작은 follow-up" 범위 밖이다:

1. `actions/scan/action.yml` — `exclude-paths` input (+ CLI 스크립트 전달)
2. `POST /v1/projects/{id}/scans` + `ScanCreate` 스키마 — per-scan exclude globs
3. (선택) Project 모델 — 프로젝트 기본 exclude(마이그레이션)
4. `tasks/scan_source.py` — 사용자 exclude를 `EXCLUDED_DIR_NAMES`와 **병합**해 scancode/cdxgen에 전달

기능 구현 시 dogfood 워크플로우가 이 레버로 `docs/`·`tests/e2e/golden/`·카탈로그 경로를 제외하면
self-scan 오탐이 (카탈로그 소스 잔여분을 제외하고) 해소된다.
