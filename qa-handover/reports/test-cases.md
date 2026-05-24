# TrustedOSS Portal 출시 전 QA — 마스터 테스트 케이스 카탈로그

> **작성**: 외부 독립 QA | **대상**: v2.0.0 | **일자**: 2026-05-24 | **용도**: 검증 범위 증빙(`qa-report.md`·`bug-report.md`의 근거)
> 출처(명세): `docs-site/docs/`(user-guide·admin-guide·ci-integration·reference·installation/uat-checklist) + CLAUDE.md
> 종류: N=정상 / E=엣지·실패 / S=보안 / X=횡단
> 우선순위: P0(출시 차단/안정성) · P1(주요) · P2(확장)
> 검증 레이어: E2E(브라우저) · API · CI · L(부하) · 수동
> **3-Pass 검증**: Pass1=영역별 도출(완료) / Pass2=횡단·누락 발굴 / Pass3=문서 1:1 전수 확인

---

## A. 인증·세션·프로필 (auth-and-profile)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| AUTH-01 | 올바른 이메일/비밀번호 로그인 → /projects | N | P0 |
| AUTH-02 | 잘못된 비밀번호 → "Invalid email or password" + URL 유지 | E | P0 |
| AUTH-03 | 존재하지 않는 이메일 → 동일 메시지(열거 방지) | S | P0 |
| AUTH-04 | 빈 입력 제출 → 필드 검증 | E | P0 |
| AUTH-05 | 로그인 레이트리밋 5회/분 초과 → 429 + Retry-After:60 | S | P0 |
| AUTH-06 | 미인증 보호 페이지 접근 → /login 리다이렉트 | N | P0 |
| AUTH-07 | 로그아웃 → 세션 종료 + 보호 페이지 차단 | N | P0 |
| AUTH-08 | access 토큰 만료(30분) → refresh 자동 회전 | N | P1 |
| AUTH-09 | refresh 토큰 재사용 탐지 → 전체 체인 취소 | S | P0 |
| AUTH-10 | 비밀번호 찾기 요청 → 204(항상, 열거 방지) | S | P0 |
| AUTH-11 | 리셋 토큰 24시간 유효 + 1회용 | S | P0 |
| AUTH-12 | 리셋: 새 비밀번호 12자 미만 거부 | E | P0 |
| AUTH-13 | 리셋: breach 사전 비밀번호 거부 | S | P1 |
| AUTH-14 | 리셋 성공 → 모든 refresh 취소(재인증 강제) | S | P0 |
| AUTH-15 | OAuth(GitHub/Google) 첫 로그인 → 계정+개인팀 생성 | N | P0 |
| AUTH-16 | OAuth 후속 로그인 → (provider, provider_user_id) 조회 | N | P0 |
| AUTH-17 | OAuth 이메일 리사이클 차단(계정 takeover 방지) | S | P0 |
| AUTH-18 | OAuth 7종 에러(거부/스코프/만료/반복/충돌/중단/5xx) i18n 표시 | E | P1 |
| AUTH-19 | /profile Connected Accounts 표시 | N | P0 |
| AUTH-20 | 유일 로그인 수단 unlink → 409 | E | P0 |
| AUTH-21 | refresh 쿠키 HttpOnly+Secure+SameSite=Lax | S | P0 |

## B. 프로젝트 (projects)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| PROJ-01 | 생성: 필수 이름만 → 성공 | N | P0 |
| PROJ-02 | 생성: 빈 이름 → "프로젝트 이름은 필수입니다" ✅검증완료 | E | P0 |
| PROJ-03 | 생성: 팀 내 이름 중복 거부 | E | P0 |
| PROJ-04 | 생성: 잘못된 git URL → 형식 검증 ✅검증완료 | E | P1 |
| PROJ-05 | 생성: http:// git URL 허용 여부(SSRF 관점 백엔드 차단 확인) | S | P1 |
| PROJ-06 | 생성: git@/ssh:// 거부(v2.0 HTTPS만) | E | P1 |
| PROJ-07 | 생성: PAT 포함 HTTPS URL 정상 | N | P0 |
| PROJ-08 | 생성: 특수문자/XSS 이름 → escape 렌더 | S | P1 |
| PROJ-09 | API: 필수필드 누락 → 422 | E | P0 |
| PROJ-10 | API: 알 수 없는 필드 → 거부(extra=forbid) | E | P0 |
| PROJ-11 | 목록: 검색(부분일치/결과없음) | N | P0 |
| PROJ-12 | 목록: 상태 필터/정렬(이름·스캔일·리스크) | N | P0 |
| PROJ-13 | 목록: 빈 상태 | N | P1 |
| PROJ-14 | 상세: 8개 탭 전환 | N | P0 |
| PROJ-15 | 상세: 존재하지 않는 ID → Not Found alert ✅검증완료(BUG-002/004) | E | P0 |
| PROJ-16 | 리스크 점수 0~100, 스캔 없으면 중립 뱃지 | N | P0 |
| PROJ-17 | 아카이브(소프트 삭제) + 인라인 확인 | N | P0 |
| PROJ-18 | 아카이브 후 새 스캔 차단 + 목록 숨김 | N | P0 |
| PROJ-19 | developer 미만 권한 생성 시도 → 403 | S | P0 |

