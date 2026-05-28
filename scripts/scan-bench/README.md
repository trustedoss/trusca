# scan-bench — cdxgen/Trivy 검출력 검증 도구

bd-scan fixture 32개(A: 회귀 매트릭스) + real-world 3개(B: 벤치마크)를 portal에 일괄
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

# 단일 프로젝트만
python3 run_bench.py --suite fixtures --only node
```

산출: `out/<suite>-<timestamp>.{csv,md,jsonl}`

## 동작
1. 로그인 → access_token + refresh cookie 보관 (30분 만료 자동 갱신)
2. 입력 디렉토리 zip 압축 (`node_modules/`, `.git/`, `target/`, `build/`, `.gradle/`, `venv/` 제외)
3. POST /v1/projects → 프로젝트 생성 (slug 충돌 시 재사용)
4. POST /v1/projects/{id}/source-archive → archive_id 수령
5. POST /v1/projects/{id}/scans → scan_id 수령 (kind=source, source_type=upload)
6. GET /v1/scans/{scan_id} 5초 폴링 → succeeded/failed/cancelled까지
7. GET /v1/projects/{id}/{overview,components,vulnerabilities,licenses} 집계

## 동시성
worker가 1개이므로 직렬 실행이 기본. concurrency cap=10/team, rate limit 20/min/user.
