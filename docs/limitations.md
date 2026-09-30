# v0.1 지원 범위와 제한

실제 검증 기록은 `CURRENT_STATE.md`, `docs/environment.md`, `docs/acceptance.md`를 따른다. 코드가 존재하거나 단위시험이 통과했다는 사실은 실제 이미지 빌드·HTTP 시험·AWS 성공 증거가 아니다. Docker daemon이 시작되지 않으면 컨테이너 통합 검증은 BLOCKED/NOT_RUN으로 남는다.

## 지원 입력

- Java 21, 단일 모듈 Maven, Spring Boot web/webflux dependency와 실행 JAR plugin이 명시된 웹앱. 호스트 Maven/JDK는 실행하지 않는다.
- 앱 루트의 기존 Dockerfile을 가진 단일 Linux/amd64 HTTP 앱. 언어의 업무 로직을 분석하거나 자동 수정하지 않는다.
- 승인된 일반 UI 로컬 시험은 컨테이너 포트 8080만 지원한다. 호스트 포트는 Docker가 할당한 임시 값이며 시작과 재시작 후 달라질 수 있다.
- Gradle 상세 대응, 콘솔 앱, multi-module/Compose/여러 서비스, ARM/Windows/GPU, 기존 이미지 가져오기, 정적 S3 배포는 지원하지 않는다.
- DB·결제·메일·로그인·영구 파일 저장 등 추가 환경을 자동 구성하거나 운영 서비스에 시험 연결하지 않는다. 파싱하지 못한 입력은 의존성이 없다는 뜻이 아니다.

## 제한된 분석과 복사본

Spring XML/properties와 제한된 YAML에서 실행 단서만 읽는다. profile, 변수 치환, 설정 우선순위, 동적 Java/Maven 코드를 완전히 해석하지 않는다. EXPOSE와 설정 포트는 선언이며 실제 listener는 HTTP 시험으로 확인해야 한다.

입력 제한은 파일 5,000개, 방문 항목 20,000개, 전체 256 MiB, 단일 파일 32 MiB, 깊이 20, 각 읽기/복사 단계 30초이다. 구조 분석용 텍스트는 파일당 512 KiB로 제한한다. 이미지·정적 리소스는 이 제한과 경계를 통과하면 바이너리 그대로 복사한다.

안전을 위해 `.env*`, 인증서/키 저장소, 실제 DB/dump/업로드, 캐시와 `target`, `build`, `dist` 폴더는 제외한다. 미리 만든 JAR을 COPY하는 Dockerfile처럼 제외 산출물에 의존하는 입력은 이 버전에서 차단될 수 있다. 사용자 원본을 고치거나 민감 파일을 자동 재포함하지 않는다.

`.dockerignore`의 일반 glob 규칙은 보수적으로 적용한다. `!` 재포함, escape, 문자 집합 규칙은 지원하지 않고 승인을 차단한다. 복잡한 규칙을 묵인하지 않으며, 자체 제외를 원본 ignore 규칙으로 해제할 수 없다.

원격 ADD/COPY, 외부 COPY context/image, 동적 COPY 경로, Dockerfile frontend 변경, RUN mount/network/security 확장은 지원하지 않는다. VOLUME은 정확한 `/tmp` 경로 하나만 제한적으로 허용하며, 다른 경로·여러 경로·변수로 정한 경로는 차단한다. 원본 `project.toml`은 복사하지 않으며 Paketo에는 도구가 만든 descriptor를 명시한다.

## 코드 실행의 권한

빌드와 컨테이너 실행은 별도 동의가 필요한 코드 실행이다. 공식 builder도 의존성과 Maven plugin을 실행한다. Dockerfile RUN과 ADD도 네트워크·코드 실행을 포함할 수 있다. 패키지/이미지 다운로드가 필요할 수 있으므로 오프라인 동작을 보장하지 않는다.

Docker daemon과 공식 pack lifecycle은 강한 권한의 신뢰 경계이다. `--trust-builder`는 고정된 공식 builder에만 사용하며 전역 trusted-builder 설정은 변경하지 않는다. 로컬 컨테이너 제한을 악성 코드에 대한 완전한 격리라고 표현하지 않는다. 신뢰하고 검토한 프로젝트를 전용 개발 환경에서 사용해야 한다.