## C. 스캔 (scans)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| SCAN-01 | 소스 스캔 트리거(Git URL) → queued | N | P0 |
| SCAN-02 | 소스 스캔: .zip 업로드 | N | P0 |
| SCAN-03 | 소스 스캔: 폴더 선택 업로드 | N | P1 |
| SCAN-04 | 컨테이너 스캔(Trivy) 트리거 | N | P0 |
| SCAN-05 | 컨테이너: 이미지 참조 형식(name:tag) 검증 | E | P1 |
| SCAN-06 | 상태 전이 queued→running→succeeded | N | P0 |
| SCAN-07 | 실패 → failed + error_detail 표시 | E | P0 |
| SCAN-08 | WebSocket 실시간 진행률 업데이트 | N | P0 |
| SCAN-09 | 파이프라인 6단계 진행 표시 | N | P0 |
| SCAN-10 | scancode 타임아웃 → 선언 라이선스만 계속(비치명) | E | P1 |
| SCAN-11 | DT 서킷브레이커 OPEN → 캐시 사용 + warning | E | P0 |
| SCAN-12 | 취소(queued/running) → 상태 변경 + worker SIGTERM + 작업공간 반환 | N | P0 |
| SCAN-13 | 취소: 전역 스캔 큐에서 취소 | N | P0 |
| SCAN-14 | 취소: 터미널 상태 취소 시도 → 409 scan_already_cancelled | E | P0 |
| SCAN-15 | 취소: 확인 다이얼로그 ✅(다이얼로그 취소 검증완료) | N | P0 |
| SCAN-16 | **브라우저 닫기(스캔 중) → 서버측 계속 진행(고아 아님)** | E | P0 |
| SCAN-17 | zip: 빈 archive 거부 | E | P1 |
| SCAN-18 | 전역 스캔 큐 상태별 탭 필터 | N | P1 |
| SCAN-19 | 디스크 95% 초과 시 스캔 제출 → 503 disk-pressure | E | P0 |
| SCAN-20 | 새 CVE 자동 재탐지(hourly resync) | N | P0 |

## D. 취약점·VEX (vulnerabilities)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| VULN-01 | 목록 렌더 + 컬럼(CVE/심각도/CVSS/EPSS/제목/컴포넌트/상태/발견) | N | P0 |
| VULN-02 | 심각도/상태 필터 + URL 유지 + 새로고침 | N | P0 |
| VULN-03 | 정렬(심각도/CVSS/EPSS/상태/발견시각) | N | P0 |
| VULN-04 | EPSS 필터 경계(0~1, 범위 외 입력) | E | P1 |
| VULN-05 | EPSS 없음 → "—" 표시(null 아님), 정렬 마지막 | N | P1 |
| VULN-06 | EPSS 백분율·percentile 표시 | N | P1 |
| VULN-07 | 검색(CVE ID/제목/컴포넌트) | N | P0 |
| VULN-08 | VEX: New→Analyzing(필수 경유) | N | P0 |
| VULN-09 | VEX: Analyzing→Exploitable/Not affected/False positive/Fixed | N | P0 |
| VULN-10 | VEX: Analyzing→Suppressed(team_admin만) | S | P0 |
| VULN-11 | VEX: New→Suppressed 직접(team_admin만) | S | P0 |
| VULN-12 | VEX: New→Exploitable 직접 시도 → 거부 | E | P0 |
| VULN-13 | VEX: Terminal→Analyzing(Reopen) | N | P0 |
| VULN-14 | developer가 Suppressed 시도 → 403 | S | P0 |
| VULN-15 | 정당화 10자 미만 거부 | E | P0 |
| VULN-16 | 정당화 없이 상태 변경 거부 | E | P0 |
| VULN-17 | 상태 전이마다 감사 로그 + 이력 표시 | N | P0 |
| VULN-18 | 정당화 VEX 내보내기 포함 | N | P1 |
| VULN-19 | PDF 보고서 다운로드(UI/API) | N | P0 |
| VULN-20 | PDF 렌더러 실패 → 500 + problem+json | E | P1 |
| VULN-21 | 빌드 게이트: Critical CVE → fail | N | P0 |
| VULN-22 | 게이트 제외: not_affected/fixed/false_positive | N | P0 |
| VULN-23 | If-Match 동시성: stale version 상태변경 → 412 | E | P1 |

