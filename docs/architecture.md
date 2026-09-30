# AWS App Packager v0.1 구조

이 프로젝트는 독립된 로컬 단일 사용자 Streamlit 프로그램이다. 웹 서버·작업 서버·DB를 별도로 운영하지 않는다. UI는 루프백에서만 서비스하며, AWS SDK나 AWS 실행 경로가 없다.

## 단계와 계약

| 단계 | 담당 모듈 | 입력 → 출력 | 실행 권한 |
|---|---|---|---|
| 환경 진단 | `preflight.py` | 도구 경로·로컬 Docker context → 준비 상태 | 로컬 도구의 버전·context·daemon 정보 조회 |
| 앱 확인 | `project_input.py` | 앱 절대경로 → `ProjectAssessment` | 파일 읽기만 수행 |
| 복사본 승인 | `project_input.py` | 분석 지문·명시적 승인 → `BuildPlan` | `.work/jobs/<job_id>/snapshot`에 선택 파일 복사 |
| 이미지 생성 | `builders/` | 검증된 BuildPlan → `ImageArtifact` | 승인한 코드·의존성 다운로드·Docker/pack 실행 |
| 실행 승인 확인 | `runtime_approval.py` | 승인한 snapshot·소스/조건 지문·실행 job → 승인 기록 | 일반 UI bridge 실행 전 승인 일치 확인 |
| HTTP 시험 | `runtime_check.py` | 같은 image ID·`RuntimeConditions` → `RuntimeCheck` | 별도 실행 승인, 작업별 일반 bridge와 루프백 HTTP 요청 |
| 크기 선택 | `presets.py`, `service.py` | 소형/중형/대형 → 시험 조건 | 이미지 재사용 가능, 조건 변경 시 재시험 |
| 내보내기 | `terraform_export.py`, `reports.py` | 일치하는 성공 증거·`DeploymentSpec` → `ExportBundle` | 로컬 파일 생성; 선택적으로 Docker image save |
| 화면·작업 관리 | `ui.py`, `jobs.py` | 사용자 조작 → 단일 worker 작업 | UI의 직접 Docker 실행 없음 |

`models.py`의 Pydantic 모델은 입력 범위와 상태를 공유한다. `presets.py`의 값은 UI·Docker 자원 제한·사양표에 사용하고 Terraform의 `presets.json`도 같은 값에서 생성한다. 별도 HCL 수치표를 관리하지 않는다. 기본 중형은 성능 최적값 판정이 아니다.

## 승인과 식별

분석 지문은 빌드에 포함할 상대 파일명·크기·SHA256 및 제외 목록을 연결한다. 복사 전 재분석, 복사 중 파일 식별/수정 시각 확인, 복사 후 원본 재검사, 이미지 생성 전후 복사본 검사를 수행한다. 승인 지문은 job·경로·빌드 방식·builder·소스 지문을 연결한다. 승인 전 체크박스가 없거나 원본·복사본·manifest·빌드 설정이 변경되면 진행하지 않는다.

로컬 image ID는 실제 Docker inspect 결과이다. 로컬 시험은 이 ID와 source fingerprint, 조건 fingerprint를 기록한다. 포트·경로·환경·크기·재시작 조건 변경은 이전 성공의 재사용을 막는다. 내보내기 직전 원본과 태그가 가리키는 ID를 다시 확인한다. ECR manifest digest는 수동 push 후 사용자가 확인하는 별도 값이다.

일반 UI의 `_conditions()`는 `network_mode=approved_project_bridge`를 명시한다. `service.start_build`는 빌드와 실행 승인을 모두 확인한 뒤 snapshot을 만들고, `runtime_approval.py`가 빌드 승인·snapshot manifest·소스·실행조건·`execution_job_id`를 연결한 로컬 승인 기록을 남긴다. 런타임은 이 기록과 실제 이미지·복사본을 검증한 뒤에만 Docker 실행을 시작한다. `start_retest`도 새로운 조건의 명시적 승인과 별도 실행 job을 요구한다. 이미지의 빌드 job ID는 유지하고 컨테이너·네트워크 이름과 label·정리 기록은 각 실행 job을 사용한다. 원본 분석은 이 승인·실행 경로를 호출하지 않는다.

Paketo 경로는 공식 고정 run image의 `/tmp`를 ECS 임시 volume에 연결하도록, 빌드 후 도구 소유 Dockerfile로 `VOLUME ["/tmp"]` 메타데이터를 추가한다. 코드 실행이나 USER/ENTRYPOINT/CMD 변경은 없으며 최종 image ID만 시험·내보내기에 사용한다. 중간 이미지와 최종 이미지 증거를 함께 기록한다. 사용자 Dockerfile은 수정하지 않고 단일 `/tmp` VOLUME만 허용하며 권한 준비는 사용자 입력 조건이다.