현재 실행 경로는 명시적 숫자 non-root USER만 허용한다. 빈 USER, root, 이름만 있는 USER, `/tmp` 이외 VOLUME·쓰기 요구를 자동 권한 상승이나 호스트 mount로 해결하지 않는다. 허용한 `/tmp` 선언도 로컬 시험에서는 제한된 tmpfs로 명시적으로 덮어쓴다.

Java 경로는 신뢰된 Paketo 결과에 `VOLUME ["/tmp"]` 메타데이터를 준비한다. USER·ENTRYPOINT/CMD·rootfs layer를 보존하는 최종화이며 사용자 비즈니스 코드나 원본 Dockerfile을 고치지 않는다. 이후 로컬 시험·tar·사용자 ECR 업로드는 모두 최종화한 동일 image ID를 기준으로 한다. Dockerfile 경로는 사용자가 작성한 이미지를 그대로 사용하므로 `/tmp` 권한과 필요한 선언을 사용자가 준비해야 한다.

일반 UI는 빌드·로컬 실행을 각각 명시적으로 승인한 프로젝트에 한해 작업 전용 normal bridge를 사용한다. 파일 분석만으로 실행하지 않으며, 승인한 소스 복사본과 빌드 근거·실행조건·실행 job을 다시 확인한다. normal bridge에서는 앱의 외부 송신이 가능하다. UI는 이 사실을 실행 승인 전에 고지하며 신뢰하지 않는 프로젝트를 안전하게 실행할 수 있다고 보장하지 않는다.

게시 포트는 `127.0.0.1::8080` 하나로 제한하며, 시작·재시작 후 inspect와 `docker port` 결과에서 실제 단일 루프백 mapping을 검증한 다음 HTTP를 요청한다. mapping이 없거나 공개 인터페이스·불일치가 발견되면 `ENVIRONMENT_BLOCKED_PORT_MAPPING`으로 기록하고 해당 실행 job의 컨테이너·네트워크만 정리한다. host network·privileged·Docker socket·호스트 홈 mount로 우회하지 않으며 daemon·방화벽·Docker 버전도 변경하지 않는다.

모델의 `internal` 기본값과 이전 `authored_sample_bridge` 통합시험 경로는 호환·과거 검증 재현을 위해 남아 있다. 일반 UI는 이 기본값 대신 승인 조건이 포함된 `approved_project_bridge`를 명시한다. 과거 자체 샘플 성공이나 AppTest mock 성공만으로 새 UI workflow의 실제 Docker 성공을 주장하지 않는다. 새 경로의 실행 결과는 `docs/ui_workflow_validation.md`에 따로 기록한다.

## 결과 해석

HTTP 200은 지정 경로의 응답 시험이다. 업무 기능·로그인·외부 DB·데이터 보존·AWS 성능이 검증되었다는 뜻이 아니다. 재시작 시험도 데이터 지속성을 보장하지 않는다. 소형/중형/대형은 고정 사양이며 동시 사용자 수·최적 성능을 보장하지 않는다.

화면은 이미지 생성·컨테이너 시작·포트 연결·HTTP·재시작·재시작 포트·재시작 HTTP·정리를 따로 표시한다. 실제 image ID가 표시되어도 이후 HTTP가 실패할 수 있다. mapping 차단은 HTTP 미실행이고 이미지 빌드 실패가 아니다. 자체 샘플은 HTTP 200뿐 아니라 기대 marker도 맞아야 통과한다. 진행률은 완료한 작업 구간의 비율이며 남은 시간이나 다운로드 비율을 예측하지 않는다.

프로세스 출력의 비밀정보 마스킹은 방어 수단이며 완전한 탐지 보장이 아니다. 알 수 없는 형식·인코딩·간접 출력은 검출되지 않을 수 있다. UI에 실제 비밀값을 입력하는 기능은 없다. 이 프로젝트를 보안 감사 도구로 사용하지 않는다.

소스 복사본, pack cache와 이미지/tar에는 앱 코드·설정이 남는다. `.work`, `.tools`, `exports`는 Git에서 제외된다. 작업이 끝나고 실행 중인 worker/해당 job의 container/network가 없는지 확인한 뒤 그 job의 snapshot 폴더와 필요 없는 export만 사용자가 삭제할 수 있다. 소유를 확인하지 못한 pack cache는 자동 삭제하지 않는다. 공유 Docker 자원의 전체 정리는 수행하지 않는다.

### 작업별 로컬 정리

