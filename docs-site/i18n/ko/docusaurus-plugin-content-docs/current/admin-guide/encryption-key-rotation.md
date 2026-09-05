---
title: 암호화 키 교체
sidebar_position: 12
---

# 암호화 키 교체

`GITHUB_APP_ENCRYPTION_KEY`는 이 배포가 저장하는 모든 비밀을 암호화합니다. 이름보다
범위가 넓습니다. GitHub App 개인키와 웹훅 시크릿, 프로젝트별 git 자격증명, 사설
레지스트리 비밀번호, 프로젝트 웹훅 시크릿에 걸립니다.

이 변수는 콤마로 구분한 목록을 받습니다. 첫 번째 키가 암호화하고 모든 키가 복호화합니다.
그래서 중단 없이 교체할 수 있고, 같은 이유로 한 단계 일찍 키를 빼면 데이터를 잃습니다.

## 되돌릴 수 없는 것 하나

아직 그 키로 암호화된 행이 남아 있는데 키를 빼면 그 행들은 영구히 읽을 수 없게 됩니다.
키를 다시 찾는 것 말고는 복구 경로가 없습니다.

그리고 그 순간에는 아무 이상이 없어 보입니다. 애플리케이션이 뜨고 행도 다 있습니다.
증상은 나중에 웹훅이 거부되거나 레지스트리 인증이 실패하는 형태로 옵니다.

그래서 아래 절차가 확인으로 끝나고, 그 확인은 선택이 아닙니다.

## 절차

### 1. 새 키를 앞에 넣습니다

<!-- docs-uat: id=key-rotation-generate kind=shell ctx=host tier=manual waiver=prints-a-key-that-must-not-be-recorded -->
```bash
# Generate one
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

앞에 두고 지금 키는 남겨 둡니다.

```
GITHUB_APP_ENCRYPTION_KEY=<new-key>,<current-key>
```

재시작합니다. 이때부터 새로 쓰는 것은 전부 새 키를 쓰고 두 키 모두 읽을 수 있습니다.
깨지는 것이 없고 비밀을 다시 입력할 필요도 없습니다.

### 2. 이미 저장된 것을 다시 암호화합니다

<!-- docs-uat: id=key-rotation-rewrite kind=manual tier=manual -->
```bash
docker-compose -f docker-compose.yml exec backend \
  env MODE=rewrite python scripts/reencrypt_secrets.py
```

중단해도 되고 다시 돌려도 됩니다. 이미 최신 키인 행은 건너뛰므로 두 번째 실행은
처음부터가 아니라 이어서 갑니다.

작업 중에 누가 바꾼 행은 "changed under us"로 보고하고 그대로 둡니다. 그것이 맞습니다.
애플리케이션이 쓴 값은 이미 최신 키이고, 앞서 읽은 값으로 다시 쓰면 그 사람이 교체한
비밀을 되돌리는 셈입니다.

### 3. 남은 것이 없는지 확인합니다

<!-- docs-uat: id=key-rotation-count kind=manual tier=manual -->
```bash
docker-compose -f docker-compose.yml exec backend \
  env MODE=count python scripts/reencrypt_secrets.py
```

`Nothing is on an older key`가 나와야 다음으로 갑니다. 아니면 0이 아닌 코드로 끝나므로
스크립트에서 관문으로 쓸 수 있습니다.

같은 수가 백엔드 기동 로그의 `key_rotation.stale_at_boot`에도 나옵니다. 키가 둘 이상일
때만입니다. 재암호화를 돌린 사람과 환경변수를 고치는 사람이 다를 수 있고, 명령 출력은
앞사람의 터미널에만 있었습니다.

### 4. 옛 키를 뺍니다

```
GITHUB_APP_ENCRYPTION_KEY=<new-key>
```

재시작합니다.

## 옛 키를 언제까지 두어야 하나

교체 이전 시점의 백업을 보관하는 기간만큼입니다.

백업에는 그것을 뜰 때 쓰던 키로 암호화된 값이 들어 있습니다. 복원하면 그 암호문이 돌아오고,
목록에 없는 키로는 열 수 없습니다. `BACKUP_RETENTION_DAYS`가 90이면 옛 키도 교체 뒤 90일
동안 필요합니다. 4단계가 끝나는 시점까지가 아닙니다.

두는 비용은 없습니다. 첫 번째가 아닌 키는 아무것도 암호화하지 않습니다.

`scripts/restore.sh`가 복원 뒤에 위 확인을 돌리고, 복원한 데이터가 이 배포에 없는 키를
요구하면 그 자리에서 알려 줍니다.

## 어떤 키로도 열리지 않는 행이 있을 때

두 명령 중 하나가 "could not be opened by any configured key"를 보고하면 쓰이던 키가
이미 빠진 것입니다. 재암호화를 돌려도 소용없습니다. 빠진 키를 목록에 되돌려야 합니다.

보고에 컬럼과 행 식별자가 나오므로 무엇이 영향을 받는지 알 수 있습니다. 키가 돌아올
때까지 그 비밀들은 읽을 수 없고, 그것을 쓰는 기능이 실패합니다. 웹훅 전송이 거부되고,
사설 레지스트리 인증이 실패하고, GitHub App이 서명하지 못합니다.

## 암호화 컬럼을 새로 추가할 때

`apps/backend/core/encrypted_columns.py`가 암호문을 담는 컬럼과 그것을 여는 키를 전부
적습니다. 교체는 그 목록을 훑으므로, 목록에 없는 컬럼은 교체가 건너뛰고 그 행들은 운영자가
곧 빼려는 키에 남습니다.

목록과 코드가 어긋나거나, 재암호화 마이그레이션 없이 컬럼의 키가 바뀌면 계약 테스트가
실패합니다.
