# 일반 UI workflow의 실제 Docker 검증

2026-09-30 14:43:02–14:50:07 KST, `D:\AWS App Packager`. 최종 결과는 두 자체 샘플 모두 **PASS**다.

## 실행 경로와 네트워크

`devtools/ui_workflow_integration.py`에서 일반 UI 버튼이 호출하는 `service.start_build()`를 호출했다. 동일한 JobManager, 승인 기록, 소스 snapshot, 이미지 빌드, 공통 `runtime_check.run_check()`를 사용했다. 기존 이미지 artifact를 넘겨 재시험한 결과가 아니라, 두 샘플 모두 실제 빌드 명령을 새로 실행했다. Docker/Paketo의 정상적인 빌드 캐시는 사용될 수 있다. mock runner나 AppTest 성공을 실제 결과로 사용하지 않았다.

소스 분석은 읽기 전용이다. 빌드 및 로컬 시험 승인 후 `approved_project_bridge` 실행 조건·소스·빌드 승인·실행 job 지문을 기록한다. 재시험은 별도의 실행 job과 승인 기록을 사용한다. 기존 자체 샘플의 공통 normal bridge 구현을 재사용했다. 새 네트워크 구현을 중복 추가하지 않았다.

실행 job마다 user-defined normal bridge를 만들고 실제 `Driver=bridge`, `Internal=false`를 확인한다. 컨테이너 8080은 `127.0.0.1::8080`으로만 게시한다. Docker가 정한 host port를 시작과 재시작 후 각각 `docker inspect`와 `docker port`로 다시 읽고, 실제 mapping이 127.0.0.1 하나이며 두 명령 결과가 일치할 때만 HTTP를 요청한다. 다른 포트의 추가 host mapping도 거부한다. 이 bridge는 외부 송신이 가능하며 승인 화면에서 고지한다.

## 실제 결과

| 확인 항목 | Java / Spring Boot / Paketo | Python / Dockerfile |
|---|---|---|
| 새 이미지 빌드 및 inspect | BUILT / PASS | BUILT / PASS |
| 실행 job | `b10060d30f7a4e3d8b65670e7cf8086c` | `c0af864b6539493c94b3ff1702721ad0` |
| 실행 조건 | 중형, Linux/amd64, 8080, `/health` | 중형, Linux/amd64, 8080, `/health` |
| 컨테이너 시작 | PASS | PASS |
| 최초 `docker port … 8080/tcp` | `127.0.0.1:24992` | `127.0.0.1:30924` |
| 최초 inspect mapping | `8080/tcp → 127.0.0.1:24992`, 하나 | `8080/tcp → 127.0.0.1:30924`, 하나 |
| 최초 HTTP | 200 + `packager-spring-v1` 확인 | 200 + `packager-python-v1` 확인 |
| `docker restart` | PASS | PASS |
| 재시작 후 `docker port … 8080/tcp` | `127.0.0.1:25000` | `127.0.0.1:30932` |
| 재시작 후 inspect mapping | `8080/tcp → 127.0.0.1:25000`, 하나 | `8080/tcp → 127.0.0.1:30932`, 하나 |
| 재시작 후 HTTP | 새 port에서 200 + marker 확인 | 새 port에서 200 + marker 확인 |
| 정리 | CLEANED | CLEANED |
| 정리 후 독립 확인 | 해당 container/network ID inspect: 없음 | 해당 container/network ID inspect: 없음 |
| 최종 분류 | PASS | PASS |

실제 image ID:

- Java: `sha256:62a758719af208b29138edfbe935fc6212f5d4e7865c654dc2500f68892db9e6`
- Python: `sha256:a67dc6caab9cbac799e3d980ad37db2c3ebce1e3bd6ba08e07f52467c05a1392`

Java는 약 6분 54초, Python은 약 11초가 걸렸다. Java 빌드 중 pack의 containerd 성능 저하 경고가 관찰됐으며 시스템 설정 변경 없이 완료됐다. 위 port는 정리된 시험 컨테이너의 기록이며 현재 접속 주소가 아니다.

원본 기록: [실제 실행 JSON](../.work/integration/ui-workflow-7333baaae387421c90fb0510fbedf5b2.json). 보관 사본: [ui_workflow_validation.json](ui_workflow_validation.json).

