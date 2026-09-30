# AWS App Packager v0.1 현재 상태

기록일: 2026-09-30. 작업공간 `D:\AWS App Packager`에서 새로 작성했다. 시작 시 빈 폴더·Git 저장소 없음 확인. 기존 Cloud Readiness Lab 코드·가상환경·Git 이력은 사용하거나 수정하지 않았다.

## 집 PC 인계 — 2026-09-30

사용자가 이 독립 프로젝트를 `https://github.com/eliet9999/AWS-App-Packager.git`로 옮겨 집에서 이어서 작업하도록 요청했다. 새 PC의 clone·Python 3.13 환경 준비·실행·시험 순서는 [docs/HOME_HANDOFF.md](docs/HOME_HANDOFF.md)에 정리했다. `.venv`, `.tools`, `.work`, `exports` 및 Docker 이미지/cache는 Git으로 옮겨지지 않는다. 보고서에 있는 이전 PC의 절대경로와 로컬 증거/ZIP 링크는 새 clone에 없을 수 있다.

인계 기준 최신 결과는 일반 UI와 같은 workflow의 Java/Python 실제 새 빌드·HTTP·재시작·CLEANED PASS, 기본 회귀 **480 PASS, 1 SKIP**, Ruff PASS다. 저장된 요약 근거는 `docs/ui_workflow_validation.md/json`이다. 집 PC의 검증은 재실행 전 **NOT_RUN**이며, 새 이미지·승인·시험 증거와 필요한 bundle을 다시 생성한다. 현재 AWS 실제 실행은 계속 NOT_RUN이다. 아래 과거 실패·수정·성공 이력은 해당 시점의 기록으로 보존한다.

## 실행

현재 준비된 환경에서는 `run.cmd`를 실행한다. 기본 주소는 `http://127.0.0.1:18502`이며 기존 프로세스의 포트를 강제로 차지하지 않는다. 새 설치는 `setup.cmd`, 기본 검증은 `test.cmd`이다. 모두 프로젝트 폴더에서 실행한다.

## 구현 범위

Streamlit 5단계 화면, 읽기 전용 앱 판정·제한된 snapshot, Java 21 Maven/Paketo와 기존 Dockerfile 실제 빌드, 이미지 ID 확인, 제한된 non-root 실행·루프백 HTTP·재시작·선택 정리, 소형/중형/대형 동일 이미지 재시험, 일치하는 성공 증거의 Terraform/사양표/runbook ZIP 및 디스크 tar 저장을 구현했다.

실제 제품 경로는 ProcessRunner를 사용하며 mocked success 옵션이 없다. 실패·취소·환경 차단 때 성공 bundle을 생성하지 않는다. AWS 인증/API/push/실제 plan/apply/destroy, 비용 계산, AI/LLM, 자동 확장은 없다.

2026-09-30 진행 표시 개선: 1초마다 전체·단계 경과, 마지막 출력 이후 시간, 실제 로그 표지의 세부 단계, 최근 마스킹 로그를 표시한다. 60초 넘는 출력 지연 안내, 명령 제한 시간(완료 예상 시간 아님), 취소 처리 상태, 실패 원인 즉시 표시를 추가했다. 오해하기 쉬운 화면 위치 진행 막대는 `화면 단계 N/5`로 바꿨다. tar 해시 계산 중에도 취소를 확인한다. Classic Docker와 BuildKit/Paketo 로그를 모두 읽으며 buildx 전용 CLI 옵션을 요구하지 않는다.

2026-09-30 추가 요청으로 **작업 단계 진행률 %·진행 막대·단계별 상태**를 화면 상단에 추가했다. 화면 위치와 구분하며 빌드/시험 5단계, 재시험 4단계, tar 저장 3단계의 실제 완료 경계를 사용한다. 시간·빌드 로그로 %를 늘리지 않는다. HTTP 성공 marker는 요청된 시험(재시작 선택 포함)을 통과한 뒤에만 기록하고, 실패 후 finally 정리 진입은 진행률을 올리지 않는다. 작업 DONE과 HTTP PASS/필요한 재시작/CLEANED가 확인될 때만 100%다. 최신 기본 회귀 **430 PASS, 1 SKIP**, Ruff PASS; 근거 `.work/validation/percentage-pytest.xml`. 실제 Docker·Terraform·AWS는 이 UI 변경 검증에서 재실행하지 않았다. 실행 중인 서버는 종료 후 `run.cmd`를 다시 실행해야 새 코드가 반영된다.