실행 중인 UI 작업과 개발팀 통합시험을 먼저 완료하거나 취소한다. 프로젝트 루트의 별도 터미널에서 다음처럼 명시적으로 실행한다.

```powershell
.\.venv\Scripts\python.exe devtools\cleanup_local.py --list
.\.venv\Scripts\python.exe devtools\cleanup_local.py --job JOB_UUID
.\.venv\Scripts\python.exe devtools\cleanup_local.py --job JOB_UUID --remove-snapshot
```

`JOB_UUID`는 `--list`가 표시한 특정 작업 ID로 바꾼다. 목록은 최대 100개 작업의 ID와 저장된 정리 상태만 보여주며 Docker를 조회하지 않는다. `CLEANED_RECORDED`는 기록상 정리 완료이고 새로운 daemon 실측 결과가 아니다.

`--job`은 기존 `cleanup_owned`를 통해 실제 container/network ID와 소유 label을 확인한다. 같은 이미지의 재시험도 별도 `execution_job_id`를 가지므로 정리할 실제 실행 job을 지정한다. `--remove-snapshot`은 snapshot이 존재하는 빌드 job에서만 사용하며, 지정한 경우 자원 정리 성공 또는 정리 대상 없음 확인 뒤 해당 작업의 snapshot을 삭제한다. manifest와 모든 대상 경로·링크·junction·hard link·경계를 검사하며, 검사 실패나 실행 중인 UI 작업이 있으면 중단한다. source 원본, 이미지, pack cache, export, 다른 작업, 전역 Docker 자원은 삭제하지 않는다. UI 작업의 OS lock을 공유하며 개발팀 별도 CLI 시험과는 동시에 실행하지 않는다.

## AWS에 남은 확인

이 도구는 인증, AWS API, ECR push, 원격 상태 조회, 실제 plan/apply/destroy를 실행하지 않는다. 계정 권한·PassRole·할당량·AZ·주소 충돌·execution role·인증서·DNS·이미지 manifest digest는 별도 확인 대상이다. 비용 계산·AI/LLM·자동 확장은 범위 밖이다.

템플릿은 public subnet과 public IP를 쓰는 고정 ECS/Fargate + ALB 구성이며 desired_count=1이다. 업데이트 중 task가 일시적으로 두 개가 될 수 있다. 고가용성·운영 보안 완성·단일 task만 실행됨을 보장하지 않는다.

Fargate의 task-scoped `/tmp` volume은 로컬 mode=1777 tmpfs와 다르며 non-root 쓰기 권한과 앱 시작을 실제 AWS에서 확인해야 한다. 로컬 PID 제한, tmpfs 크기/noexec/nosuid와 no-new-privileges를 같은 의미로 Terraform에 복사하지 않는다. 이 차이를 숨기고 배포 성공으로 표시하지 않는다.

전체 AWS 실험은 학교 계정에서 사용자가 별도로 수행하고 결과 양식을 채운다. 계정 권한 제약을 우회하는 관리자 권한 부여를 안내하지 않는다.

**`/tmp` 이미지 준비와 실제 검증의 경계:** [AWS bind mount 문서](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html)의 같은 VOLUME/containerPath 연결 방식에 맞추어 Java 최종 이미지의 `/tmp` 메타데이터를 준비한다. 자체 Dockerfile 샘플도 `/tmp`를 선언한다. 이 준비가 실제 Fargate에서 기대한 소유권·권한으로 동작하는지는 별도 AWS 검증 대상이다. `VOLUME` 선언 자체는 권한의 증거가 아니며, 기본 빈 volume의 root:0755 권한에 의존하지 않도록 이미지 디렉터리 권한을 검토해야 한다.

2026-09-29 개발팀 진단에서 그때 생성된 자체 Java/Python 샘플 이미지의 `/tmp`가 `1777:0:0`임을 실제로 관찰했다. 기록은 `.work/integration/tmp-mode-diagnostic.json`에 이미지 ID별로 있다. 이것은 해당 로컬 이미지의 디렉터리 권한 관찰이며 최종 이미지의 HTTP 시험이나 AWS volume 권한 성공 기록이 아니다. 최종 이미지별 빌드·시험 결과는 실제 통합시험 기록을 따른다. 일반 Dockerfile 앱의 권한은 사용자 준비 조건이며 자체 샘플 관찰을 일반화하지 않는다. root 실행·추가 초기화 컨테이너로 우회하지 않는다.
