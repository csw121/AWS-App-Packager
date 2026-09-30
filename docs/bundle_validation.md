# 실제 생성된 중형 success bundle Terraform 검증

2026-09-30 10:34:38–10:35:06 KST. **Java/Paketo 중형과 Python/Dockerfile 중형의 실제 ZIP에서 추출한 네 root가 모두 PASS**다. Terraform 1.14.8 / AWS provider 6.14.0 / Windows amd64에서 실행했다. AWS에는 접속하지 않았다.

기계 판독 기록과 모든 명령 출력: [bundle_validation.json](bundle_validation.json).
이번 검증은 이전 `template_validation.json`의 템플릿/mock 시험과 별개이며, 소형/대형 ZIP에는 이 결과를 적용하지 않는다.

## 대상과 원본 보존

| 항목 | Java / Spring Boot / Paketo | Python / Dockerfile |
|---|---|---|
| 원본 ZIP | `exports/spring-http-1cfcb09e414d45fbb92fffef51e8047a-fe353915.zip` | `exports/docker-http-f8cffc9a37c44d51aee1ea4c035dfe88-140fb808.zip` |
| ZIP SHA256 | `af5caac58afae2e623721dbc7daf32ffb25aef3517b8b8a9110a66829e3b5c49` | `7475b6f0ecd46cc9d266e61bc1af25ff1c047203f179a209c0f39e0b9745abce` |
| 추출 하위 폴더 | `java-medium` | `python-medium` |
| 원본 ZIP 변경 없음 | PASS | PASS |
| 추출된 기존 파일 변경 없음 | PASS | PASS |
| ZIP manifest 파일 해시 일치 | PASS | PASS |

검증 임시 디렉터리(프로젝트 내부):

```text
.work/bundle-validation/e5c37647eb8c4840a503b9280d5e3e60/
  java-medium/terraform/registry/
  java-medium/terraform/service/
  python-medium/terraform/registry/
  python-medium/terraform/service/
  home/
  terraform.rc
  result.json
```

각 ZIP의 23개 파일을 개별 폴더에 byte 그대로 추출했다. manifest 파일별 SHA256과 검토된 자체 HCL의 해시를 확인하고, 기존 bundle 디렉터리와 ZIP은 쓰지 않았다. fmt는 `-check`만 사용했다. init이 추가한 `.terraform/`와 `.terraform.lock.hcl`은 임시 root에만 있다.

원본 manifest의 `terraform_cli_status=NOT_RUN_FOR_THIS_BUNDLE`은 생성 당시 상태로 보존했다. 이후 CLI 검증 결과는 원본 ZIP SHA256·manifest SHA256·전체 파일 해시를 연결한 이 외부 보고서로 증명한다. 원본을 재압축하거나 PASS 문자열로 바꾸지 않았다.

## 1–4. 실제 CLI 결과

각 root를 현재 디렉터리로 삼아 다음 세 명령을 순서대로 실행했다.

```text
terraform fmt -check
terraform init -backend=false -input=false -no-color
terraform validate -no-color
```

| 번호 | 실제 bundle root | fmt -check | init -backend=false | validate |
|---|---|---|---|---|
| 1 | Java 중형 / registry | PASS (exit 0) | PASS (exit 0) | PASS (exit 0) |
| 2 | Java 중형 / service | PASS (exit 0) | PASS (exit 0) | PASS (exit 0) |
| 3 | Python 중형 / registry | PASS (exit 0) | PASS (exit 0) | PASS (exit 0) |
| 4 | Python 중형 / service | PASS (exit 0) | PASS (exit 0) | PASS (exit 0) |

네 validate 출력은 모두 `Success! The configuration is valid.`다. fmt 출력은 비어 있고 종료 코드는 0이다. 총 12개 명령을 실제 실행했으며 mock으로 대신하지 않았다.

실행기는 인자 배열·`shell=False`·명령당 300초 timeout·마스킹된 제한 출력을 사용한다. AWS 자격증명 및 `TF_VAR_*`/`TF_CLI_ARGS*` 등 호스트 환경을 상속하지 않았고, 새 HOME/USERPROFILE 및 별도 CLI 설정을 사용했다. EC2 metadata와 checkpoint 접근은 비활성화했다. 기존 사용자 인증 파일이나 설정은 읽거나 변경하지 않았다.

### init 경고와 provider 출처

추가 다운로드 없이 이전 자체 검증 때 `signed by HashiCorp`로 설치한 provider를 프로젝트 내부 filesystem mirror에서 재사용했다. 설치 직전 unpacked provider의 두 파일과 h1을 다시 계산해 당시 잠금 파일과 대조했다.

- provider h1: `h1:j08+AhqbMSg7/I4mMJCLB/uNJAcd85dNKLTxJ3z8rHo=`
- 실행 파일 SHA256: `8724e27cd2d248dcbf1c0a09ab4ea40918ea18379ef02b73bc1b95c0ea4e58cc`
- 출처: `.work/template-validation/f8e7e5a1b128423fa20c0a34c866ce11/registry/.terraform/providers/`
- 이번 CLI RC는 이 filesystem mirror만 허용하며 direct/network mirror는 없다.

