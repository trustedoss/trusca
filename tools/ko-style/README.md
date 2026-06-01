# ko-style — 한국어 번역투(飜譯套) 린터

TrustedOSS Portal 한국어 문서(`docs-site/i18n/ko/.../current/**/*.md`)가
영어/일본어 직역 티(번역투)를 내지 않도록 점검하는 무의존 Node 린터.
`im-not-ai`(이승현/Liam Lee)의 "10대분류 × 심각도" 접근을 산문 문서에 맞게
**결정적 정규식 규칙**으로 옮긴 것이다. LLM·외부 의존성·API 비용 없음.

## 무엇을 점검하나

- 대상: 한국어 문서 미러(`docs-site/i18n/ko/docusaurus-plugin-content-docs/current/**`)만.
  프론트엔드 UI 문자열(`apps/frontend/src/locales/ko/*.json`)·영어 문서는 범위 밖.
- 규칙은 `patterns.json` 한 곳에 데이터로 모여 있다. 엔진(`lint.mjs`)은 건드리지 않고
  규칙만 추가/수정하면 된다.
- 적용 전, 다음 영역은 **마스킹**되어 규칙이 발동하지 않는다:
  코드 펜스(```), 인라인 코드(`` `…` ``), 마크다운 링크/이미지의 URL,
  맨 URL/오토링크, HTML 주석(`<!-- docs-uat: … -->` 포함), YAML front matter.

## 심각도

| 등급 | 의미 | 훅/CI 차단 |
|------|------|-----------|
| **S1** | 명백한 오류(예: '그리고 나서', '라이센스') | 차단 |
| **S2** | 강한 권고(예: '~에 의해', '~을 가지고 있다') | 차단 |
| **S3** | 약한 권고(맥락에 따라 허용 가능) | 권고만 |

`--fail-on`(기본 `S2`)이 비0 종료를 만드는 최저 등급이다.

## 사용법

```bash
# 전체 한국어 문서 점검 (S3까지 모두 보고)
node tools/ko-style/lint.mjs --all --fail-on S3

# 변경된 문서만 (origin/main 대비; KO_STYLE_DIFF_BASE 로 base 변경)
node tools/ko-style/lint.mjs --changed

# 특정 파일
node tools/ko-style/lint.mjs --files docs-site/i18n/ko/.../current/intro.md

# JSON 출력
node tools/ko-style/lint.mjs --all --format json

# 카탈로그 자가 검증 (example_bad/ok + 마스킹 가드)
node tools/ko-style/selftest.mjs
```

## 어디에 물려 있나

세 갈래로 "앞으로의 문서"에 자동 반영된다 (CI 게이트는 의도적으로 미사용):

1. **Claude 훅** — `.claude/settings.json` 의 `PostToolUse`(Edit|Write|MultiEdit)가
   `hook.mjs` 를 호출. 한국어 문서 `.md` 를 편집하면 그 파일만 즉시 린트해
   S1·S2 발견 시 Claude 에 피드백한다(자가 교정). 다른 파일은 무반응.
2. **슬래시 커맨드** — `/ko-style` 로 변경분 또는 지정 경로를 수동 점검.
3. **에이전트 규칙** — `doc-writer`·`i18n-specialist` 에이전트와 `CLAUDE.md` 공통 DoD가
   "한국어 문서 변경 시 ko-style 통과(S1·S2 0건)"를 요구.

## 규칙 추가하기

`patterns.json` 의 `rules[]` 에 항목을 더한다:

```json
{
  "id": "kebab-id",
  "category": "분류명",
  "severity": "S1|S2|S3",
  "pattern": "정규식(JS, gu 플래그로 컴파일)",
  "message": "무엇이 왜 번역투인지",
  "suggestion": "어떻게 고칠지",
  "example_bad": "규칙이 잡아야 하는 예",
  "example_ok": "잡으면 안 되는 자연스러운 예"
}
```

`example_bad`/`example_ok` 는 `selftest.mjs` 가 회귀로 검증한다. 오탐이 잦은
표층 패턴은 S3으로 두고, 정밀도가 확실한 것만 S1·S2(차단)로 올린다.
용어 일관성 규칙의 정규형은 `docs/glossary.md` 가 단일 진실이다.

## AI 글투(번역투 너머) 처리

`im-not-ai` 방법론의 핵심은 "AI가 쓴 티"를 줄이는 것이다. 표층 정규식으로
정밀하게 잡히는 것만 규칙으로 차단하고, 판단이 필요한 것은 가이드로 남긴다.

**규칙으로 차단(자동):**
- `decorative-emoji` (S2) — 장식 이모지(🎉✨✅ 등). 강조는 Docusaurus
  admonition(`:::note`)으로. ※ `→` 화살표는 제외(내비·상태 표기로 정당).
- `mechanical-step-label` (S3) — '1단계:', 'Step 1:' 같은 기계적 라벨.
  번호 목록(`1.`)이나 task 제목으로 대체.

**가이드만(자동 차단 안 함) — 하우스 스타일과 충돌하므로 판단 영역:**
- **가운뎃점(·) 나열** — 이 레포는 `NVD·OSV·GHSA`, `컴포넌트·라이선스·취약점`
  처럼 `·`를 합성·압축 구분자로 의도적으로 쓴다(디자인 시스템). 단,
  서너 개 이상의 *절*을 `·`로 이어 목록을 흉내 내면 진짜 목록/문장으로 풀 것.
- **화살표(→)** — 내비 경로(`Project Settings → Archive`)와 상태 전이
  (`Pending → Approved`)는 정당. 산문 접속사 대용(`A이고 → B`)으로만 지양.
- **과한 볼드** — 한 문장 전체를 굵게, 한 줄에 강조가 너무 많으면 정리.
  표·용어 첫 등장 강조는 정당.

이 셋은 규칙으로 일괄 제거하면 디자인 시스템·도메인 표기를 깨뜨리므로,
작성/리뷰 단계의 판단(또는 `/ko-style` 리뷰 시 사람·에이전트 확인)에 맡긴다.

## 한계

- 정규식은 표층 패턴만 잡는다. 의미 차원의 어색함(문단 흐름, 논리 비약)은
  사람/에이전트의 판단이 필요하다.
- 기계적 번호매기기·불릿 남발은 기술 문서의 정당한 목록과 구분이 어려워
  자동 규칙에서 제외했다(필요 시 리뷰에서 수동 점검).
- legacy 잔여를 일시 허용하려면 `node tools/ko-style/lint.mjs --all --write-baseline`
  으로 `baseline.json` 을 생성한다(이번 정비로 S3까지 0건이라 현재는 불필요).