추가 실제 브라우저 검증: 자체 `samples/docker-http`를 job `be40e2e2c1a6465a909f55b8c6000b89`로 빌드했다. image ID `sha256:d3b587cd1907d4e70e6ef53498b01cf77eaa08237a905935bbb6e1c47a3b233f`, BUILT/inspect PASS, 컨테이너 시작, HTTP BLOCKED, 정리 CLEANED. 약 20초의 경과 표시·최근 실제 빌드 로그·차단 결과를 화면에서 확인했다. 근거: `.work/jobs/be40e2e2c1a6465a909f55b8c6000b89.json` 및 같은 이름 폴더의 `build-result.json`. 중간 검증에서 발견한 `--progress` 옵션 호환성 오류는 옵션 제거 후 이 재실행으로 수정 확인했다. 이번에는 Java·tar 실제 통합·Terraform CLI·AWS를 재실행하지 않았다.

## 현재 검증 상태

**최신: 일반 UI workflow 실제 새 빌드·HTTP·재시작·정리 모두 PASS.** 2026-09-30 14:43–14:50 KST, 명시적으로 승인된 프로젝트의 UI 조건을 `approved_project_bridge`로 연결했다. 실행 job별 승인 기록과 별도 normal user-defined bridge를 사용하며 기존 공통 runtime 구현을 재사용한다. 소스 분석은 실행하지 않는다. container 8080을 `127.0.0.1::8080`에만 게시하고 시작/재시작 후 inspect와 docker port를 대조한 다음에만 HTTP를 요청한다. 일반 UI가 더 이상 internal-only bridge 기본값을 그대로 사용하지 않는다. 모델의 과거 internal 기본값은 오래된 기록 호환용으로만 유지한다.

일반 UI 버튼과 동일한 `service.start_build()` / JobManager workflow에서 자체 Java/Paketo와 Python/Dockerfile을 각각 새로 빌드했다. Java image ID는 `sha256:62a758719af208b29138edfbe935fc6212f5d4e7865c654dc2500f68892db9e6`, Python은 `sha256:a67dc6caab9cbac799e3d980ad37db2c3ebce1e3bd6ba08e07f52467c05a1392`다. 최초/재시작 실제 docker port는 Java `127.0.0.1:24992 → 127.0.0.1:25000`, Python `127.0.0.1:30924 → 127.0.0.1:30932`다. 두 샘플 모두 각 포트에서 `/health` HTTP 200·기대 marker를 확인했고 해당 job의 container/network만 CLEANED, 삭제 후 ID inspect로 부재까지 확인했다. 새 빌드 명령에서 정상 빌드 캐시는 사용할 수 있다.

최신 근거: `docs/ui_workflow_validation.md`, `docs/ui_workflow_validation.json`, 원본 `.work/integration/ui-workflow-7333baaae387421c90fb0510fbedf5b2.json`. **480 PASS, 1 SKIP**, Ruff PASS. 초기 승인 기록 생성 경로 오류로 빌드 전 차단된 시도도 원본 기록으로 보존하고 수정 후 재실행했다. 이번에는 AWS/Terraform/push·소형/대형·bundle 재생성·tar를 실행하지 않았다.

UI는 빌드/컨테이너/최초 mapping/최초 HTTP/재시작/재시작 mapping/재시작 HTTP/정리를 따로 표시한다. mapping 차단은 `ENVIRONMENT_BLOCKED_PORT_MAPPING`이며 빌드 성공과 실제 image ID를 보존하고 후속 검사를 미실행으로 남긴다. 결과 분류·8개 단계·image ID·완료 기반 %를 실제 두 job의 저장 기록으로 제품 renderer에서 브라우저 확인했다. 실제 실행은 UI와 동일한 서비스 진입점에서 수행했으며 브라우저 버튼 클릭 종단 검증과는 구분한다.

### 이전 자체 샘플 전용 통합과 bundle 검증

