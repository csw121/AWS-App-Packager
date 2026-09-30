# AWS App Packager v0.1

로컬 웹앱을 실제 Linux/amd64 컨테이너 이미지로 만들고, 같은 이미지를 위한 AWS ECS/Fargate Terraform·사양표·수동 안내서를 생성합니다. 원본 소스는 변경하지 않습니다. AWS 인증·API·push·배포·삭제는 실행하지 않습니다.

다른 PC에서 이어서 작업하려면 [집 PC 인수인계](docs/HOME_HANDOFF.md)와 [현재 상태](CURRENT_STATE.md)를 먼저 읽으세요. Git 저장소에는 코드·샘플·검증 보고서가 포함되며, 기존 PC의 가상환경·Docker 이미지·작업 폴더·생성 ZIP은 포함되지 않습니다.

## 실행

이 폴더에서 `setup.cmd`를 한 번 실행한 뒤 `run.cmd`를 실행하세요. 준비된 현재 작업공간에서는 바로 `run.cmd`를 사용할 수 있습니다.

```bat
setup.cmd
run.cmd
test.cmd
```

- CPython 3.13 64비트와 로컬 Linux/amd64 Docker 엔진이 필요합니다. Java 경로에는 공식 pack CLI도 필요합니다.
- `setup.cmd`: 이 프로젝트의 새 `.venv`와 잠금 버전 Python 패키지만 준비합니다. Docker·WSL·pack·Terraform은 설치하지 않습니다.
- `run.cmd`: 설치/업데이트를 하지 않습니다. 기본 주소 `http://127.0.0.1:18502`로 시작하고 바인딩 불가 시 뒤의 19개 포트 중 사용 가능한 주소를 표시합니다. 18501은 사용하지 않습니다.
- 경로 공백·한글을 지원하며 PowerShell 실행 정책이나 가상환경 활성화가 필요 없습니다.
- `pack.exe`는 `.tools/pack/pack.exe` 또는 PATH에서 찾습니다. 현재 준비된 공식 버전은 `docs/environment.md`에 기록되어 있습니다.
- Terraform은 제품 필수 의존성이 아닙니다. 개발팀이 신뢰된 생성 템플릿을 검증할 때만 별도로 사용합니다.

## 화면 흐름

1. 앱 절대경로 또는 자체 Java/Dockerfile 샘플을 선택합니다. 후보가 여러 개면 한 앱 루트를 선택합니다.
2. 빌드 범위·제외·다운로드·코드 실행 내용을 읽고 빌드와 실행 시험을 각각 승인합니다. 로컬 시험은 외부 통신이 가능한 작업 전용 일반 bridge를 사용하며, 접속은 이 PC의 127.0.0.1로 제한합니다. 비밀번호/API 키를 입력하지 마세요.
3. 실제 이미지 생성 → Docker inspect → 작업 전용 네트워크 → 127.0.0.1 포트 연결 확인 → HTTP·재시작 시험 → 시험 자원 정리를 수행합니다. 일반 UI의 컨테이너 내부 포트는 8080만 지원하며 호스트 임시 포트는 Docker가 할당합니다.
4. 중형 기본값 또는 소형/대형을 선택합니다. 변경하면 동일 이미지로 다시 시험해야 합니다.
5. AWS 공개 방식과 리전을 선택해 Terraform ZIP·사양표·수동 안내서를 받습니다. 이미지 tar는 디스크에 별도로 저장합니다.

### 작업이 오래 걸릴 때

진행 화면 상단은 **단계 진행률 %와 진행 막대**, 단계별 완료·진행·중단 표시를 보여줍니다. 빌드와 시험은 파일 준비 → 이미지 생성 → 컨테이너 시작 → 웹 응답 시험(선택한 재시작 포함) → 정리·완료 확인의 5단계이며, 순서대로 완료한 단계가 늘면 0/20/40/60/80/100%로 바뀝니다. 작업 시간이나 로그 줄 수로 비율을 올리지 않습니다. 이미지 생성처럼 오래 걸리는 단계에서는 %가 유지될 수 있습니다.