## E. 컴포넌트·라이선스 (components-and-licenses)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| LIC-01 | 컴포넌트 테이블 로드 + 가상 스크롤(1000+) | N | P1 |
| LIC-02 | 검색(name@version 부분일치) | N | P0 |
| LIC-03 | 심각도 다중선택 필터 | N | P0 |
| LIC-04 | 라이선스 카테고리 필터(허용/조건부/금지/미상) | N | P0 |
| LIC-05 | 필터 조합 + URL 업데이트 | N | P1 |
| LIC-06 | 분류: MIT/Apache-2.0/BSD → 허용 | N | P0 |
| LIC-07 | 분류: LGPL/MPL/EPL/CDDL → 조건부 | N | P0 |
| LIC-08 | 분류: AGPL/GPL/SSPL/BUSL → 금지 | N | P0 |
| LIC-09 | 분류: 미매칭 SPDX → 미상 | N | P0 |
| LIC-10 | 분류: 접미사 없는 ID(LGPL-3.0) → 미상(v2.0 정확매칭) | E | P1 |
| LIC-11 | Declared(cdxgen) 표시 + 게이트 평가값 | N | P0 |
| LIC-12 | Detected(scancode) + source_path | N | P0 |
| LIC-13 | Detected 제외 디렉토리(node_modules/vendor/.git/dist 등) | N | P1 |
| LIC-14 | Concluded(레지스트리 폴백) | N | P1 |
| LIC-15 | 선언/감지 충돌 → 양쪽 표시 + provenance 뱃지 | N | P0 |
| LIC-16 | 의무사항 7종 표시 + 분포 | N | P0 |
| LIC-17 | NOTICE 다운로드(text/HTML/markdown) + 저작권 포함 | N | P0 |
| LIC-18 | 빌드 게이트: 금지 라이선스 → fail | N | P0 |

## F. 컴포넌트 승인 (approvals)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| APPR-01 | 조건부 라이선스 감지 → Pending 자동 생성 | N | P0 |
| APPR-02 | Pending→Under Review | N | P0 |
| APPR-03 | Under Review→Approved/Rejected | N | P0 |
| APPR-04 | Pending→Rejected 직접(team_admin만) | S | P0 |
| APPR-05 | 무효 전이 거부 | E | P0 |
| APPR-06 | 전이마다 감사 로그 | N | P0 |
| APPR-07 | Decision note ≤2000자, 초과 거부 | E | P1 |
| APPR-08 | 같은 컴포넌트 여러 프로젝트 → 각각 Pending(자동전파 없음) | N | P0 |
| APPR-09 | Rejected는 감사기록만, 게이트 자동차단 안함(v2.0) | N | P0 |
| APPR-10 | 큐 필터(상태/날짜범위), 기본 Pending+Under Review | N | P0 |
| APPR-11 | developer 승인 권한 경계 | S | P0 |

## G. SBOM (sbom)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| SBOM-01 | CycloneDX JSON/XML 다운로드 | N | P0 |
| SBOM-02 | SPDX JSON/Tag-Value 다운로드 | N | P0 |
| SBOM-03 | 바이트 안정성(재내보내기 sha256 동일) | N | P0 |
| SBOM-04 | API 잘못된 format 값(spdx-tv) → 거부 | E | P1 |
| SBOM-05 | 파일명 sbom-<slug>.<ext> | N | P1 |
| SBOM-06 | 스캔 성공 전 → 빈 components | E | P1 |
| SBOM-07 | 권한 없이 다운로드 → 404(existence-hidden) | S | P0 |
| SBOM-08 | CycloneDX VEX 상태 포함 + 매핑 정확성 | N | P0 |
| SBOM-09 | SPDX VEX 생략 | N | P1 |

## H. 알림 (notifications)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| NOTIF-01 | 채널 토글(In-app/Email/Slack/Teams) + 기본값 | N | P0 |
| NOTIF-02 | 저장(변경 있을 때만 활성) | N | P1 |
| NOTIF-03 | 직접 PUT in_app=false → 422 + Problem | E | P1 |
| NOTIF-04 | 벨 읽지않음 배지(0/1-99/99+) | N | P0 |
| NOTIF-05 | 60초 폴링, 탭 숨김→중단, 포커스→즉시 | N | P1 |
| NOTIF-06 | 벨 클릭 → /notifications | N | P0 |
| NOTIF-07 | 인박스 페이지(20개) + 이전/다음 | N | P0 |
| NOTIF-08 | 읽지않음 볼드, 행 클릭 → 읽음+리소스 이동 | N | P0 |
| NOTIF-09 | "모두 읽음" 대량 작업 | N | P1 |
| NOTIF-10 | 트리거 6종(scan_completed/failed/cve/license/approval/gate) — v2.0 연결상태 확인 | N | P0 |