**승인된 자체 샘플 로컬 통합 PASS.** 2026-09-30 사용자가 지정한 normal user-defined bridge 예외를 검토된 두 샘플에만 적용했다. 이전 실제 생성 이미지의 소스·빌드 근거·ID를 확인해 재사용했으며, 새 빌드를 수행한 것으로 기록하지 않는다. 초기 중형 2개, 이어서 같은 이미지의 소형/중형/대형 6개 모두 최초 HTTP 200·marker, 재시작 후 HTTP 200·marker, CLEANED를 확인했다. 실제 성공 bundle 6개를 생성했다.

최종 기록: `.work/integration/result-63e3c3a6322647f880f9d49e5e91693c.json`. 실제 포트 전체·ZIP 링크·실패 수정 이력은 `docs/network_validation.md`, 결과물 hash/ZIP/조건 대조는 `.work/validation/network-output-verification.json`에 있다. 기본 회귀는 **398 PASS, 1 SKIP**, Ruff PASS다.

2026-09-30 추가 검증: 실제 생성된 Java/Paketo 중형 `spring-http-1cfcb09e414d45fbb92fffef51e8047a-fe353915.zip`과 Python/Dockerfile 중형 `docker-http-f8cffc9a37c44d51aee1ea4c035dfe88-140fb808.zip`을 각각 임시 디렉터리에 추출했다. 각 registry/service의 실제 Terraform CLI `fmt -check`, `init -backend=false`, `validate` 총 12개 명령이 모두 PASS다. 원본 ZIP과 추출한 기존 파일은 hash 불변이며 generator 수정은 없다. 근거: `docs/bundle_validation.md`, `docs/bundle_validation.json`. 이번에는 plan/test/apply/destroy와 AWS 실행을 하지 않았다. 소형/대형 ZIP의 개별 CLI 검증은 NOT_RUN이다.

위 과거 자체 샘플 전용 시험 당시에는 일반 UI의 internal bridge를 유지했다. 그 전용 모드의 RUN_LOCAL_CONTAINER_TESTS=1·고정 소스·생성 증거 guard는 현재도 보존한다. 최신 일반 UI 승인 경로는 앞 절의 별도 승인 기록을 사용한다. normal bridge는 외부 송신이 가능하며 host 공개는 127.0.0.1만 허용한다. daemon·방화벽·버전·공유 자원은 변경하지 않았다.

첫 normal bridge 시도 `.work/integration/result-23dd82c803004ae3b163bbf03c738be5.json`에서는 Java 재시작 port 변경을 불필요하게 실패 처리하고, Python의 완료된 HTTP 본문 뒤 닫힌 socket을 재접근하는 시험기 오류를 발견했다. 이 시도는 FAIL/CLEANED로 보존하고 사양 시험·export를 차단했다. 포트 재확인 및 HTTP EOF/Content-Length 처리를 수정한 뒤 위 최종 실제 통합을 통과했다.

### 이전 internal bridge 검증 이력

2026-09-29 전체 샘플 통합 기록: `.work/integration/result-7fe3d3f2356940859de50f2394d03b2e.json` (종료 2026-09-29 15:00 KST). 세 항목 모두 해당 명령에서 실제 빌드했고, 컨테이너 시작 뒤 환경 차단을 기록했으며 container/network 정리는 CLEANED다.

| 자체 샘플 | 최종 image ID | 실제 결과 |
|---|---|---|
| Java / Spring Boot / Paketo | `sha256:3bcfb78df19a983fdd12603d97a5c867dd3b6e77b8b5bffb90f32cc1df30d55e` | BUILT, metadata 준비 PASS, 실행 시작, HTTP BLOCKED, CLEANED |
| Python / Dockerfile | `sha256:bfe62b4dc83c6a3d7b81ddd0c2a7e21fc488f8cd436e68bcec8c20b9cf2714b9` | BUILT, 실행 시작, HTTP BLOCKED, CLEANED |
| Python 이름 변경 복사본 | `sha256:588d74394ab2e34f28a2155f7e670938bcf573e6f45cf486cafd6b0dae8d5b62` | 새 빌드, 실행 시작, HTTP BLOCKED, CLEANED |

