# 자체 샘플 normal bridge 실제 검증 — 2026-09-30

최종 결과 **PASS**. Java/Python의 초기 중형 시험 2개를 통과한 뒤 동일 이미지의 소형·중형·대형 시험 6개를 모두 통과하고 성공 bundle 6개를 생성했다. 모든 시험에서 최초 HTTP 200과 기대 marker, 재시작 후 HTTP 200과 기대 marker, 정리 CLEANED를 실제 확인했다. mock/AppTest 결과가 아니다.

## 범위와 원인

이 환경의 internal-only bridge에서는 실제 host mapping이 없었다. 사용자 승인에 따라 검토된 두 자체 샘플에만 작업별 user-defined **normal bridge**를 사용했다. network inspect 결과 driver=bridge, Internal=false이며 `--publish 127.0.0.1::8080`을 사용했다. 시작·재시작마다 Docker inspect와 docker port가 같은 단일 127.0.0.1 mapping인지 먼저 확인한 뒤 HTTP를 요청했다. 0.0.0.0·LAN 공개, daemon/방화벽 변경, downgrade, reset/prune은 하지 않았다.

일반 bridge는 외부 송신을 허용한다. 일반 UI/외부 프로젝트는 이 예외를 사용하지 않는다. 소스 지문·빌드 성공 기록·image ID·명시적 opt-in을 확인하는 guard를 적용했다. 입력 소스 또는 조건이 달라지면 차단한다.

첫 normal bridge 재시험에서 추가로 두 문제를 발견했다. Docker가 restart 때 임시 host port를 다시 할당했으므로 새 포트를 교차 확인하여 같은 컨테이너의 같은 `/health`를 요청하도록 수정했다. Python은 실제 HTTP 200을 반환했지만 시험기가 Content-Length만큼 읽은 후 닫힌 socket을 다시 조작하던 오류가 있어, 응답 완료/EOF 처리와 잘린 본문 거부를 수정했다. 이 첫 실패에서도 자원은 CLEANED였고 추가 사양 시험·bundle은 실행하지 않았다.