## I. 통합: API키·웹훅 (integrations + ci/webhooks)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| INTEG-01 | API키 생성: org(super)/team(team-admin)/project(developer 자기팀) | N | P0 |
| INTEG-02 | 키 1회만 표시 + 복사, 닫은 후 prefix만 | S | P0 |
| INTEG-03 | 권한 외 스코프 키 생성 → 403 | S | P0 |
| INTEG-04 | Bearer 인증: 유효→200, 무효→401, 스코프외→403, 취소→401 | S | P0 |
| INTEG-05 | 키 prefix/secret whitespace → 401 | E | P0 |
| INTEG-06 | 키별 레이트리밋 → 429 + Retry-After | E | P1 |
| INTEG-07 | GitHub 웹훅 HMAC-SHA256 검증: 정상→202, 변조→401 | S | P0 |
| INTEG-08 | GitHub push(기본브랜치) → 소스 스캔 트리거 | N | P0 |
| INTEG-09 | GitHub PR opened/synchronize/reopened → 스캔 + PR 코멘트(마커 idempotent) | N | P0 |
| INTEG-10 | GitLab 웹훅 토큰 검증: 정상→204, 변조→401 | S | P0 |
| INTEG-11 | GitLab Push/MR Hook → 스캔(MR 코멘트 v2.0 미구현) | N | P0 |
| INTEG-12 | 비대상 이벤트 → 200 수락, 미트리거(감사 기록) | E | P1 |
| INTEG-13 | 비기본 브랜치 push → 수락, 미트리거 | E | P1 |
| INTEG-14 | 웹훅 idempotency(중복 delivery UUID) → 스캔 1개만 | E | P0 |
| INTEG-15 | 웹훅 시크릿 회전 후 구 시크릿 → 401 | S | P1 |

## J. CI 빌드 게이트 (ci-integration)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| GATE-01 | 양호 → gate=pass, exit 0 | N | P0 |
| GATE-02 | Critical CVE → gate=fail, exit 1 | N | P0 |
| GATE-03 | 금지 라이선스 → gate=fail, exit 1 | N | P0 |
| GATE-04 | CVE+라이선스 동시 → fail | N | P0 |
| GATE-05 | EPSS 게이트(threshold=0.5): EPSS≥0.5 → fail | N | P0 |
| GATE-06 | EPSS<0.5 → pass | N | P1 |
| GATE-07 | EPSS 없음 → 게이트 미적용(pass) | E | P1 |
| GATE-08 | Advisory 모드(fail-on-gate=false) → exit 0, gate=fail 반환 | N | P1 |
| GATE-09 | severity_floor=High 설정 후 High CVE → fail | N | P1 |
| GATE-10 | gate-result API: gate/reason/counts 필드 | N | P0 |
| GATE-11 | GitHub branch protection → merge 차단 / GitLab protected → MR 차단 | N | P1 |
| GATE-12 | Jenkins: gate!=pass → build 실패 | N | P1 |

## K. API 계약 (reference/api-overview)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| API-01 | 만료 JWT → 401 / Missing Auth → 401 | S | P0 |
| API-02 | RFC7807 모든 4xx/5xx(type/title/status/detail/instance) | N | P0 |
| API-03 | 페이지네이션(limit 기본50/max200, offset, sort -field) | N | P1 |
| API-04 | limit>200 → 400 | E | P1 |
| API-05 | well-known problem: last-super-admin 409, disk-pressure 503 | E | P1 |
| API-06 | WebSocket auth 첫 메시지, 타임아웃 1초→close 1008 | E | P1 |
| API-07 | WebSocket 사용자당 최대 연결(3) 초과 → 오래된 것 evict(1001) | E | P2 |
| API-08 | 익명 엔드포인트(health/auth/webhooks)만 인증 면제 | S | P0 |

## L. 관리자: 사용자·팀 (admin/users-and-teams)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| ADM-USR-01 | super-admin 팀 생성/멤버 역할 지정 | N | P0 |
| ADM-USR-02 | team_admin 자기팀 멤버 추가·제거 | N | P0 |
| ADM-USR-03 | 역할 다중팀 가법성 | N | P1 |
| ADM-USR-04 | 사용자 비활성화 → 세션/refresh 회수, 로그인 불가 | S | P0 |
| ADM-USR-05 | **마지막 active super-admin 강등/비활성 → 422 last_super_admin_protected** | S | P0 |
| ADM-USR-06 | self-elevation 차단 | S | P1 |
| ADM-USR-07 | 동시 강등 경쟁 → 한 요청만 성공(FOR UPDATE) | E | P1 |
| ADM-USR-08 | 역할 변경 감사 로그(diff) | N | P0 |
| ADM-USR-09 | developer가 /admin/users 접근 → 404/숨김 ✅(부분 검증) | S | P0 |
| ADM-USR-10 | team_admin이 타팀 멤버 추가 → 403 | S | P0 |

