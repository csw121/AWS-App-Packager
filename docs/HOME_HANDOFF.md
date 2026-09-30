# 집 PC에서 이어서 작업하기

인계 기준: 2026-09-30. 저장소: `https://github.com/eliet9999/AWS-App-Packager.git`.

이 저장소에는 독립적인 AWS App Packager v0.1의 코드·자체 샘플·템플릿·시험 코드·문서가 들어 있다. 이전 Cloud Readiness Lab의 코드나 환경을 가져오지 않는다. 현재 기능과 과거 검증 이력은 [CURRENT_STATE.md](../CURRENT_STATE.md), 최신 일반 UI 실행 결과는 [ui_workflow_validation.md](ui_workflow_validation.md)를 먼저 읽는다.

## 먼저 실행하기

집 PC에 Git과 CPython **3.13.x 64비트**가 준비되어 있어야 한다. 아래 명령은 PowerShell에서 실행하며, 처음 clone할 상위 폴더는 사용자가 선택한다.

```powershell
git clone https://github.com/eliet9999/AWS-App-Packager.git
Set-Location 'AWS-App-Packager'
py -3.13 -c "import sys, struct; print(sys.version); print(struct.calcsize('P') * 8)"
.\setup.cmd
.\run.cmd
```

비공개 저장소라면 집 PC의 Git 인증 절차로 접근한다. 토큰을 저장소 URL, 코드, 문서 또는 셸 명령에 붙여 저장하지 않는다.

`setup.cmd`는 새 `.venv`를 만들고 `requirements.lock.txt`의 Python 패키지를 내려받아 설치한 뒤 프로젝트를 editable로 설치한다. 네트워크가 필요하다. Docker·WSL·pack·Terraform은 설치하지 않으며, 시스템 설정도 바꾸지 않는다. Python 3.13을 찾지 못하면 Python 준비 후 다시 실행한다. 기존 PC의 `.venv`를 복사하지 않는다.

`run.cmd`는 설치 없이 앱을 시작한다. 기본 주소는 `http://127.0.0.1:18502`이며, 사용 중이면 이후 19개 포트에서 사용 가능한 주소를 출력한다. **실행 터미널에 나온 주소**를 연다. 예전 스크린샷이나 시험 보고서의 포트는 재사용할 주소가 아니다. 가상환경 활성화와 PowerShell 실행 정책 변경은 필요 없다.

코드를 pull한 뒤에는 진행 중인 시험의 완료·정리를 기다리고, 서버 터미널에서 Ctrl+C로 종료한 다음 `run.cmd`를 다시 실행한다. 잠금 의존성이 바뀌었다면 먼저 `setup.cmd`를 다시 실행한다.

## 실제 빌드를 위한 준비