이 검사는 실수와 변경을 탐지하기 위한 경계이다. 동시에 파일을 악의적으로 계속 교체하는 호스트 공격자까지 격리하는 보안 샌드박스가 아니다.

## 입력과 출력 경계

앱 루트는 로컬 절대경로 하나이다. 드라이브·홈 전체, UNC, 링크/junction/reparse point, hard link, 도구 작업/출력 폴더와의 포함 관계를 거부한다. 파일 수·전체 크기·단일 파일 크기·깊이·시간·방문 항목 수는 `InputLimits` 한 곳에 정의한다.

`.git`, 자격증명/키/DB/업로드/캐시, `.env*`, 빌드 산출물 폴더와 원본 `project.toml` 등을 제외한다. `.env.example`도 복사하지 않고 변수명만 안내한다. Dockerfile은 재작성하지 않는다. 제외된 COPY/ADD 입력을 조용히 복구하지 않는다. `.dockerignore`는 보수적인 부분집합을 지원하고 복잡한 규칙은 승인을 차단한다. README·AGENTS·주석은 앱 데이터이며 프로그램의 지시가 아니다.

`process_runner.py`가 절대 실행 파일 경로, 문자열 인자 배열, `shell=False`, timeout, 취소, 종료 코드 수집을 담당한다. 클라우드 키·앱 비밀값·프록시·사용자 홈은 자동 전달하지 않는다. Docker config와 pack home은 도구 소유 폴더를 사용한다. 출력은 메모리에서 마스킹·길이 제한 후 반환하며 원문 로그 파일을 먼저 만들지 않는다.

긴 출력은 마스킹된 처음 16,384자와 끝부분 약 49,000자를 보존하고 중간 생략을 표시한다. 전체 길이는 65,536자로 제한하여 다운로드 진행 로그 뒤의 실제 마지막 실패 원인을 확인할 수 있게 한다. 이 한도는 문자열 길이이며 원문 로그를 별도 디스크에 보관하지 않는다.

`ProcessRunner.on_progress`는 시작·약 1초 간격·종료 때 호출 스레드로 관찰값을 전달한다. CR/LF로 끝난 레코드를 마스킹한 뒤 최근 16,384자만 공유하며 부분 행은 비밀값이 분할되어 노출되지 않도록 보류한다. 바이트 수신 시각과 명령 제한 시간도 전달한다. `jobs.py`는 단계 진입 시각과 최근 로그를 보존하고 상태 파일 쓰기를 제한한다. `progress.py`는 관찰한 완료 구간 수로 %를 계산하며 경과 시간·로그 양으로 비율이나 완료 예상 시간을 만들지 않는다. 화면 fragment는 1초마다 읽고, 오류/취소를 성공으로 바꾸지 않는다. 콜백 예외가 프로세스 감시를 중단하지 않으며, 작업 기록 저장 실패는 같은 취소 이벤트를 설정한다.

빌드가 반환한 `ImageArtifact`는 로컬 시험 전에 `JobManager.record_artifact`로 보존하여 이후 런타임이 실패해도 실제 image ID를 표시한다. 순수 함수 `describe_workflow`는 이미지, 시작, 최초 포트 mapping, 최초 HTTP, 재시작, 재시작 mapping, 재시작 HTTP, 정리의 8개 결과를 만든다. `RuntimeCheck.outcome`은 `IMAGE_BUILD_FAILED`와 구분되는 `CONTAINER_START_FAILED`, `ENVIRONMENT_BLOCKED_PORT_MAPPING`, `INITIAL_HTTP_FAILED`, `RESTART_HTTP_FAILED`, `PASS`를 제공한다. 빌드 예외는 job의 `IMAGE_BUILD_FAILED`로 남는다. HTTP 상태 코드만으로 통과를 추정하지 않고 `initial_http_passed`·`restart_passed`와 포트별 검증 근거를 사용한다. 정리 단계 진입은 앞선 시험의 성공 근거가 아니다.

보고서에는 개인 source 경로나 원문 image Config/Env를 내보내지 않는다. 작은 ZIP은 제한된 템플릿/문서만 담는다. 이미지 tar는 `docker image save`가 임시 디스크 파일에 기록하고 완료 뒤 확정한다. tar 전체를 Python 메모리나 Streamlit 다운로드 버퍼에 넣지 않는다.

## 로컬 실행과 정리

실행은 검증한 Linux/amd64 image ID 한 개에 한정한다. 이미지 USER는 숫자로 확인 가능한 non-root UID여야 한다. 호스트 mount/socket/privileged/device 공유는 없다. CPU·메모리·PID·로그 제한, read-only root, 제한된 `/tmp`, capabilities 제거와 no-new-privileges를 사용한다. 승인된 일반 UI는 실행 job별 normal user-defined bridge와 `127.0.0.1::8080` publish를 사용한다. 실행 승인 화면은 외부 송신이 허용된다는 점을 명시한다. 포트 8080 외 일반 UI 실행은 차단한다.