## M. 관리자: API Keys (admin/api-keys)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| ADM-KEY-01 | 키 형식 tos_<prefix>_<secret> | N | P1 |
| ADM-KEY-02 | 회전(새 발급→CI 갱신→구 revoke) | N | P0 |
| ADM-KEY-03 | revoke 후 ~5초 내 거부(캐시 TTL) | E | P1 |
| ADM-KEY-04 | secret mismatch → 401 + structured log | S | P0 |
| ADM-KEY-05 | team scope로 cross-team 호출 → 403 | S | P0 |
| ADM-KEY-06 | 생성/revoke 감사 로그 | N | P0 |

## N. 관리자: DT 커넥터·서킷브레이커 (admin/dt-connector)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| DT-01 | /admin/dt breaker CLOSED + health healthy(60초 내) | N | P0 |
| DT-02 | DT 5회 연속 실패 → breaker OPEN(red) + 캐시 serving | E | P0 |
| DT-03 | OPEN 상태 게이트 호출 → 캐시 사용, DT 미요청 | E | P0 |
| DT-04 | OPEN 30초 후 HALF_OPEN → probe 성공 → CLOSED | N | P0 |
| DT-05 | HALF_OPEN probe 실패 → OPEN 복귀 | E | P0 |
| DT-06 | breaker reset(이미 CLOSED) → 409 dt_breaker_already_closed | E | P1 |
| DT-07 | manual health probe/orphan cleanup | N | P1 |
| DT-08 | DT 1회 실패 → degraded(아직 down 아님) | E | P0 |
| DT-09 | DT 컨테이너 재시작 → self-heal | N | P0 |

## O. 관리자: 감사 로그 (admin/audit-log)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| AUDIT-01 | 인증된 쓰기 작업마다 정확히 1행(GET 제외) | N | P0 |
| AUDIT-02 | 업데이트 시 diff(before/after) 기록 | N | P0 |
| AUDIT-03 | append-only: raw UPDATE → SQLSTATE 23000 에러 | S | P0 |
| AUDIT-04 | append-only: raw DELETE → 에러 | S | P0 |
| AUDIT-05 | append-only: TRUNCATE → 에러 | S | P0 |
| AUDIT-06 | team_admin 자기팀 행만 / super-admin org-wide | S | P0 |
| AUDIT-07 | PII 마스킹(email/password/API key) | S | P0 |
| AUDIT-08 | Celery job actor_user_id=null 기록 | N | P1 |
| AUDIT-09 | CSV export(100k 한계) | N | P0 |
| AUDIT-10 | request_id end-to-end trace | N | P1 |

## P. 관리자: 백업·복원 (admin/backup-and-restore)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| BKP-01 | 수동 백업 트리거 → running→succeeded + 다운로드 | N | P0 |
| BKP-02 | 백업 3파일(postgres.sql.gz/workspace.tar.gz/manifest.json) | N | P0 |
| BKP-03 | 복원: type-to-confirm "restore"(대소문자) 게이트 | S | P0 |
| BKP-04 | 복원: X-Confirm-Restore 헤더 누락 → 412 | S | P0 |
| BKP-05 | non-super-admin /admin/backup → 403/숨김 | S | P0 |
| BKP-06 | 복원 파일 >10GB 거부 | E | P1 |
| BKP-07 | alembic head 불일치 → 경고 + 복구 커맨드 | E | P1 |
| BKP-08 | 복원 후 JWT 전체 revoke | S | P1 |
| BKP-09 | cross-host 복원 → 데이터 count 일치 | N | P0 |
| BKP-10 | 자동 백업(매일 00:00 UTC) + 7일 retention | N | P0 |
| BKP-11 | 백업 3일 연속 실패 → alert | E | P0 |

## Q. 관리자: 디스크·헬스 (admin/disk-and-health)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| DISK-01 | /admin/health 모든 component ok | N | P0 |
| DISK-02 | postgres/redis/celery/dt down → row down(red) | E | P0 |
| DISK-03 | disk 80% warn(yellow) / 90% critical(red)+알림 | E | P0 |
| DISK-04 | disk 95% hard limit → 스캔 제출 503 | E | P0 |
| DISK-05 | in-flight 스캔은 95% 도달해도 계속 | N | P0 |
| DISK-06 | cleanup 후 10초 내 갱신 + 스캔 자동 수락 | N | P0 |
| DISK-07 | health auto-refresh 30초 + pause | N | P1 |
| DISK-08 | /admin/scans force-cancel | N | P0 |