- Docker Desktop을 사용자가 설치·시작하고 **로컬 Linux/amd64 엔진**을 준비한다. 엔진 상태와 권한은 앱의 환경 진단으로 확인한다. 원격 Docker endpoint와 Windows/ARM 컨테이너 환경은 지원 범위가 아니다.
- Java/Paketo 시험에는 공식 `pack` CLI가 필요하다. 기존 확인 버전은 **0.40.9**이며, [공식 pack releases](https://github.com/buildpacks/pack/releases)에서 Windows amd64 배포본과 대응 SHA-256 자료를 확인하여 별도로 준비한다. 다운로드 파일의 SHA-256을 대조한 뒤 **`.tools/pack/pack.exe`**에 둔다. 기존 프로젝트의 `.tools`나 실행 파일을 복사하지 않는다. 프로젝트 안에 두면 시스템 PATH 변경은 필요 없다.
- Docker CLI는 PATH 또는 Docker Desktop의 표준 설치 경로에서 찾는다. 제품은 엔진을 자동 설치·시작하지 않는다. Java 빌드에 호스트 JDK/Maven을 설치할 필요는 없다.
- 첫 빌드는 고정된 builder/run/base 이미지와 Maven 의존성 등을 다운로드할 수 있다. 도구 설치·다운로드·실행 권한은 집 PC의 정책에 맞게 승인한다. 과거 PC에서 허용되었다는 이유로 시스템 설정을 바꾸지 않는다.
- Terraform은 프로그램 실행이나 ZIP 생성의 필수 도구가 아니다. 이번 재개 확인에 AWS 인증이나 Terraform 실행은 필요 없다.

기존 PC에서 확인한 버전은 [environment.md](environment.md)에 있다. 그 문서의 초기 internal bridge 차단과 환경 기록은 과거 이력이며, 현재 제품의 일반 UI 네트워크 정책은 아래와 [CURRENT_STATE.md](../CURRENT_STATE.md)를 따른다. 동일 버전 설치나 새 PC의 동작을 이미 검증했다고 해석하지 않는다.

## Git으로 옮겨지지 않는 것

| 항목 | 집 PC에서의 처리 |
|---|---|
| `.venv/` | `setup.cmd`로 새로 생성 |
| `.tools/` | 필요한 공식 도구를 별도로 준비 |
| `.work/` | 새 실행의 snapshot·승인·job·시험 기록을 새로 생성 |
| `exports/` | 새 로컬 시험이 통과한 뒤 bundle/tar를 다시 생성 |
| Docker 이미지·volume·build cache | Git에 포함되지 않음. 집 PC에서 새로 빌드 |
| AWS credentials·실제 tfvars·state·plan·비밀값 | 저장소에 넣지 않음 |

`docs/*validation.md/json`은 이전 PC의 결과를 보관한 **보고서**다. 보고서 안의 `D:\...`, `.work/...`, `exports/...` 링크나 파일 경로는 새 clone에서 존재하지 않을 수 있다. Git에 있는 JSON만 복사해서 빌드 증거·승인을 복원하거나 실제 이미지가 있는 것으로 처리하지 않는다. 이전 image ID도 새 PC에 이미지가 있다는 뜻이 아니다. 이미지 ID와 포트는 새 실행에서 다시 확인한다.

자체 샘플과 신뢰된 템플릿은 파일 내용의 지문으로 보호한다. 저장소의 `.gitattributes`는 `* -text`로 Git의 자동 줄바꿈 변환을 막아 검토된 파일 bytes를 보존한다. 편집기의 자동 포맷 등으로 소스 또는 줄바꿈이 바뀌어 guard가 막히면 원인을 검토한다. 통과시키려고 지문을 임의로 갱신하거나 guard를 해제하지 않는다.

## 집 PC에서 다시 확인하는 순서

먼저 실제 외부 도구를 실행하지 않는 기본 검증을 수행한다.

```powershell
Remove-Item Env:RUN_LOCAL_CONTAINER_TESTS -ErrorAction SilentlyContinue
.\test.cmd
```

이 명령은 Ruff와 pytest를 실행한다. 기본 pytest는 실제 Docker/pack/Terraform/AWS를 호출하지 않는다. Streamlit AppTest의 service/worker stub은 UI 검증이며 실제 컨테이너 성공을 의미하지 않는다.

Docker와 pack을 준비하고 자체 샘플의 다운로드·코드 실행을 승인한 뒤, **일반 UI와 같은 workflow**를 실제로 검증한다. UI에서 다른 실행 job이 진행 중이지 않아야 한다.

```powershell
$env:RUN_LOCAL_CONTAINER_TESTS = '1'
.\.venv\Scripts\python.exe devtools\ui_workflow_integration.py
Remove-Item Env:RUN_LOCAL_CONTAINER_TESTS
```

이 명령은 `samples/spring-http`와 `samples/docker-http`를 `service.start_build()` → JobManager → 승인 기록 → snapshot → 새 이미지 빌드 → 공통 runtime으로 시험한다. 중형, 8080, `/health`, 기대 marker, 재시작 조건이다. 새 보고서는 `.work/integration/ui-workflow-<id>.json`에 생성된다. **이 명령 자체는 성공 bundle을 만들지 않는다.** 실패하면 차단 계층과 정리 상태를 기록하고, mock 결과로 바꾸지 않는다.

bundle이 필요하면 UI에서 자체 샘플을 선택하고 빌드·실행을 각각 승인한다. 실제 시험 PASS/CLEANED 후 원하는 사양을 선택하고, 사양 변경 시 같은 이미지로 재시험한 다음 내보내기를 진행한다. 모든 크기의 자체 샘플 bundle을 개발팀용으로 다시 만들 때는 다음 별도 경로를 사용한다.

```powershell
$env:RUN_LOCAL_CONTAINER_TESTS = '1'
.\.venv\Scripts\python.exe devtools\local_integration.py
Remove-Item Env:RUN_LOCAL_CONTAINER_TESTS
```

`local_integration.py`는 이전 자체 샘플 전용 `authored_sample_bridge` 경로다. 두 초기 중형 시험이 통과한 뒤 동일 이미지의 소형/중형/대형 시험과 성공 bundle 생성을 진행한다. 위 일반 UI workflow 시험과 결과를 구분한다. 새 clone에서는 이전 PC의 `--reuse-builds-from` 경로를 지정하지 않는다. 결과는 `.work/integration/`, 생성된 ZIP은 `exports/`에 있다.

## 인계 시점의 검증과 새 PC의 상태

| 구분 | 2026-09-30 기존 PC 기록 | 집 PC |
|---|---|---|
| 기본 회귀 | 480 PASS, 1 SKIP; Ruff PASS | 재실행 전 NOT_RUN |
| 일반 UI와 같은 실제 workflow | Java/Paketo·Python/Dockerfile 새 빌드, 시작/재시작 mapping, HTTP 200·marker, CLEANED 모두 PASS | 재실행 전 NOT_RUN |
| 이전 자체 샘플 전용 시험 | 동일 이미지 사양별 시험 및 성공 bundle 6개 생성 | 로컬 이미지·bundle 없음 |
| 생성된 중형 bundle Terraform | 기존 Java/Python ZIP의 registry/service 4 root에서 fmt/init -backend=false/validate PASS | 새 bundle에 대한 CLI 검증은 NOT_RUN |
| Terraform 명시적 mock | 과거 신뢰된 자체 템플릿에서 11개 PASS | 실제 AWS 결과가 아님 |
| AWS 실제 배포·권한·업무 기능·부하 | NOT_RUN | NOT_RUN |

최신 실제 이미지/포트/HTTP 결과는 [ui_workflow_validation.md](ui_workflow_validation.md)와 [JSON](ui_workflow_validation.json), 이전 bundle 검증은 [bundle_validation.md](bundle_validation.md)에 있다. 이전 PC의 PASS를 새 PC나 새 bundle의 PASS로 자동 승계하지 않는다.

## 다음 작업의 안전 경계

일반 UI는 소스 분석 중 코드를 실행하지 않는다. 사용자가 빌드와 로컬 시험을 명시적으로 승인한 뒤 실행 job마다 normal user-defined bridge를 만들고 `127.0.0.1::8080`으로만 publish한다. 외부 송신은 가능하다. 시작과 재시작 후 `docker inspect`/`docker port`를 대조하여 실제 mapping을 확인한 뒤 HTTP를 요청하고, 변경된 임시 port를 다시 사용한다. 해당 job의 container/network만 정리한다. 누락되거나 안전하지 않은 mapping은 `ENVIRONMENT_BLOCKED_PORT_MAPPING`이며 빌드 실패로 바꾸지 않는다.

AWS 인증/API/CLI·이미지 push·실제 Terraform plan/apply/destroy는 수행하지 않는다. 신뢰된 자체 템플릿의 개발팀 정적/CLI/mock 검증은 별도 승인 범위와 기록을 따른다. 임의 사용자 Terraform은 실행하지 않는다. Docker daemon·방화벽 변경, downgrade, factory reset, prune, 기존 사용자 이미지/volume/network 삭제, privileged/host network/Docker socket mount를 사용하지 않는다.

집에서 Codex 작업을 재개할 때는 이 저장소를 작업공간으로 열고 다음처럼 요청하면 된다.

> AGENTS.md, CURRENT_STATE.md, docs/HOME_HANDOFF.md를 읽어줘. 현재 경로와 새 PC의 Python/Docker/pack 환경을 먼저 확인하고, 기본 회귀 테스트 후 필요한 도구·다운로드 승인을 구분해서 자체 샘플의 일반 UI workflow 실제 통합시험을 재현해줘. 이전 PC의 결과를 새 PC의 성공으로 취급하지 말고 새 기록을 남겨줘. AWS 작업이나 시스템 설정 변경은 하지 마.