따라서 이번 init 출력에는 `(unauthenticated)`와 `Incomplete lock file information for providers` 경고가 있다. 이번 init에서 원격 서명을 새로 검증했다는 뜻이 아니며, 앞서 서명 확인된 파일과의 체크섬 일치로 출처를 확인했다. 임시 lock에는 `windows_amd64` 체크섬만 있어 다른 OS용 잠금 파일로 배포하지 않는다. 원본 bundle에는 이 lock을 추가하지 않았다. init과 validate는 모두 성공했다.

실행 스크립트는 `.work/bundle-validation/validate_medium_bundles.py`에 보존했다. 정확히 위 두 ZIP 해시만 허용하며 version/fmt/init/validate 외 실행 경로가 없다. 재실행 시 새 UUID 디렉터리를 만든다. 이번에는 `terraform test`도 실행하지 않았다.

## 5. generator/템플릿 및 placeholder 판정

generator와 템플릿 수정, bundle 재생성은 **없다**. 실제 CLI 실패나 generator/구조 오류가 발견되지 않았다.

각 `terraform/service/terraform.tfvars.json`에는 실제 시험으로 확정된 medium, 8080, `/health`, 200, 사용자 UID/GID, 빈 환경변수와 기본 공개 방식이 들어 있다. `image_uri`, `availability_zones`, `allowed_cidrs`, 인증서/도메인은 의도적으로 미입력이다. `.tfvars.example`의 문서용 ARN/IP/digest는 자동 로드되는 실제 입력이 아니며, 이번에 가짜 값으로 채우지 않았다.

