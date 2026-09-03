---
id: logs
title: 로그
description: TRUSCA가 로그에 무엇을 남기는지, 한 사건을 컨테이너 여러 개에 걸쳐 어떻게 읽는지, 중앙으로 어떻게 보내는지.
sidebar_label: 로그
sidebar_position: 3.5
---

# 로그

모든 컨테이너가 stdout에 JSON을 씁니다. 한 줄에 객체 하나입니다. 컨테이너 안의 파일로는
아무것도 쓰지 않으므로, 로그 수집은 볼륨을 붙여 파일을 따라가는 일이 아니라 Docker의 로깅을
쓸 만한 곳으로 향하게 하는 일입니다.

## 한 줄의 모습

```json
{"event": "scancode_stage_done", "level": "info", "timestamp": "2026-09-03T04:12:07.918431Z",
 "request_id": "0199...", "task_name": "trustedoss.scan_source",
 "task_id": "b2c8...", "scan_id": "7f31..."}
```

`event`는 문장이 아니라 식별자입니다. 걸러내기 위한 값이고(`event="scancode_stage_done"`),
그래서 정작 중요한 값들은 메시지에 섞이지 않고 각자 필드로 있습니다.

무언가를 찾을 때 쓰는 필드는 넷입니다.

| 필드 | 출처 |
|---|---|
| `request_id` | `X-Request-ID` 헤더, 또는 미들웨어가 만든 UUID. 그 요청이 남기는 모든 줄에 있고, 그 요청이 보낸 작업이 남기는 줄에도 있습니다 |
| `user_id`, `team_id` | 인증 미들웨어가 넣습니다. 비인증 호출에는 없습니다 |
| `task_name`, `task_id` | worker에서 모든 task에 바인드됩니다 |

task가 자기 값을 더하기도 합니다. 스캔 파이프라인의 `scan_id`, 스윕의 `dry_run` 같은 것들입니다.

## 한 사건을 컨테이너에 걸쳐 읽기

스캔은 백엔드가 받은 HTTP 요청으로 시작해 몇 분 뒤 worker 안에서 끝납니다. 그 두 조각을
잇는 것이 `request_id`입니다. 작업을 보낼 때 메시지에 실리고 반대편에서 다시 바인드됩니다.

```bash
# 요청 하나가 일으킨 모든 것을 백엔드와 worker에서 함께, 오래된 순으로.
REQ=0199abcd-...
docker-compose logs --no-color --timestamps backend worker-scan worker-default \
  | grep "\"request_id\": \"$REQ\"" \
  | sort -k1,1
```

beat가 보내는 작업은 뒤에 요청이 없어 `request_id`를 갖지 않습니다. 이 부재는 그 자체로 뜻이
있어 임의의 값으로 채우지 않으므로, 그런 작업은 `task_name`으로 거릅니다.

```bash
docker-compose logs --no-color beat worker-default \
  | grep '"task_name": "trustedoss.kev_catalog_refresh"'
```

"이 작업이 요즘 어땠는가"는 로그보다 관리자 화면의 작업 실행 이력이 더 잘 답합니다. 실행마다
한 행이 결과와 소요 시간과 함께 남고, 로그가 회전돼도 사라지지 않습니다.
[디스크와 상태](disk-and-health.md)를 참고하십시오.

## 레벨

`LOG_LEVEL`(기본 `INFO`)은 백엔드, worker, beat에 적용됩니다. `TRAEFIK_LOG_LEVEL`은 별개이며
프록시에만 해당합니다.

`INFO`는 요청마다, 그리고 작업 상태가 바뀔 때마다 한 줄을 남깁니다. 위의 결합이 기대는 것이
이 줄들입니다. `DEBUG`는 애플리케이션 아래 라이브러리들이 그 레벨에서 내는 것까지 더하는데,
크기를 제한한 로그 링을 금방 채울 만큼 많습니다. 진단할 때만 올리고 끝나면 다시 내리십시오.

둘 다 컨테이너가 뜰 때 읽으므로, 바꾸려면 해당 서비스를 다시 시작해야 합니다.

## 로그에 없는 것

비밀번호, 토큰, API 키, 이메일 주소는 평문으로 나오지 않습니다. 값이 마스킹 헬퍼를 거치면서
민감한 하위 항목이 `***`로 바뀌고, 접속 문자열에 섞인 자격증명은 오류를 기록하기 전에
제거됩니다.

이것은 바닥이지 여러분이 더할 것까지 보장하지는 않습니다. 앞으로 어떤 연동이 알려지지 않은
이름으로 값을 기록하면 저절로 가려지지 않습니다. 로그를 호스트 밖으로 보낸다면 누가 언제
무엇을 했는지가 담긴 운영 데이터로 취급하고, 그에 맞는 보존 기간과 접근 통제를 두십시오.

## 어딘가로 보내기

Docker의 기본 `json-file` 드라이버는 로그를 호스트에 남기는데, 이 저장소의 compose 파일에는
크기 제한이 설정돼 있지 않습니다. 바쁜 배포에서는 디스크가 감당하지 못할 때까지 자라고,
디스크가 차면 스캔이 멈춥니다. 할 일이 둘이고 순서가 있습니다.

먼저 호스트에 남는 양을 제한하십시오. 배포된 파일을 고치는 대신 여러분의 compose 오버라이드에
`logging:` 블록을 더하면 업그레이드가 그것을 되돌리지 않습니다.

```yaml
services:
  backend:
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

`worker-scan`, `worker-default`, `beat`, `traefik`에도 같이 넣습니다. 양이 많은 쪽은
worker입니다.

그다음, 보낼 중앙 수집처가 있다면 드라이버를 그쪽으로 바꾸십시오. 애플리케이션이 stdout에만
쓰기 때문에 Docker의 어떤 로깅 드라이버든 씁니다. `syslog`, `gelf`, `awslogs`, `fluentd`,
또는 컨테이너 소켓을 읽는 Vector나 Promtail 같은 수집기입니다. 실려 나가는 것이 이미 JSON이므로
수집기가 그 줄을 메시지 문자열로 다루지 말고 JSON으로 파싱하게 설정하십시오. 그러지 않으면 위의
필드들이 질의에서 보이지 않습니다.

:::caution 원격 드라이버가 컨테이너를 멈출 수 있습니다
`fluentd`, `gelf`, `syslog` 드라이버는 목적지에 닿지 못할 때 드라이버 모드에 따라 컨테이너를
멈추게 할 수 있습니다. 로그 목적지가 배포와 같은 장애 범위에 있지 않다면 `mode: non-blocking`을
(`max-buffer-size`와 함께) 설정해서, 수집기 장애가 스캐너까지 멈추지 않게 하십시오.
:::

## 함께 보기

- [디스크와 상태](disk-and-health.md)가 지표 엔드포인트와 작업 실행 이력을 다룹니다.
  "돌고 있는가"에는 로그보다 그쪽이 잘 답합니다.
- 필드 목록과 binder 패턴은 기여자 가이드의 코딩 표준에 개발자용으로 정리돼 있습니다.