## R. 설치·UAT·온콜 (installation + oncall-runbook)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| INST-01 | fresh host install.sh → containers healthy + schema HEAD + super-admin | N | P0 |
| INST-02 | 첫 로그인 → 팀 생성 → 프로젝트 → 스캔 → WS 진행 | N | P0 |
| INST-03 | docker-compose V1만(V2 금지) | E | P0 |
| INST-04 | cross-host 백업/복원 round-trip | N | P0 |
| ONCALL-01 | DT down → 재시작 → health 복구 | N | P0 |
| ONCALL-02 | stuck 스캔 force-cancel | N | P0 |
| ONCALL-03 | disk 95% → cleanup → 회복 | N | P0 |

## S. 횡단: i18n (전수)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| XCUT-01 | KO 로케일 영어 노출(에러/게이트/Not Found) ✅BUG-002 | X | P1 |
| XCUT-05 | 로그인/회원가입 KO 전수(라벨·버튼·에러) | X | P1 |
| XCUT-06 | 프로젝트 목록·상세 8탭 KO 전수(필터/배지/단계명) | X | P1 |
| XCUT-07 | VEX 다이얼로그 KO(상태명·정당화 placeholder) | X | P1 |
| XCUT-08 | 라이선스 분류 라벨 KO(허용/조건부/금지/미상) | X | P1 |
| XCUT-10 | 날짜/시각 로케일 포맷(스캔/발견 시각) | X | P2 |
| XCUT-11 | 숫자 포맷(EPSS %, 천단위, 파일크기) | X | P2 |
| XCUT-12 | 상대시간 KO("5분 전") | X | P2 |
| XCUT-13 | 언어 설정 영속(새로고침/재로그인) | X | P1 |
| XCUT-14 | 백엔드 problem+json detail 영어 노출 범위(OAuth/422/409) | X | P1 |
| XCUT-15 | 404/500 에러 화면 KO | X | P1 |

## T. 횡단: a11y (전수)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| XCUT-02 | 취약점 상태 배지 색 대비 WCAG AA ✅BUG-001 | X | P1 |
| A11Y-01 | axe: 로그인 화면 위반 0 | X | P1 |
| A11Y-02 | axe: 프로젝트 목록+상세 8탭 | X | P1 |
| A11Y-03 | axe: 스캔 드로어+취소 다이얼로그 | X | P1 |
| A11Y-04 | axe: 취약점 목록+VEX 다이얼로그 | X | P1 |
| A11Y-05 | axe: 컴포넌트/라이선스 테이블+드로어 | X | P1 |
| A11Y-06 | axe: admin 전체(users/teams/dt/scans/disk/audit/health/backup) | X | P1 |
| A11Y-07 | 키보드 전체 네비(사이드바→헤더→메인), focus-visible | X | P1 |
| A11Y-08 | 포커스 트랩(드로어/다이얼로그 내 순환) | X | P1 |
| A11Y-09 | ESC로 모든 드로어/다이얼로그 닫힘 | X | P1 |
| A11Y-10 | ARIA live: 스캔 상태 변경 공지 | X | P1 |
| A11Y-11 | ARIA live: VEX 변경/토스트 공지 | X | P1 |
| A11Y-12 | 모든 폼 input 라벨 연결 | X | P1 |

## U. 횡단: 반응형/모바일
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| RESP-01 | 모바일(320~480px) 주요 화면 렌더(가로스크롤 없음) | X | P1 |
| RESP-02 | 태블릿(768px) 테이블 컬럼 우선순위 | X | P1 |
| RESP-03 | 데스크톱(1024px+) 멀티컬럼 | X | P0 |
| RESP-04 | 모바일 사이드바 드로어 열림/닫힘+자동닫힘 | X | P1 |
| RESP-05 | 모바일 헤더(언어/프로필/로그아웃) 적응 | X | P1 |
| RESP-06 | 테이블 가로 스크롤+스티키 컬럼 | X | P2 |
| RESP-07 | 터치 타깃 최소 크기(44~48px) | X | P1 |
| RESP-08 | 가로 모드 적응 | X | P2 |
| RESP-09 | 초고해상도(1920px+) 가독성 | X | P2 |