세부 결과 표는 이미지 만들기, 컨테이너 시작, 로컬 포트 연결, 최초 HTTP, 컨테이너 재시작, 재시작 후 포트 연결, 재시작 후 HTTP, 시험 자원 정리를 각각 표시합니다. 완료·실패·환경 차단·진행 중·미실행을 구분하고, 이미지 빌드가 완료되면 실제 image ID를 바로 보여줍니다. 시작·재시작의 `docker port` 출력도 상세에서 확인할 수 있습니다. 포트 연결이 없으면 `ENVIRONMENT_BLOCKED_PORT_MAPPING`이며 이미지 빌드 실패로 표시하지 않습니다. 그 밖의 결과는 `IMAGE_BUILD_FAILED`, `CONTAINER_START_FAILED`, `INITIAL_HTTP_FAILED`, `RESTART_HTTP_FAILED`, `PASS`로 구분합니다. 취소와 정리 실패도 별도로 남습니다.

같은 이미지 재시험은 4단계, tar 저장은 저장/SHA256 확인/확정의 3단계로 계산합니다. 실패·취소·포트 매핑 차단 후 정리 단계로 이동해도 앞선 시험을 완료로 세지 않습니다. 로컬 시험 PASS와 필요한 재시작·CLEANED 및 작업 완료가 확인되어야 100%입니다. tar도 저장 worker가 성공 종료해야 100%가 됩니다.

진행 화면은 1초마다 전체·현재 단계 경과 시간, 현재 명령의 마지막 출력 이후 시간, 실제 로그와 인식한 빌드 단계를 갱신합니다. 로그는 비밀값을 마스킹한 최근 16,384자를 바로 보여줍니다. 60초 넘게 새 출력이 없으면 안내하며, 출력이 없다는 사실만으로 정지라고 판정하지 않습니다. 실패하면 오류와 마지막 로그를 표시하고 타이머를 멈춥니다.

`화면 단계 3/5`는 화면 위치입니다. 별도의 `단계 진행률`은 완료한 작업 단계 수의 비율이며, 이미지 다운로드 바이트 비율이나 소요 시간 비율이 아닙니다. 표시되는 `현재 명령의 제한 시간까지`는 그 명령의 강제 종료 한도이며 예상 대기 시간이 아닙니다. 취소를 누르면 프로세스 종료·자원 정리가 끝날 때까지 `취소 요청 처리 중`으로 표시합니다. 이미지 tar 저장도 같은 진행 화면과 취소를 제공합니다.

코드를 업데이트한 뒤에는 진행 중인 작업의 종료·취소 처리를 기다리고, 기존 실행 터미널에서 Ctrl+C로 앱을 닫은 다음 `run.cmd`를 다시 실행하세요. 브라우저 새로고침만으로는 이미 실행 중인 서버 코드가 바뀌지 않습니다.

| 크기 | vCPU | 메모리 MiB | Fargate CPU | 실행 수 |
|---|---:|---:|---:|---:|
| 소형 | 0.5 | 1024 | 512 | 1 |
| 중형 | 1 | 2048 | 1024 | 1 |
| 대형 | 2 | 4096 | 2048 | 1 |

성능·동시 사용자 수·고가용성 보장이 아닌 제안 프리셋입니다. 가격 계산과 자동 확장은 없습니다.

## 입력과 자체 샘플