각 `.work/jobs/<실행 job>/`의 `runtime-approval.json`, `build-context-manifest.json`, `build-result.json`, `image.json`, `runtime-resources.json`, runtime 결과와 상위 `<job>.json`에 승인·빌드·실행 근거를 보관한다. 컨테이너/네트워크만 선택 정리했고 이미지·build cache·기록은 보존했다.

## UI와 실패 구분

화면 상단에는 구체적인 결과 분류와 단계별 상태를 표시한다. 이미지 만들기, 컨테이너 시작, 로컬 포트 연결, 최초 HTTP, 컨테이너 재시작, 재시작 후 포트 연결, 재시작 후 HTTP, 시험 자원 정리의 8개 행이다. 완료는 `✓ 완료`, 실패는 `✗ 실패`, mapping 차단은 `✗ 환경 차단`, 실행하지 않은 후속 검사는 `미실행`이다. 성공한 실제 image ID는 펼침 영역 밖에 표시하며 후속 시험이 실패해도 보존한다.

요청한 `IMAGE_BUILD_FAILED`, `CONTAINER_START_FAILED`, `ENVIRONMENT_BLOCKED_PORT_MAPPING`, `INITIAL_HTTP_FAILED`, `RESTART_HTTP_FAILED`, `PASS`를 구분한다. 승인/입력 차단, 취소, 오래된 결과, 정리 실패는 별도 상태로 보존한다. mapping 차단을 이미지 빌드 실패로 바꾸지 않는다. 기존 단계 완료 기반 % 막대도 유지한다. 시간 경과를 가짜 진행률로 환산하지 않는다.

실제 두 job의 저장 기록을 제품의 `_progress_details()` renderer로 읽는 임시 loopback 화면에서 브라우저 표시를 확인했다. 두 샘플 모두 8행 `✓ 완료`, 100%, 실제 image ID가 표시됐다. 이 확인 화면은 읽기 전용이며 브라우저에서 실행 버튼을 눌러 시험했다고 주장하지 않는다. 실제 실행은 위 동일 서비스 workflow가 수행했다. 실패 장면과 승인 버튼 동작은 별도 명시적 AppTest/stub 검증이다.

## 회귀와 수정 이력

- `.venv\Scripts\python.exe -m pytest`: **480 passed, 1 skipped**. 단위시험 450개와 Streamlit AppTest 30개. [JUnit](../.work/validation/approved-ui-pytest.xml).
- `.venv\Scripts\python.exe -m ruff check .`: **PASS**.
- 기본 pytest의 opt-in Docker test 1개는 SKIP이며, 위 별도 실제 통합시험 두 건은 PASS다.
- 첫 실제 시도는 승인 기록을 새로 생성할 때 존재하지 않는 파일까지 읽기용 경로 검사로 확인한 오류 때문에 두 샘플 모두 빌드 전에 BLOCKED였다. 존재하는 상위 경로와 링크를 검사한 뒤 exclusive 생성하도록 수정했다. 이 실패를 [이전 시도 JSON](../.work/integration/ui-workflow-1704a4926c4b4587a2424173c4623d5a.json)에 보존하고 위 최종 시도로 다시 확인했다.

재실행 명령(자체 두 샘플 실제 빌드/실행):

```powershell
Set-Location 'D:\AWS App Packager'
$env:RUN_LOCAL_CONTAINER_TESTS = '1'
.venv\Scripts\python.exe devtools\ui_workflow_integration.py
```

현재 켜 둔 제품 서버에는 이전 코드가 남을 수 있다. 그 서버의 터미널에서 Ctrl+C로 종료하고 `run.cmd`를 다시 실행한다. 기존 브라우저의 표시 주소 대신 새 실행 시 안내하는 loopback 주소를 사용한다.

## 이번에 실행하지 않은 항목

AWS 인증/API/CLI, 이미지 push, Terraform 명령, daemon/방화벽/버전 변경, prune, 임의 외부 프로젝트 실행은 하지 않았다. 기존 성공 ZIP과 이전 검증 결과도 수정하지 않았다. 이번에는 중형 두 샘플의 제품 workflow를 확인했으며 소형/대형 재시험·bundle 재생성·tar save/load는 재실행하지 않았다. 실제 사용자 업무 앱, DB/영구 데이터, AWS 배포·권한·부하·운영 보안은 이 자체 샘플 결과로 검증됐다고 주장하지 않는다.
