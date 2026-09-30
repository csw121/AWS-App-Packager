# v0.1 결정 기록

결정일: 2026-09-29. 검증 버전·실행 결과는 별도 환경/검증 문서에 기록한다.

| 결정 | 이유와 영향 |
|---|---|
| 독립 Python 패키지 + Streamlit + Pydantic 2 | 새 작업공간 안에서 책임을 분리하며 추가 웹 프레임워크·DB·queue를 도입하지 않는다. |
| Python 3.13 64비트 / Windows 로컬 | 요청한 실행 환경에 맞춘다. 기본 UI 후보 포트는 18502이고 루프백만 허용한다. |
| Java 21 Maven Spring Boot + 기존 Dockerfile 두 경로 | 소스 언어별 범용 분석 대신 실제 컨테이너 생성 경로 두 개를 완성한다. |
| 읽기 전용 분석과 빌드/런타임 승인 분리 | 파일 확인만으로 사용자 코드를 실행하지 않는다. 승인 대상 소스/조건의 지문을 보존한다. |
| 도구 소유 snapshot에서 빌드 | 원본 불변, 민감 파일 제외, source 변경 시 기존 승인 차단을 적용한다. |
| 복잡한 ignore/Dockerfile 입력은 보수적으로 차단 | 분석이 불완전한 상태에서 build context를 넓히지 않는다. 원본 Dockerfile도 재작성하지 않는다. |
| pack descriptor를 도구가 직접 지정 | 원본 project.toml이 builder/buildpack/extension 설정을 덮어쓰지 못하도록 한다. |
| 공식 Paketo builder/run image를 tag + 확인된 digest로 고정 | latest를 검증 버전으로 표시하지 않는다. 이미지 실제 빌드 검증과 메타데이터 확인은 별도로 기록한다. |
| Paketo 최종 이미지의 /tmp VOLUME 메타데이터 | ECS 빈 volume의 root:0755 문제를 피할 수 있도록 고정 run image의 /tmp 권한을 전달한다. 코드 실행 없이 메타데이터만 추가하고 최종 ID로 시험한다. 실제 AWS 권한 전달은 별도 검증한다. |
| 기본 중형: 1 vCPU, 2048 MiB, task 1개 | 첫 시험의 기준이다. 성능 측정 결과 또는 사용자 수 보장이 아니다. |
| 소형 0.5/1024, 중형 1/2048, 대형 2/4096 | 세 선택지 모두 같은 이미지와 Terraform 프로필을 사용한다. 조건이 바뀌면 HTTP 재시험한다. |
| 숫자 non-root UID, read-only root, /tmp만 쓰기 | v0.1의 제한된 자동 시험 조건이다. 모르는 USER나 추가 쓰기 요구를 자동 완화하지 않는다. |
| 초기 internal network 방식은 모델 호환용으로 유지 | 최초 일반 UI 경로에서 localhost port mapping이 생성되지 않는 환경 차단이 관찰되었다. 현재 일반 UI는 아래 승인된 normal bridge 경로를 명시적으로 선택한다. 자동 fallback이나 daemon 변경은 하지 않는다. |
| 자체 샘플 normal bridge 시험 경로 보존 | 2026-09-30의 이전 사용자 요청으로 검토한 두 샘플만 실행하는 authored_sample_bridge를 구현했다. 당시 실제 성공 증거는 별도로 보존하며 일반 UI의 새 실행 검증과 구분한다. |
| 명시적으로 승인한 일반 UI 프로젝트에 normal bridge 적용 | 2026-09-30 후속 사용자 요청. approved_project_bridge는 승인한 snapshot·빌드 근거·실행조건·실행 job을 확인한다. 외부 송신 가능성을 고지하고 빌드/실행 각각 승인 후에만 사용한다. 기존 runtime 구현을 재사용한다. |
| 실행 job마다 별도 네트워크와 127.0.0.1::8080 | 사용자 승인 범위에 따라 일반 UI 포트는 8080으로 제한한다. Docker가 임시 host port를 할당하며 시작과 재시작 후 inspect·docker port 양쪽에서 단일 루프백 mapping을 확인한 뒤 HTTP를 요청한다. 재시작 후 이전 host port를 재사용하지 않는다. |
| 빌드 job과 실행 job 식별 분리 | 같은 이미지 재시험도 새 execution_job_id를 가진다. 컨테이너·네트워크 label과 정리 기록은 실행 job 기준이며 실제 ID와 소유권이 확인된 자원만 정리한다. |
| 단계별 실패 분류와 실제 image ID 표시 | 이미지 빌드·컨테이너 시작·포트 mapping·최초 HTTP·재시작 HTTP 실패를 구분한다. 이미지 성공은 런타임 전에 보존하고, mapping 차단 이후 HTTP는 미실행으로 표시한다. 경과 시간으로 %를 올리지 않는다. |
| 일반 UI와 동일한 서비스 경로의 실제 통합시험 | devtools/ui_workflow_integration.py는 opt-in에서 고정된 자체 샘플을 service.start_build로 새로 빌드한다. JobManager·승인·snapshot·런타임은 UI와 같고, 실제 Docker 결과·정리와 UI 단계 결과를 함께 기록한다. mock/AppTest를 실제 실행 증거로 대체하지 않는다. |
| registry/service Terraform 두 root | ECR 생성 → 수동 push → 실제 manifest digest → service 적용 순서를 보존한다. |
| 일반 ECS/Fargate + ALB 프로필 하나 | ECS Express Mode·EC2·EKS·RDS 등으로 범위를 넓히지 않는다. |
| HTTPS 기존 인증서가 기본, HTTP는 제한 CIDR의 명시적 실습 모드 | 인증서·도메인은 사용자 준비사항이며 가짜 값을 성공으로 표시하지 않는다. |
| execution role 신규/기관 제공 기존 role 분기 | 학교 계정의 IAM 제약을 숨기지 않는다. 기존 role은 수정/삭제하지 않는다. |
| CloudWatch Logs 보존 7일, ECR force_delete=false | 명확한 기본 사양과 검토 가능한 수동 정리 절차를 제공한다. |
| 작은 문서 ZIP과 큰 이미지 tar 분리 | tar를 메모리에 통째로 올리지 않고 실제 image save를 디스크로 수행한다. |
| 기본 테스트와 opt-in 통합시험 분리 | 기본 pytest가 사용자 모르게 Docker/pack/Terraform/AWS를 실행하지 않게 한다. |
| 자체 템플릿 검증 전용 devtools | 사용자 Terraform은 실행하지 않는다. 정적 검사/CLI validate/mock plan과 실제 AWS를 구분한다. |

프로그램은 이미지 업로드나 AWS 적용 버튼을 제공하지 않는다. 생성된 안내서에 있는 AWS/수동 Terraform 명령은 사용자가 학교 계정의 별도 터미널에서 검토 후 실행한다.