공식 근거: [Docker 포트 게시와 loopback 바인딩](https://docs.docker.com/engine/network/port-publishing/). 특정 Docker 버전 전체의 결함이라고 일반화하지 않으며, 이 호스트에서 관찰한 mapping과 HTTP 결과를 기록한다.

## 이미지

이번에는 이전에 실제 빌드한 이미지와 소스 지문을 검증해 재사용했다. 새 빌드를 수행한 것으로 기록하지 않는다. 각 샘플의 모든 크기 시험에서 같은 image ID를 유지했다.

| 샘플 | 빌드 경로 | 실제 image ID |
|---|---|---|
| spring-http | Java 21 / Paketo | `sha256:3bcfb78df19a983fdd12603d97a5c867dd3b6e77b8b5bffb90f32cc1df30d55e` |
| docker-http | Python / Dockerfile | `sha256:bfe62b4dc83c6a3d7b81ddd0c2a7e21fc488f8cd436e68bcec8c20b9cf2714b9` |

## 실제 docker port 출력과 HTTP

아래는 `docker container port <해당 container ID> 8080/tcp`의 출력이다. 각 출력은 같은 시점의 inspect 결과와 일치했다. 모든 컨테이너·네트워크는 시험 후 제거했으므로 이 임시 주소가 지금도 실행 중이라는 뜻은 아니다.

| 순서 | 샘플 / 크기 | 시작 시 docker port | 재시작 후 docker port | 최초 / 재시작 HTTP | 정리 |
|---|---|---|---|---|---|
| 초기 | Java 중형 | `127.0.0.1:29114` | `127.0.0.1:29121` | 200 / 200 | CLEANED |
| 초기 | Python 중형 | `127.0.0.1:29129` | `127.0.0.1:29132` | 200 / 200 | CLEANED |
| 크기 재시험 | Java 소형 | `127.0.0.1:29135` | `127.0.0.1:29147` | 200 / 200 | CLEANED |
| 크기 재시험 | Java 중형 | `127.0.0.1:38614` | `127.0.0.1:58121` | 200 / 200 | CLEANED |
| 크기 재시험 | Java 대형 | `127.0.0.1:58127` | `127.0.0.1:48705` | 200 / 200 | CLEANED |
| 크기 재시험 | Python 소형 | `127.0.0.1:48712` | `127.0.0.1:48716` | 200 / 200 | CLEANED |
| 크기 재시험 | Python 중형 | `127.0.0.1:15574` | `127.0.0.1:15577` | 200 / 200 | CLEANED |
| 크기 재시험 | Python 대형 | `127.0.0.1:15580` | `127.0.0.1:15585` | 200 / 200 | CLEANED |

소형 0.5 vCPU/1024 MiB, 중형 1 vCPU/2048 MiB, 대형 2 vCPU/4096 MiB. non-root, read-only root, /tmp tmpfs, PID·로그·capability 제한을 유지했다. 소유 label과 ID를 확인한 해당 container/network만 삭제하고 삭제 후 부재를 확인했다. 이미지·공유 volume·사용자 network를 삭제하지 않았다.

## 성공 bundle

각 ZIP과 같은 이름의 디렉터리가 `D:\AWS App Packager\exports`에 있다. Terraform 2 root, AWS_SPEC, 수동 runbook, 실제 local-test-result, manifest와 image.json을 포함한다. 이미지 tar는 이번 실행에서 생성하지 않았다.

| 샘플 / 크기 | 성공 ZIP |
|---|---|
| Java 소형 | [spring small](../exports/spring-http-1cfcb09e414d45fbb92fffef51e8047a-01eedb6f.zip) |
| Java 중형 | [spring medium](../exports/spring-http-1cfcb09e414d45fbb92fffef51e8047a-fe353915.zip) |
| Java 대형 | [spring large](../exports/spring-http-1cfcb09e414d45fbb92fffef51e8047a-14301fde.zip) |
| Python 소형 | [docker small](../exports/docker-http-f8cffc9a37c44d51aee1ea4c035dfe88-8092ede9.zip) |
| Python 중형 | [docker medium](../exports/docker-http-f8cffc9a37c44d51aee1ea4c035dfe88-140fb808.zip) |
| Python 대형 | [docker large](../exports/docker-http-f8cffc9a37c44d51aee1ea4c035dfe88-ed8deef6.zip) |

6개 ZIP의 CRC·포함 파일 bytes, manifest의 파일 SHA-256, image ID, preset, 실제 시험조건 fingerprint 일치를 별도 확인했다. Terraform은 생성·정적 검사만 했으며 이번 bundle의 Terraform CLI 검증과 AWS 적용은 NOT_RUN/AWS_NOT_TESTED다. ECR digest·인증서·도메인·학교 계정 입력은 실제 사용자가 채워야 한다.

## 근거와 재실행

- 최종 실제 통합: `.work/integration/result-63e3c3a6322647f880f9d49e5e91693c.json`
- 첫 normal bridge 실패·정리 보존: `.work/integration/result-23dd82c803004ae3b163bbf03c738be5.json`
- 이전 실제 빌드 근거: `.work/integration/result-7fe3d3f2356940859de50f2394d03b2e.json`
- 결과물 검증: `.work/validation/network-output-verification.json`
- 기본 회귀: `.work/validation/network-pytest.xml` — **398 PASS, 1 SKIP**, Ruff PASS. 기본 명령의 opt-in Docker 시험 1개만 SKIP이며 위 실제 실행과 구분한다. 이 중 AppTest 24개와 합성 runner는 실제 Docker 성공을 대신하지 않는다.

```powershell
$env:RUN_LOCAL_CONTAINER_TESTS = '1'
.\.venv\Scripts\python.exe devtools\local_integration.py --reuse-builds-from .work\integration\result-7fe3d3f2356940859de50f2394d03b2e.json
```

실제 통합은 현재 앱의 OS 작업 잠금을 공유한다. 다른 작업이 실행 중이면 중복 실행하지 않는다. mapping이 없으면 ENVIRONMENT_BLOCKED/port_mapping, 응답 실패는 FAIL/initial_http 또는 restart_http로 구분하며 해당 단계에서 성공 export를 차단한다.