이전 자체 샘플 전용 `RuntimeConditions.network_mode=authored_sample_bridge`도 보존한다. `trusted_samples.py`는 opt-in 환경값, 고정된 두 샘플 소스 지문, 생성 이미지·빌드 manifest 기록, 8080·/health·예상 marker·재시작 조건을 확인한다. 일반 UI는 별도의 승인 검증을 거치는 `approved_project_bridge`를 선택하지만 실제 bridge 생성·publish·inspect/port 검증·HTTP·정리 구현은 동일한 `run_check`를 사용한다. 모델의 `internal` 기본값은 호환용이며 UI가 사용하거나 normal bridge 실패 시 자동 전환하는 값이 아니다. 네트워크 방식은 실행조건 지문에 포함한다. normal bridge는 외부 송신 차단을 제공하지 않는다.

호스트 포트는 `127.0.0.1::8080`으로 Docker가 임시 할당한다. 시작 뒤와 재시작 뒤 `.NetworkSettings.Ports`와 `docker container port`를 비교하여 단일 loopback mapping을 확인한 후 HTTP를 요청한다. 없는 mapping은 outcome `ENVIRONMENT_BLOCKED_PORT_MAPPING`과 failure_layer `port_mapping`으로 남고, 기존 status `ENVIRONMENT_BLOCKED`도 유지한다. HTTP 응답 실패는 `INITIAL_HTTP_FAILED` 또는 `RESTART_HTTP_FAILED`로 구분한다. 공개 주소가 0.0.0.0/LAN이거나 두 관찰이 불일치하면 HTTP를 요청하지 않고 정리한다.

이 환경에서는 Docker가 재시작 시 임시 host port를 다시 할당할 수 있다. 고정 포트를 추정하거나 이전 port를 계속 호출하지 않고, 재시작 후 확인한 새 loopback port로 같은 컨테이너의 동일 health path를 시험한다. 시작·재시작 port를 각각 증거에 보존한다. `127.0.0.1`을 명시하는 근거: [Docker 포트 게시 문서](https://docs.docker.com/engine/network/port-publishing/).

HTTP는 `http.client`로 루프백에만 접속한다. 프록시와 리다이렉트는 사용하지 않으며 상대 경로·응답 크기·요청 시간·전체 시험 시간을 제한한다. 응답 body는 결과물에 저장하지 않는다. 샘플의 건강 확인 marker는 실제 응답과 대조한다.

정리 기록은 job별 JSON으로 원자적으로 기록한다. 소유 label과 실제 container/network ID를 다시 검사한 뒤 해당 자원만 제거한다. 기록이 손상되거나 소유권을 확인할 수 없으면 `NEEDS_ATTENTION`으로 남긴다. 이미지와 pack cache는 별도로 보관하며 공유 Docker prune은 하지 않는다.

`devtools/cleanup_local.py`는 사용자가 특정 job을 지정하는 복구 경로이다. UI worker와 같은 OS lock을 획득하고 기존 runtime 정리 함수를 호출한다. 선택적 snapshot 삭제는 자원 정리 성공 후에만 수행하며 정확한 소유 경로와 전체 하위 트리의 링크/경계를 검사한다. `--list`는 저장된 상태만 제한된 수량으로 보여주고 Docker를 호출하지 않는다.

`jobs.py`는 스레드 lock과 OS 파일 lock으로 중복 작업을 막는다. Streamlit rerun 동안 worker를 재시작하지 않는다. worker는 Streamlit API를 호출하지 않고 상태/결과만 제공한다.

`devtools/ui_workflow_integration.py`는 `RUN_LOCAL_CONTAINER_TESTS=1`에서 검토된 자체 샘플만 `service.start_build`로 새로 빌드한다. 일반 UI와 같은 JobManager·승인·snapshot·빌드·런타임 경로를 사용하고 `describe_workflow`의 단계별 화면 결과도 기록한다. Docker 결과를 mock하지 않으며 실행 job 소유 container/network의 실제 삭제 여부를 추가 확인한다. 실제 실행 결과는 `docs/ui_workflow_validation.md`와 `.work/integration/ui-workflow-<id>.json`에서 별도로 관리한다.

## Terraform 경계

출력은 검토 가능한 고정 HCL 템플릿과 JSON 변수 파일이다. `registry` root에서 전용 ECR을 준비한 다음 사용자가 같은 이미지를 push하고 manifest digest를 확인한다. 그 후 `service` root를 별도로 검토·적용한다. 앱은 Terraform CLI가 없어도 내보낼 수 있다.

Terraform CLI 검증은 개발 도구에만 있으며 신뢰된 자체 템플릿만 대상으로 한다. 정적 검사, 실제 CLI validate, 명시적 provider mock 시험, 실제 AWS 적용을 구분한다. AWS 적용 상태는 항상 `AWS_NOT_TESTED`이다.