- `samples/spring-http`: Java 21 / Spring Boot 3.5.6 / Maven 단일 모듈. Paketo로 빌드하며 호스트 Maven/JDK를 실행하지 않습니다. `/health`, 8080, marker `packager-spring-v1`.
- Paketo 빌드 뒤 `/tmp` VOLUME 메타데이터를 추가합니다. 코드·실행 사용자·시작 명령을 바꾸지 않으며, 그 최종 이미지 ID를 시험과 Terraform에서 사용합니다.
- `samples/docker-http`: Python 3.13.2 표준 라이브러리 고정 HTTP 라우트. non-root Dockerfile, `/health`, 8080, marker `packager-python-v1`.
- 임의 기존 Dockerfile은 일반 언어 분석 없이 단일 HTTP 앱 경로만 지원합니다. 숫자 non-root USER가 필요합니다.
- 일반 UI에서 승인한 로컬 실행은 컨테이너 내부 포트 8080을 사용합니다. 다른 포트의 앱은 설정을 확인해야 하며 제품이 소스나 Dockerfile을 자동 수정하지 않습니다.
- Gradle, Compose, 다중 서비스, 외부 DB 준비/이전, 영구 업로드 데이터, ARM, Windows 컨테이너, 기존 이미지 가져오기, AI/LLM은 지원하지 않습니다.

지원 코드와 실제 검증 범위는 다릅니다. 최신 실제 결과는 `CURRENT_STATE.md`, `docs/acceptance.md`를 확인하세요. HTTP 통과가 업무 기능·데이터 보존·AWS 동작 통과를 의미하지 않습니다.

## 결과물과 보관

성공한 실제 로컬 시험에 한해 `exports/<label>-<job>-<export>/`와 작은 ZIP을 생성합니다.

- `README_FIRST.md`, `AWS_SPEC.md`, `REQUIRED_INPUTS.md`
- `deployment-spec.json`, `LOCAL_TEST_RESULT.md`, `local-test-result.json`, `manifest.json`
- `terraform/registry/`와 `terraform/service/`: 고정 AWS provider와 검토 가능한 템플릿·비민감 확정값·별도 example
- `MANUAL_AWS_RUNBOOK.md`, 미실시 상태의 `AWS_VERIFICATION_RECORD.md`
- `image/image.json`, 선택한 경우 `image/app-image.tar` 및 tar SHA-256 메타데이터

로컬 image ID, tar SHA-256, ECR manifest digest는 다른 값입니다. ECR digest는 사람이 같은 이미지를 push한 뒤 확인해서 입력해야 합니다. CLI에서 한 번 검증한 템플릿 이력을 사용자 bundle 자체의 검증으로 표시하지 않습니다.

2026-09-30 생성된 **Java/Paketo 중형·Python/Dockerfile 중형 ZIP 2개**는 별도 추출 후 네 Terraform root의 실제 `fmt -check` / `init -backend=false` / `validate`를 모두 통과했습니다. 원본 ZIP은 보존했고 검증 결과를 원본 해시와 연결해 별도 기록했습니다. [실제 bundle 검증 보고서](docs/bundle_validation.md)에서 대상 ZIP, 명령 출력, HTTP 시험 모드, AWS 준비값을 확인하세요. 소형/대형 ZIP의 개별 CLI 검증과 AWS 실제 배포는 미실시입니다.

실패·DB/비밀정보 차단 시에는 진단 기록만 다운로드할 수 있습니다. 성공 bundle에 들어갈 수 없습니다.

`.work/jobs/<job>/snapshot`에는 소스 복사본이 남고 빌드 이미지는 Docker에 남습니다. 둘 다 민감한 코드/설정을 포함할 수 있습니다. 작업과 내보내기 폴더는 Git 제외입니다. 시험 컨테이너와 네트워크는 job label·실제 ID를 확인한 뒤 자동 정리합니다. 실패 시 기록을 보존합니다. 공유 Docker prune·전체 stop/rm은 사용하지 마세요. 선택적 정리 방법은 `docs/limitations.md`를 참고하세요.

## 개발팀 검증