이전 internal 실행 조건: Linux/amd64, 중형 1 vCPU/2048 MiB, 컨테이너 8080, 요청 경로 `/health`, non-root, read-only root, `/tmp` 64 MiB tmpfs, 요청한 host bind는 127.0.0.1이었다. 당시 실제 host port 미할당으로 HTTP 상태는 null이었고 재시작·소형 재시험·컨테이너 404는 NOT_RUN이었다. 이 과거 결과를 소급하여 PASS로 변경하지 않았다.

Java 빌드 증거: `.work/jobs/1cfcb09e414d45fbb92fffef51e8047a/build-result.json`. Paketo 중간 ID `sha256:c86bd0ce8fc1359000a79bb35c848e044f7adca572790a995d2720023b4bb9b6`에서 `/tmp` VOLUME과 소유 label만 준비했다. USER·rootfs 불변을 확인하고 최종 ID를 시험했다. `RUN`이나 root 실행을 추가하지 않았다. 실제 `/tmp` mode 1777 관찰과 tmpfs override 시 익명 volume 없음은 `.work/integration/tmp-mode-diagnostic.json`, `tmp-volume-override-diagnostic.json`에 별도 기록한다.

## 생성 파일과 실제 tar

**이전 성공한 Terraform/사양표/runbook ZIP 6개 보존.** Java와 Python 각각 소형/중형/대형에 대응하며 당시 같은 샘플 image ID를 유지한다. `exports/`의 정확한 6개 ZIP 링크는 `docs/network_validation.md`에 있다. 실제 시험 결과 JSON·네트워크 방식·시작/재시작 mapping을 포함하며 AWS_NOT_TESTED이다. 그중 중형 ZIP 두 개의 후속 CLI 검증은 `docs/bundle_validation.md`의 PASS 기록을 따른다. 이번 일반 UI workflow의 새 이미지로 ZIP을 다시 생성하지 않았다.

실제 Python 최종 이미지 tar: `exports/diagnostic-f8cffc9a37c44d51aee1ea4c035dfe88/image/app-image.tar`. 같은 폴더에 `app-image.tar.json`, 상위에 `DIAGNOSTIC_ONLY.md`가 있다. HTTP 미통과 진단 이미지이며 AWS 미검증이다.

- 실제 크기: 44,344,320 bytes.
- tar SHA-256: `78ffd3bffeb35ec14d1d79d7f2eef97ca7b412fb6a39d3751e0272c0c45c3f2e`.
- 실제 `image save` → `image load` → 새 고유 tag inspect의 image ID 일치: PASS.
- roundtrip tag: `aws-app-packager/tar-roundtrip:f312a518787241feb1759aca3d0f204d`.
- 로컬 image ID·tar SHA·미확인 ECR manifest digest는 서로 구분한다.

소스 snapshot·마스킹된 build/runtime 기록은 `.work/jobs/<job>/`, 원래 빌드 이미지와 중간 이미지·pack cache는 Docker에 남는다. 이전 실패/진단 기록도 보존했다. 소유 시험 container/network만 정리했으며 공유 prune·사용자 이미지 삭제는 하지 않았다.

## 시험 결과

| 종류 | 실제 결과 | 근거 |
|---|---|---|
| 단위시험 | 450 PASS | `.work/validation/approved-ui-pytest.xml` (2026-09-30) |
| Streamlit AppTest | 30 PASS, 명시적 service/worker stub 포함 | 같은 XML; 실제 Docker 결과와 분리 |
| Ruff | PASS | 최종 `.venv\Scripts\python.exe -m ruff check .` 실행 출력 |
| 기본 opt-in Docker pytest | 1 SKIP | 기본 명령은 Docker를 실행하지 않음 |
| 최신 실제 일반 UI workflow | 중형 Java/Python 새 빌드·최초/재시작 mapping·HTTP 200·marker·CLEANED 전부 PASS | `docs/ui_workflow_validation.json` |
| 별도 실제 샘플 통합 | 기존 실제 이미지 2개 재사용, 초기 2개+크기별 6개 HTTP/재시작 PASS, 전부 CLEANED | result-63e3c3a6322647f880f9d49e5e91693c.json |
| 실제 success bundle | 6개 생성 및 ZIP/hash/이미지/사양/시험조건 대조 PASS | network-output-verification.json |
| 실제 tar save/load | Python 최종 이미지 PASS | integration extended.tar_roundtrip |
| 실제 Terraform CLI: 자체 템플릿 | 두 root fmt/init -backend=false/validate PASS | `docs/template_validation.json` |
| 실제 Terraform CLI: 생성된 중형 ZIP 2개 | 네 root, fmt/init -backend=false/validate 12개 명령 PASS; 원본 불변 | `docs/bundle_validation.json` |
| 명시적 Terraform mock | registry 1 + service 10 PASS | 같은 기록; 모든 provider mock, command=plan |
| 실제 AWS / 업무 기능 / 부하 / 인간 사용성 | NOT_RUN | 이번 작업 범위와 환경 제한 |