## V. 횡단: 보안
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| SEC-01 | CSRF 방어(SameSite + 상태변경 검증) | S | P1 |
| SEC-02 | 세션 고정: 로그인 전후 쿠키 변경 | S | P1 |
| SEC-03 | Clickjacking: X-Frame-Options DENY/SAMEORIGIN | S | P1 |
| SEC-04 | IDOR: 타팀 프로젝트 ID 직접 접근 → 404 | S | P1 |
| SEC-05 | IDOR: 타사용자 프로필 접근 차단 | S | P1 |
| SEC-06 | Open redirect: /login?redirect=//evil.com 차단 | S | P1 |
| SEC-07 | API 키 revoke 후 prefix만, 풀키 재노출 없음 | S | P1 |
| SEC-08 | 5xx에 스택트레이스/쿼리/경로/env 노출 금지 | S | P1 |
| SEC-09 | 권한 상승: developer가 /admin/* 직접 진입 → 차단 | S | P1 |
| SEC-10 | 로그아웃 후 뒤로가기 캐시 차단(no-store) | S | P1 |
| SEC-11 | 감사 로그 PII 평문 저장 금지(마스킹) | S | P1 |

## W. 횡단: 성능/부하
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| XCUT-03 | 중복 API 호출 ✅BUG-003(prod 확인 필요) | X | P2 |
| PERF-01 | 컴포넌트 1000개 가상스크롤 부드러움 | X | P2 |
| PERF-02 | 취약점 500개 필터/검색 응답 <500ms | X | P2 |
| PERF-03 | 가상스크롤 DOM 행 제거 확인 | X | P2 |
| PERF-04 | 동시 스캔 10개 큐 처리 | X | P2 |
| PERF-05 | 느린 네트워크(3G) 로딩 | X | P2 |
| PERF-06 | 네트워크 끊김 후 자동 재시도(backoff) | X | P1 |
| PERF-07 | 탭 반복 열고닫기 메모리 누수 없음 | X | P2 |
| PERF-08 | 필터 변경 시 불필요 리렌더 없음 | X | P2 |

## X. 횡단: 데이터 무결성·동시성(UI)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| DATA-01 | 두 탭 동시 편집 → 자동 갱신 또는 conflict 알림 | X | P1 |
| DATA-02 | optimistic 충돌(If-Match) → 한쪽 실패 알림 | X | P1 |
| DATA-03 | 새로고침 후 탭/필터 상태 일관성 | X | P1 |
| DATA-04 | optimistic 롤백(취소 거부 409 → UI 원복) | X | P1 |
| DATA-05 | 동일 이름 즉시 2번 생성 → 1개만 성공 | X | P1 |
| DATA-06 | 상태 변경 후 캐시 무효화 즉시 리페치 | X | P2 |
| DATA-07 | 배치 부분 실패 → 성공행만 반영+실패 명시 | X | P2 |

## Y. 횡단: 상태·엣지·회복력
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| XCUT-04 | 404 후 breadcrumb 로딩 잔존 ✅BUG-004 | X | P2 |
| EDGE-01 | 빈 상태(스캔 0개) CTA | X | P1 |
| EDGE-02 | 로딩 스켈레톤(깜빡임 없음) | X | P1 |
| EDGE-03 | 에러 바운더리(드로어 렌더 에러 → 흰화면 아님) | X | P1 |
| EDGE-04 | 네트워크 끊김 → "Reconnecting…" | X | P1 |
| EDGE-05 | 재연결 → 상태 동기화 | X | P1 |
| EDGE-06 | 느린 응답(10s) 로딩 표시 | X | P2 |
| EDGE-07 | 부분 실패(탭별 독립 에러) | X | P2 |
| EDGE-08 | 요청 타임아웃(30s) 알림 | X | P2 |

## Z. 횡단: 브라우저 동작
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| BROWSER-01 | 뒤로 버튼(드로어 닫힘→목록) | X | P1 |
| BROWSER-02 | 앞으로 버튼(드로어 재오픈) | X | P1 |
| BROWSER-03 | 새로고침 시 필터 URL 상태 유지 | X | P1 |
| BROWSER-04 | 딥링크 직접 진입(?tab=&severity=) | X | P1 |
| BROWSER-05 | 다중 탭 세션/데이터 동기화 | X | P2 |
| BROWSER-06 | refresh 쿠키 HttpOnly/Secure/SameSite | S | P1 |

## AA. 대시보드 (존재 시)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| DASH-01 | 전사 리스크 포트폴리오 렌더(또는 /projects 리다이렉트 확인) | X | P2 |
| DASH-02 | 권한별 표시(developer 자기팀 / super-admin 전체) | X | P2 |

## AB. 환경변수 동작 (reference/env-variables)
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| ENV-01 | SCANCODE_TIMEOUT_SECONDS=600 타임아웃 동작 | N | P1 |
| ENV-02 | SCANCODE_MAX_FILES=20000 초과 시 감지 스킵 | E | P1 |
| ENV-03 | TRUSTEDOSS_SCAN_BACKEND: prod=real / dev=mock | N | P0 |
| ENV-04 | WEBSOCKET_MAX_CONNECTIONS_PER_USER=3, 4번째 evict | E | P1 |
| ENV-05 | WEBSOCKET_AUTH_TIMEOUT_SECONDS=1 → close 1008 | E | P1 |
| ENV-06 | GATE_EPSS_THRESHOLD 0~1, unset=비활성 | N | P1 |
| ENV-07 | DT_BREAKER_FAILURE_THRESHOLD=5 / COOLDOWN=30s | N | P1 |
| ENV-08 | BACKUP_RETENTION_DAYS=7 자동 pruning | N | P1 |
| ENV-09 | DISK_HARD_LIMIT_PCT=95 하드 블록 | N | P0 |

---

## AC. Pass 3 추가·개선 (최종 전수)
신규 발굴:
| ID | 테스트 케이스 | 종류 | P |
|----|------|----|----|
| DT-15 | DT_AUTO_RESTART=true 시 down → docker restart dtrack-api 자동 | N | P1 |
| VULN-30 | 재스캔 후 심각도 변경 → 업스트림 재점수 추적 | N | P2 |
| LIC-24 | SPDX 복합 표현식(AND/OR) 정규화·분류 | E | P1 |
| PROJ-06b | git@ / ssh:// / file:// URL 각각 거부 | E | P1 |

품질 개선(권고):
- 우선순위 재조정: **AUTH-21 → P0**(refresh 쿠키 보안), **PERF-04 → P1**(동시 스캔 안정성)
- 종류 정정: SCAN-16 E→N(정상 동작), LIC-01 N→X(횡단 성능), API-07 E→N(연결 관리)
- 중복 통합: VULN-21c → VULN-21
- 모호성 명확화: PROJ-16 "스캔 전 중립 뱃지", INTEG-13 "비기본 브랜치 명시적 미트리거", PERF-02 "필터 후 첫 행 렌더 ≤500ms"

---

## 요약 (3-Pass 완료)
- 영역 29개, 최종 케이스 약 **290개**
  - Pass 1: 영역별 1차 ~220개 (user 281 / admin 174 / ci·ref 다수 통합·압축)
  - Pass 2: 횡단·비기능 54개 + 환경변수 9개 + 교차검증
  - Pass 3: 신규 4개 + 품질 개선(우선순위/종류/중복/모호)
- **가이드 대비 커버리지 ~93%**: 관리자·API·env 98~100% / user 88~95% / 횡단(i18n·a11y·반응형) 85~90%
- v2.0 미구현 항목도 "현재 동작 검증" 대상 포함: 알림 트리거 연결(NOTIF-10), GitLab MR 코멘트(GATE-16)
- 검증 완료(✅): PROJ-02/04/15, SCAN-15, XCUT-01~04(BUG-001~004 발견)
- 자동화 완료: fault-injection, 렌더링 XSS, a11y/i18n 대표 spec

## 실집행 현황 (⚠️ 카탈로그 ≠ 실행)
> 본 문서는 **도출된 290 케이스(설계 지도)** 다. 실제 브라우저/스크립트로 **실행한 것은 약 40개(~15%)** — 대표·갭 위주. 카탈로그만 보고 "전부 검증됨"으로 오해 금지. 상세는 `qa-report.md` §3·§9.

- ✅ **실집행·통과**: AUTH-02/04/06 · PROJ-02/04/15 · SCAN-01/02/08/09/12/15 · VULN-08/12 · LIC-06/07/08 · SBOM-01/02 · ADM-USR-09 · BROWSER-01/03/04 · a11y 2화면 · i18n 토글 · k6 부하(W) · DT 장애(N) · node BD 비교
- 🐛 **검증 중 발견**: BUG-001~008 (`bug-report.md`)
- ⬜ **미집행 (~250)**: BD 31 fixture(maven/gradle/python/go/rust 등) · 관리자 L~R 대부분 · i18n/a11y **전수** · 보안 횡단(IDOR/CSRF) · 승인 전이 · P1/P2 다수 → CI scan-matrix/nightly 위임(자산 준비됨)

## 3-Pass 로그
- **Pass 1** ✅: 가이드 영역별 1차 도출 → 카탈로그 종합 (~220)
- **Pass 2** ✅: 횡단 + 환경변수 + 가이드 1:1 교차검증 → +65 (~285)
- **Pass 3** ✅: 최종 전수 — 커버리지 93% 확인, 신규 4 + 품질 개선 → ~290. 잔여 누락은 P1/P2 소수(포스트-GA 보강 권고)