```powershell
.\test.cmd
# 자체 샘플만 실제 빌드/실행. 다운로드와 Docker 권한이 필요합니다.
$env:RUN_LOCAL_CONTAINER_TESTS = '1'
# 일반 UI와 동일한 service.start_build → JobManager 경로로 두 샘플을 새로 빌드/시험:
.\.venv\Scripts\python.exe devtools\ui_workflow_integration.py
# 이전 자체 샘플 전용 경로와 동일 이미지 사양별 시험을 재현하는 경우:
.\.venv\Scripts\python.exe devtools\local_integration.py
# 동일 소스의 이전 실제 생성 이미지로 재시험하는 경우:
.\.venv\Scripts\python.exe devtools\local_integration.py --reuse-builds-from .work\integration\result-7fe3d3f2356940859de50f2394d03b2e.json
# 신뢰된 자체 템플릿만. AWS credentials 제거 + 모든 provider mock + command=plan.
.\.venv\Scripts\python.exe devtools\validate_templates.py --run-cli
```

기본 pytest는 실제 Docker/pack/Terraform/AWS를 실행하지 않습니다. 단위시험의 stub과 실제 제품 runner는 구분되어 있습니다. 제품에는 mocked success 스위치가 없습니다.

일반 UI는 빌드·로컬 실행 승인을 모두 받은 뒤 `approved_project_bridge` 방식으로 실행합니다. 승인한 소스 복사본과 실행조건·실행 job의 지문을 검증하고, 실행 job마다 일반 user-defined bridge를 만듭니다. 컨테이너의 8080 포트를 `127.0.0.1::8080`으로만 publish하고 실제 mapping을 검증합니다. 모델의 `internal` 기본값은 이전 호출·기록과의 호환용으로 남아 있으며 일반 UI는 이 기본값을 사용하지 않습니다. 분석 단계는 계속 파일 읽기만 수행합니다.

`ui_workflow_integration.py`는 고정된 자체 Java/Python 샘플만 대상으로 UI 버튼과 동일한 서비스·작업 관리자·승인·snapshot·빌드·런타임을 사용합니다. 이전 이미지를 재사용하지 않고 두 이미지를 새로 빌드합니다. 실행에는 `RUN_LOCAL_CONTAINER_TESTS=1`이 필요하며 기록은 `.work/integration/ui-workflow-<id>.json`에 남습니다. 화면 클릭 자동화나 AppTest mock 성공을 실제 Docker 결과로 대신하지 않습니다. 실행 결과와 실제 포트·image ID는 [일반 UI workflow 검증 기록](docs/ui_workflow_validation.md)에 별도로 기록합니다.

이전 `local_integration.py`의 `authored_sample_bridge` 경로도 남아 있습니다. 검토된 `spring-http`/`docker-http` 소스·빌드 근거가 일치해야 하며, 두 초기 중형 시험이 통과한 다음 동일 이미지의 소형/중형/대형 시험과 성공 bundle 생성을 수행합니다. `--basic`은 초기 시험만 수행합니다. 2026-09-30의 이 경로에서는 기존 실제 이미지로 초기 시험 2개와 사양별 재시험 6개가 HTTP·재시작·CLEANED를 통과하고 성공 bundle 6개를 생성했습니다. [이전 네트워크 검증 기록](docs/network_validation.md)은 당시 이미지·실행 경로에 대한 증거이며 새 일반 UI workflow 검증과 구분합니다. daemon·방화벽·Docker 버전·전역 자원 설정은 변경하지 않습니다.

학교 AWS 검증은 `docs/manual_aws_validation.md`와 생성 runbook을 사람이 별도로 수행합니다. HTTPS는 같은 리전 ACM 인증서와 일치하는 도메인/DNS가 필요합니다. 명시적인 HTTP 실습 모드는 제한 CIDR을 필수로 받습니다. IAM 역할 생성 권한이 없으면 승인된 기존 execution role 분기를 사용하세요. Fargate 임시 volume 쓰기 권한과 로컬 Docker 옵션 차이는 AWS에서 검증할 항목입니다.

구조·안전 경계: `docs/architecture.md` · 제한: `docs/limitations.md` · 선택 근거: `docs/decisions.md` · 제3자 출처: `THIRD_PARTY_NOTICES.md`.