이전 브라우저 확인은 Windows loopback 서버 기동, 샘플 선택·분석, 8080 및 /health 입력, 두 승인 전 실행 버튼 비활성화였다. 최신 추가 확인은 일반 UI와 동일한 workflow의 실제 Docker 결과를 제품 renderer로 표시한 읽기 전용 브라우저 화면이다. 성공 fixture AppTest, 실제 서비스 workflow 통합, 브라우저 표시 확인은 각각 구분한다.

최초 시도의 Docker 시작 소켓 오류, daemon 변경 중 다운로드 EOF, Docker 29 선택적 inspect 필드 오류는 이전 기록에 남아 있다. 선택적 필드 처리와 내부 port 미게시 BLOCKED 구분을 수정한 뒤 위 최종 시험을 수행했다.

## 환경

Python 3.13.2 64-bit, Streamlit 1.64.0, Pydantic 2.13.5, pytest 9.1.1, Ruff 0.16.9, Docker CLI/Engine 29.8.1 및 Desktop 4.93.0, pack 0.40.9, Terraform 1.14.8, AWS provider 6.14.0. 고정 Paketo builder 0.4.579 / run image 0.1.227, 자세한 digest와 설치 경위는 `docs/environment.md`.

## AWS 검증

이전 신뢰된 자체 템플릿 두 root의 fmt/init/validate 및 11개 명시적 mock plan 이력은 `docs/template_validation.json`이다. 추가로 위 두 실제 중형 ZIP의 네 root에서 fmt/init/validate를 실행해 통과했다. 이 결과는 `docs/bundle_validation.json`에 원본 ZIP 해시와 함께 별도 기록했다. 원본 manifest는 생성 당시 NOT_RUN 상태를 보존했으며 모든 사용자 bundle의 CLI 검증으로 확대하지 않는다. 실제 AWS는 미실시다.

학교 계정에서 리전/AZ 2개·접속 CIDR·ECR push 후 실제 manifest digest·신규 또는 기관 승인 execution role/PassRole을 별도로 준비해야 한다. 기본 HTTPS에는 ACM 인증서와 도메인/DNS가 필요하지만, 첫 자체 샘플 시험은 기존 `http_demo` 분기로 ACM/DNS 없이 ALB DNS의 HTTP 주소를 사용할 수 있다. `/tmp` 권한 전달·Fargate 시작·ALB health·계정 권한/할당량·정리 결과는 실제 AWS에서 검증한다.

## 재개할 한 단계

제품 서버를 종료하고 `run.cmd`를 다시 실행하면 새 일반 UI 승인 경로가 반영된다. 자체 샘플의 동일 workflow는 새 빌드·HTTP·재시작·정리를 통과했다. 승인한 외부 앱은 같은 경로를 사용할 수 있지만 해당 앱의 업무 기능·부하·AWS 실제 적용은 별도 검증해야 한다. 기존 bundle을 검토하고 학교 계정의 AWS 검증을 별도로 수행할 수 있다. 이미지 tar save/load는 이번에 재실행하지 않았으며 기존 진단 tar 기록과 구분한다.

지원하지 않는 범위: Gradle/다중 서비스/DB·영구파일 준비와 이전, 임의 기존 이미지 가져오기, ARM/Windows 이미지, 전체 업무 기능·부하 성능·인간 사용성 연구·오프라인 실행 보장. 세부 근거는 `docs/acceptance.md`, `docs/limitations.md`.
