# scan-bench — cdxgen/Trivy 검출력 검증 도구

baseline-scan fixture 32개(A: 회귀 매트릭스) + real-world 3개(B: 벤치마크)를 portal에 일괄
등록·스캔하고 결과를 CSV/markdown으로 모은다. `docs/scans/`에 보고서 산출.

## 사전 조건
- portal dev stack 기동 (`docker-compose -f docker-compose.dev.yml up`)
- `frontend-admin@demo.trustedoss.dev` / `DemoTest2026!` 계정 활성

## 사용
```bash
cd scripts/scan-bench

# A — fixture 32개
python3 run_bench.py --suite fixtures

# B — real-world (Juice Shop + WebGoat + 자체 v1 셀프스캔)
python3 run_bench.py --suite realworld

# C — container (실물 공개 이미지, worker가 pull — alpine:3.19)
#     합성 픽스처가 못 잡는 다중-CVE 밀도(H-1류) 검출력 기준선.
python3 run_bench.py --suite container

# 단일 프로젝트만
python3 run_bench.py --suite fixtures --only node
```

산출: `out/<suite>-<timestamp>.{csv,md,jsonl}` (회차마다 새 파일, 비교는 안 됨)

## 결과 창고 (회차 간 비교)

`out/`의 CSV는 회차마다 새 파일이라 이전 회차와 비교하거나 추세를 볼 수 없다. 매 실행은
자동으로 SQLite 창고(`warehouse.db`, 기본 위치는 이 디렉터리)에도 결과를 남기고, 직전 회차와의
차이를 요약해 출력한다. 포털의 Postgres를 쓰지 않는 이유는 계획 문서
(`~/projects/trusca-internal/docs/self-resource-validation-plan-2026-08-30.md` §6-1)에
적어 뒀다: 포털의 findings 보존 정책이 7일 뒤 지우므로 그대로는 못 쓴다.

```bash
# 회차 이력
python3 warehouse_report.py history --suite fixtures

# 최신 회차 vs 직전 회차 (상태 변화·수치 델타·추가/탈락 대상)
python3 warehouse_report.py compare --suite fixtures

# 특정 두 회차 비교
python3 warehouse_report.py compare --suite fixtures --run-a 3 --run-b 7
```

창고 위치는 `SCAN_BENCH_WAREHOUSE_DB` 환경변수 또는 `--warehouse-db`로 바꿀 수 있다. 코호트
러너처럼 이 저장소 밖 영구 디스크에 두고 싶을 때 쓴다. 빈 문자열을 주면 창고 기록을 건너뛴다.

## 대량 등록 (S3 코호트 준비, §6-2)

`run_bench.py`는 소수의 고정된 fixture/real-world 대상을 직렬로 도는 도구다. S3(120개
팀 × 저장소 15개 안팎, 약 1,800 스캔) 같은 대량 등록에는 다른 도구 `bulk_register.py`를
쓴다. zip 압축·업로드가 아예 없다 - 프로젝트를 실제 `git_url`로 등록하고 `{"kind":
"source"}`만 보내면 워커가 기본값(`source_type=git`)으로 직접 clone하므로 로컬 디스크가
전혀 필요 없다. super-admin 계정이 팀 멤버십 없이도 임의 팀 아래 프로젝트를 만들 수
있다는 점을 그대로 쓴다.

대상 목록은 JSON 파일로 준다:
```json
{
  "teams": [
    {
      "name": "example-org",
      "slug": "example-org",
      "repos": [
        {"name": "example-repo", "slug": "example-repo", "git_url": "https://github.com/example/example-repo.git"}
      ]
    }
  ]
}
```

```bash
# 팀·프로젝트 생성 + 스캔 트리거 (중단됐다면 재실행 시 이미 만든 건 건너뛴다)
python3 bulk_register.py register --cohort github-2026-09 --input targets.json \
    --admin-email admin@example.com --admin-password '...'

# 실패한 것만 다시 시도
python3 bulk_register.py register --cohort github-2026-09 --input targets.json --retry-failed \
    --admin-email admin@example.com --admin-password '...'

# 진행 중인 스캔 상태 갱신 (한 번, 또는 --watch로 전부 끝날 때까지)
python3 bulk_register.py poll --cohort github-2026-09 --admin-email ... --admin-password ...

# 요약 (포털 접속 불필요, 등록 상태만 읽음)
python3 bulk_register.py status --cohort github-2026-09
```

등록·스캔 상태는 이 디렉터리의 `cohort.db`(SQLite, `COHORT_DB`/`--cohort-db`로 변경 가능)에
쌓인다. `register`/`poll` 둘 다 대상 하나가 실패해도 나머지를 계속 처리하고 실패 사유를
기록하므로, 1,800개 규모에서 한 건의 예외가 전체를 멈추지 않는다.

## 동작 (run_bench.py, fixture/real-world 소수 대상)
1. 로그인 → access_token + refresh cookie 보관 (30분 만료 자동 갱신)
2. 입력 디렉토리 zip 압축 (`node_modules/`, `.git/`, `target/`, `build/`, `.gradle/`, `venv/` 제외)
3. POST /v1/projects → 프로젝트 생성 (slug 충돌 시 재사용)
4. POST /v1/projects/{id}/source-archive → archive_id 수령
5. POST /v1/projects/{id}/scans → scan_id 수령 (kind=source, source_type=upload)
6. GET /v1/scans/{scan_id} 5초 폴링 → succeeded/failed/cancelled까지
7. GET /v1/projects/{id}/{overview,components,vulnerabilities,licenses} 집계

## 동시성
worker가 1개이므로 직렬 실행이 기본. concurrency cap=10/team, rate limit 20/min/user.