이는 정상적인 후속 외부 입력 요구사항이다. `validate`는 구성 문법과 내부 일관성을 검사하며 원격 서비스·계정 권한·실제 배포 입력을 검증하지 않는다. **PASS가 외부 입력 준비 또는 AWS 배포 가능성을 확정하지 않는다.** [Terraform 1.14 validate 문서](https://developer.hashicorp.com/terraform/cli/v1.14.x/commands/validate).

## 6. ACM/DNS 필수 여부

두 실제 ZIP의 기본값은 `exposure_mode="https_existing_certificate"`다. 이 기본 방식에는 같은 리전의 기존 ACM 인증서 ARN인 `certificate_arn`과 인증서에 일치하는 `domain_name`이 필요하다. 실제 사용을 위해 도메인 DNS를 ALB로 수동 연결해야 한다.

Terraform은 ACM 인증서 또는 Route53/DNS 레코드를 생성하지 않는다. HTTPS에 필요한 준비가 없으면 기본 설정 그대로 배포하는 단계로 넘어가면 안 된다. 이 검토에서는 HTTPS 기본값을 변경하지 않았다.

## 7. 첫 AWS 시험을 HTTP만으로 할 수 있는가

**구조상 가능하다.** 기존 `http_demo` 분기가 ALB HTTP 80 → target group HTTP 8080 → Fargate를 구성한다. 이 경우 사용자 도메인/DNS/ACM은 필요 없고 `application_url` output이 ALB DNS의 HTTP 주소를 반환한다. `/health`를 해당 주소에 붙여 시험한다. 실제 AWS에서의 성공 여부는 아직 미검증이다.

추후 사용자가 별도 작업 복사본의 service root에 만드는 `inputs.auto.tfvars`에서 다음 공개 방식만 명시하면 된다. 자동 로드 순서에 따라 원래 JSON의 HTTPS 기본값을 덮어쓴다. 원본 ZIP에는 이번에 이 파일을 추가하지 않았다.

```hcl
exposure_mode   = "http_demo"
certificate_arn = null
domain_name     = null
```

추가로 실제 `availability_zones`, `allowed_cidrs`, `image_uri`가 필요하다. `allowed_cidrs`는 실습자의 공인 IPv4 `/32` 등 승인된 범위로 제한하며 `/0`은 거부한다. 이 모드는 암호화되지 않은 실습용이며 자체 샘플의 비민감 HTTP 응답 확인에 사용한다. 포트·health path·환경·사양은 로컬 시험한 값을 유지한다.

근거는 각 ZIP의 `service/variables.tf`, `service/main.tf`의 `aws_lb_listener.http_demo`, `service/outputs.tf`다.

## 8. 생성 예정 주요 AWS 리소스

아래 수량은 **bundle 하나를 적용할 때의 정의**이며 아직 생성된 AWS 자원은 없다. 두 bundle을 각각 적용하면 별도 자원 집합이다.

| 자원 | 수량 / 조건 |
|---|---|
| ECR repository | registry root에 1개, immutable tag, AES256, push scan, force_delete=false |
| VPC / Internet Gateway | 각 1개 |
| public subnet | 서로 다른 AZ에 2개 |
| route table / 인터넷 기본 route / association | 1 / 1 / 2 |
| security group | ALB와 task 각 1개, 관련 ingress/egress 규칙 |
| 인터넷 ALB / IP target group | 각 1개 |
| ALB listener | HTTP 실습 1개(80); HTTPS 2개(443 전달, 80 redirect) |
| ECS cluster / task definition / service | 각 1개, Linux/X86_64 Fargate |
| Fargate task | 중형 1 vCPU / 2048 MiB / desired_count=1, public IP 할당 |
| CloudWatch Logs group | 1개, 7일 보존 |
| IAM execution role + inline policy | 기본 새 역할 각 1개; 승인된 기존 역할 선택 시 생성하지 않음 |

NAT Gateway, RDS, EC2 서버, EFS, VPC endpoint, autoscaling, ACM/Route53 생성은 없다. 앱 task IAM role도 생성하지 않는다. public subnet의 task public IP와 IGW로 image pull·로그 전송을 하는 구조다. 앱 포트 수신은 ALB 보안그룹에서만 허용한다.

ALB/Fargate/public IPv4/ECR/로그 사용량은 남으므로 무료 또는 비용 상한을 보장하지 않는다. 금액은 계산하지 않았다. 배포 설정이 minimum 100% / maximum 200%여서 업데이트 중 최대 2 task가 일시적으로 존재할 수 있다.

## 9. 사용자가 학교 AWS에서 준비할 값과 권한

| 값 / 준비사항 | 용도 |
|---|---|
| 승인된 AWS 계정·배포 주체·리전 | 인증정보를 이 앱/보고서에 입력하지 않음. 기본 리전 ap-northeast-2 |
| `repository_name` | Java 기본 spring-http-1cfcb09e / Python docker-http-f8cffc9a; 계정·리전 내 새 전용 이름 사용 가능성 확인 |
| `availability_zones` | 선택 리전에서 사용 가능한 서로 다른 실제 AZ 2개 |
| `allowed_cidrs` | 승인된 실습 접속 공인 IPv4 CIDR 목록. 기본 인터넷 전체 공개 없음 |
| `vpc_cidr` | 기본 10.72.0.0/16; 기관 네트워크/동시 실습과의 충돌 검토 |
| `image_uri` | 동일 이미지를 수동 push한 뒤 확인한 실제 ECR manifest digest를 포함한 URI |
| 공개 방식 | HTTP는 위 3개 값; HTTPS는 같은 리전 ACM ARN, 일치 도메인, 수동 DNS |
| execution role 선택 | 아래 새 역할 또는 기관 승인 기존 역할 |
| 계정 권한·할당량 | ECR, VPC/네트워크, ELB, ECS/Fargate, Logs, 해당 IAM 작업 및 학교 정책 |

`image_uri` 형식은 `<account>.dkr.ecr.<region>.amazonaws.com/<repository>@sha256:<actual-manifest-digest>`다. `service/variables.tf`에서 같은 리전 ECR URI와 64자리 digest를 요구하며 task definition의 container image에 연결한다. **로컬 image ID 또는 tar SHA256을 ECR manifest digest로 대신하지 않는다.** image URI는 이번 보고서에서도 미입력이다.

기본 `create_execution_role=true`이면 execution role과 제한된 inline policy를 생성하므로 배포 주체에게 역할 생성/정책 관리 권한 및 해당 역할을 전달하는 `iam:PassRole`이 필요하다. 학교에서 역할 생성을 막으면 `create_execution_role=false`, `existing_execution_role_arn=<기관이 승인한 실제 ARN>`을 입력한다. 이 분기는 기존 역할을 수정·삭제하지 않는다.

기존 execution role에는 `ecs-tasks.amazonaws.com` 신뢰, ECR 인증과 해당 저장소 image pull, 해당 CloudWatch log group의 log stream 생성/쓰기 권한이 필요하다. [AWS execution role 문서](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_execution_IAM_role.html).

`iam:PassRole`은 Terraform 입력 변수가 아니라 **배포 실행 주체의 권한**이다. 같은 계정의 승인된 execution role ARN을 ECS tasks에 전달할 수 있어야 한다. 역할에 PassRole 권한을 넣는 것으로 배포 주체의 권한을 대신하지 않는다. [AWS PassRole 문서](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html).

## 10. 아직 실행하지 않은 항목

- 이번 작업의 Terraform plan, test(명시적 mock 포함), apply, destroy.
- AWS 인증/프로필 변경, AWS CLI/API, ECR login/push/digest 조회, 원격 state 접근.
- 실제 Fargate 시작·image pull·ALB health·HTTP/HTTPS 연결·DNS·인증서 유효성·IAM/PassRole·할당량 확인.
- Fargate task-scoped `/tmp` volume의 non-root 쓰기 권한과 실제 자원 정리.
- 소형/대형 네 ZIP의 개별 Terraform CLI 검증.
- 이번 작업에서 Docker 재빌드/재실행, 제품 pytest/AppTest 재실행. 제품 코드를 변경하지 않았고 이전 시험 결과는 과거 기록으로 유지한다.

이번에 추가된 것은 검증용 임시 폴더/실행기와 결과 문서다. generator/템플릿·원본 ZIP·AWS 설정은 변경하지 않았다. 여기서 멈추고 실제 AWS 검증은 사용자가 별도로 진행한다.
