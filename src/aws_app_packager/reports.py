"""Human-readable offline reports; these functions never execute cloud commands."""

from .models import DeploymentSpec, ImageArtifact, RuntimeCheck
from .presets import PRESETS
from .redaction import redact

VERIFICATION_RECORD = """# AWS 수동 검증 기록 — AWS_NOT_TESTED

모든 항목은 실제 학교 계정 시험 전에는 미실시입니다. 자격증명·비밀값은 기록하지 마세요.

| 항목 | 기록 |
|---|---|
| 검증 일시 / 담당자 | 미실시 |
| 승인된 계정 별칭 / 리전 / AZ | 미실시 |
| registry 적용 / repository URI | 미실시 |
| 로컬 image ID / 업로드 release tag | 미실시 |
| ECR manifest digest | 미실시 |
| preset / CPU / memory / desired count | 미실시 |
| execution role 신규 또는 기존 / 권한 제약 | 미실시 |
| exposure mode / CIDR / 인증서·DNS 검토 | 미실시 |
| service plan 검토 / 적용 결과 | 미실시 |
| task 시작 / image digest 일치 | 미실시 |
| 업로드한 최종 이미지의 /tmp VOLUME·디렉터리 권한 | 미실시 |
| non-root /tmp 쓰기 권한 / 시작 로그 | 미실시 |
| ALB target health / 지정 HTTP 경로 | 미실시 |
| 지정 업무 기능·외부 서비스 시험 범위 | 미실시 |
| task 교체 후 HTTP 재시험 | 미실시 |
| 데이터 보존 시험 (기본적으로 범위 밖) | 미실시 |
| 실패 원인 / 다음 조치 | 미실시 |
| destroy plan 검토 / service 정리 | 미실시 |
| 전용 ECR image·repository 정리 | 미실시 |
| 남은 ALB / ENI / log / ECR / 기타 자원 | 미실시 |

HTTP 정상 응답은 로그인·DB·데이터 보존·전체 업무 기능 검증과 다릅니다.
단일 task 종료만으로 모든 자원이 정리되거나 비용이 중단됐다고 기록하지 않습니다.
"""


def _tmp_image_note(artifact: ImageArtifact) -> str:
    if artifact.build_method == "paketo_java":
        prepared = "paketo_tmp_volume_v1" in {item.strip() for item in artifact.builder_info.split("|")}
        if prepared:
            return (
                "이 최종 Java 이미지에는 `paketo_tmp_volume_v1` 준비 기록이 있습니다. "
                "USER·ENTRYPOINT/CMD·ENV·rootfs layer를 보존하고 `/tmp` VOLUME 메타데이터를 준비했습니다. "
                "이 기록은 실제 AWS volume 권한 성공을 뜻하지 않습니다."
            )
        return (
            "이 Java 이미지 기록에는 `/tmp` 메타데이터 최종화 확인이 없습니다. "
            "현재 Java 경로로 새로 빌드·시험한 최종 이미지인지 확인하고 다시 내보내세요. "
            "과거 이미지에 새 준비 단계가 적용됐다고 간주하지 않습니다."
        )
    return (
        "이 Dockerfile 이미지는 사용자가 작성한 빌드 경로를 사용합니다. "
        "`/tmp` 디렉터리 권한과 필요한 VOLUME 선언은 사용자 준비 조건이며, "
        "VOLUME 선언만으로 쓰기 권한이 증명되지 않습니다."
    )


def readme_first(spec: DeploymentSpec, artifact: ImageArtifact) -> str:
    mode = "HTTP 실습용 / 실서비스 보안 미구현" if spec.exposure_mode == "http_demo" else "기존 인증서 HTTPS"
    return f"""# AWS 실행 준비 결과

로컬에서 만든 이미지를 선택 조건으로 HTTP 시험했습니다. **AWS 실제 동작은 미검증입니다.**
프로필: `{spec.profile}` / 템플릿: `{spec.template_version}` / 공개 방식: **{mode}**

1. `AWS_SPEC.md`, `LOCAL_TEST_RESULT.md`, `REQUIRED_INPUTS.md`를 검토합니다.
2. `MANUAL_AWS_RUNBOOK.md` 순서대로 registry 준비 → 같은 이미지 업로드 →
   ECR manifest digest 확인 → service 검토·적용을 사용자가 별도 수행합니다.
3. 학교 계정 결과를 `AWS_VERIFICATION_RECORD.md`에 기록합니다.

이 프로그램은 AWS 인증/API/이미지 push/Terraform plan·apply·destroy를 실행하지 않습니다.
`terraform.tfvars.example`은 문서용 예시이며 자동 적용되는 입력이 아닙니다.
`terraform.tfvars.json`은 현재 확인된 비민감 값만 포함합니다. 런타임 조건 변경은 재시험이 필요합니다.

로컬 image ID: `{artifact.image_id}`
이 값은 **ECR manifest digest와 다릅니다.** 실제 push 후 조회한 digest만 service에 입력합니다.
작은 ZIP에는 이미지 tar가 없습니다. 선택적으로 저장한 tar에는 앱 코드·설정이 들어갈 수 있습니다.

**AWS에서 /tmp mount의 non-root 쓰기 권한과 앱 시작은 추가 확인이 필요합니다.**
Fargate의 task-scoped volume은 로컬 mode=1777 tmpfs와 같지 않습니다.
Java 경로는 Paketo 이미지의 USER·ENTRYPOINT/CMD·rootfs layer를 보존하고
최종 이미지에 `VOLUME ["/tmp"]` 메타데이터를 준비합니다.
로컬 시험·tar·수동 ECR 업로드에는 이 준비가 끝난 최종 image ID를 사용합니다.
Dockerfile 경로의 /tmp 권한은 사용자가 준비할 조건이며, VOLUME 선언만으로 쓰기 권한이 증명되지 않습니다.
이미지 메타데이터 준비와 로컬 시험은 실제 AWS volume의 권한·동작 검증과 구분합니다.
{_tmp_image_note(artifact)}
템플릿이 생성됐다는 사실만으로 AWS에서 앱이 시작된다고 보장하지 않습니다.
"""


def aws_spec(spec: DeploymentSpec, artifact: ImageArtifact) -> str:
    size = PRESETS[spec.preset]
    zones = ", ".join(spec.availability_zones) or "사용자가 확인할 2개 AZ (미입력)"
    mode = (
        "HTTP 실습용 / 실서비스 보안 미구현"
        if spec.exposure_mode == "http_demo" else "기존 ACM 인증서 HTTPS"
    )
    role = (
        "execution role 1개와 inline policy 1개 생성"
        if spec.create_execution_role else "기존 execution role 사용, 수정·삭제 없음"
    )
    return f"""# AWS 자원 사양

가격 산정 자료가 아니라 인프라 사양입니다. 별도 비용 검토가 필요합니다.

| 항목 | 값 |
|---|---|
| 프로필 / 템플릿 | {spec.profile} / {spec.template_version} |
| 리전 / AZ | {spec.region} / {zones} |
| 선택 크기 | {spec.preset} |
| vCPU / 메모리 MiB / Fargate CPU units | {size.vcpu:g} / {size.memory_mib} / {size.cpu_units} |
| desired_count | 1, 자동 확장 없음 |
| 이미지 플랫폼 | Linux / amd64 → Fargate LINUX / X86_64 |
| 컨테이너 포트 / HTTP health path | {spec.container_port} / `{spec.health_path}` |
| 이미지 USER | `{artifact.user}` |
| 공개 방식 | {mode} |
| 로그 | CloudWatch Logs group 1개, 보존 7일 |
| 이미지 저장소 | 새 전용 ECR 1개, immutable tag, AES256, scan on push, force_delete=false |
| IAM | {role}; 앱 task role 없음 |
| 네트워크 | VPC 1개, public subnet 2개, IGW 1개, route table 1개, 연결 2개 |
| ALB | 인터넷 ALB 1개, IP target group 1개; HTTPS는 listener 2개, HTTP demo는 1개 |
| 보안그룹 | ALB 1개 + task 1개; task 수신은 ALB에서 앱 포트만 |
| 외부 송신 | task HTTPS 443 허용, VPC DNS 사용; 목적지 완전 격리 아님 |
| task 네트워크 | public subnet, public IP 할당; private subnet이라고 간주하지 않음 |
| 임시 저장소 | Fargate 1.4.0 기본 task 20 GiB, 이미지 저장 공간 공유, /tmp volume |
| 영구 저장 | 없음; task 교체·삭제 시 임시 파일 보존 보장 없음 |

프리셋은 성능 최적화 결과나 사용자 수 보장이 아닙니다. 로컬과 AWS 성능은 다릅니다.
desired_count=1은 고가용성이나 배포 중 task 1개만 실행됨을 보장하지 않습니다.
업데이트 중 최대 200% 설정으로 일시적으로 2개 task가 실행될 수 있습니다.

로컬 Docker의 시험 네트워크, PID 제한, no-new-privileges, /tmp 64 MiB tmpfs,
noexec/nosuid/mode=1777은 Fargate에 동일하게 복사하지 않습니다.
Fargate는 read-only root + task-scoped /tmp volume을 사용합니다.
Java 경로는 신뢰된 Paketo 결과에 `VOLUME ["/tmp"]` 메타데이터를 추가하고,
USER·ENTRYPOINT/CMD·rootfs layer 보존을 확인한 최종 이미지를 시험 대상으로 사용합니다.
이는 ECS의 같은 VOLUME/containerPath 연결 방식을 위한 이미지 준비입니다.
**AWS volume의 non-root 쓰기 권한·Java 임시 파일·앱 시작은 실제 AWS에서 미검증입니다.**
일반 Dockerfile 앱은 /tmp 디렉터리 권한을 앱 소유자가 확인해야 합니다.
VOLUME 선언이나 로컬 mode=1777 tmpfs 시험만으로 그 이미지의 AWS 쓰기 권한을 보장하지 않습니다.
권한 문제가 있으면 앱 소유자가 이미지 설계를 수정한 뒤 다시 로컬 build/test/export해야 합니다.
root 실행이나 추가 초기화 컨테이너로 권한을 우회하지 않습니다.
이미지 내부 curl health command를 추가하지 않고 ALB HTTP check를 사용합니다.

{_tmp_image_note(artifact)}

DB·영구파일·세션 공유·도메인/DNS·ACM 생성·외부 서비스 연결은 포함되지 않습니다.
실행시간, 데이터전송량, 실제 로그량, registry 사용량은 이 사양만으로 알 수 없습니다.
계정 권한·할당량·PassRole·주소 충돌·실제 AWS 동작은 아직 확인하지 않았습니다.
"""


def local_test_result(check: RuntimeCheck) -> str:
    size = PRESETS[check.conditions.preset]
    mappings = "\n".join(
        f"{entry.phase}: {redact(entry.docker_port_output, 1000).strip()}"
        for entry in check.port_evidence
    ) or "NOT_RECORDED"
    return f"""# 로컬 HTTP 시험 결과

| 항목 | 결과 |
|---|---|
| 상태 | {check.status} |
| image ID | `{check.image_id}` |
| source fingerprint | `{check.source_fingerprint}` |
| 실행조건 fingerprint | `{check.conditions_fingerprint}` |
| 크기 / vCPU / memory MiB | {size.name} / {size.vcpu:g} / {size.memory_mib} |
| 포트 / 경로 | {check.conditions.container_port} / `{check.conditions.health_path}` |
| 로컬 네트워크 방식 | {check.conditions.network_mode} |
| 실제 driver / internal | {check.network_driver} / {check.network_internal} |
| 시작 시 image ID 확인 / 컨테이너 시작 | {check.image_verified} / {check.container_started} |
| 실제 host loopback port | {check.host_port} |
| 최초 / 재시작 후 HTTP 상태 | {check.initial_http_status} / {check.restart_http_status} |
| HTTP 상태 | {check.http_status} |
| 재시작 시험 | {"PASS" if check.restart_passed else "NOT_RUN 또는 실패"} |
| 시작 / 종료 | {check.started_at} / {check.finished_at} |
| 정리 | {check.cleanup_status} |
| 차단·실패 계층 | {check.failure_layer or "없음"} |
| AWS 실제 적용 | AWS_NOT_TESTED |

{redact(check.message)}

환경: {redact(check.environment)}

실제 `docker container port` 관찰 (start / restart):

```text
{mappings}
```

일반 bridge 자체 샘플 시험은 외부 송신 격리를 검증하지 않습니다. 공개 주소는 127.0.0.1만 허용합니다.
업무 기능·로그인·DB·데이터 보존·AWS 성능은 이 HTTP 시험의 검증 범위가 아닙니다.
응답 본문은 저장하지 않습니다. 마스킹된 로그는 `local-test-result.json`에 있습니다.
"""


def required_inputs(items: list[str]) -> str:
    return "# 사용자 후속 입력\n\n" + "\n".join(f"- {item}" for item in items) + """

자격증명은 이 앱·tfvars·보고서에 넣지 마세요. 실제 tfvars/state/plan은 Git에서 제외합니다.
문서용 IP/도메인은 실제 접속값이 아닙니다. 계정과 리전, 역할 권한, 할당량은 직접 확인해야 합니다.
"""


def manual_runbook(artifact: ImageArtifact | None = None) -> str:
    image_id = artifact.image_id if artifact else "sha256:REPLACE_WITH_TESTED_LOCAL_IMAGE_ID"
    image_note = _tmp_image_note(artifact) if artifact else "실제 내보내기의 이미지 준비 기록을 확인하세요."
    return f"""# 학교 AWS 수동 검증 순서

**아래는 사용자가 별도 터미널에서 실행할 안내입니다. 프로그램과 Codex는 실행하지 않습니다.**
실제 AWS 적용은 미실시입니다. 학교 승인·권한·리전·할당량과 별도 비용 검토를 먼저 마칩니다.
HTTP demo에는 실제 사용자 데이터·로그인 비밀번호·비밀키를 전송하지 않습니다.

## 1. 계정과 신규 registry

사용자가 승인된 AWS profile을 별도 설정하고 계정·리전을 직접 확인합니다.
자격증명을 제품에 입력하지 않습니다. 아래 PowerShell 변수는 문서용 placeholder입니다.

```powershell
$env:AWS_PROFILE = 'YOUR_APPROVED_PROFILE'
$region = 'ap-northeast-2'
aws sts get-caller-identity
Set-Location terraform/registry
terraform init
terraform validate
terraform plan -out=registry.tfplan
terraform show registry.tfplan
terraform apply registry.tfplan
$repo = terraform output -raw repository_url
$repoName = terraform output -raw repository_name
$registry = ($repo -split '/')[0]
```

새 전용 ECR 이름과 리전을 먼저 `terraform.tfvars.json`에서 확인합니다.
기존 저장소를 import하거나 같은 이름의 운영 저장소를 삭제하지 않습니다.
실제 tfvars·plan·state는 민감할 수 있으므로 Git/일반 보고서에 넣지 않습니다.

## 2. 시험한 같은 이미지 업로드와 digest 확인

로컬 시험 image ID는 `{image_id}`입니다. 다른 PC라면 선택 저장한 tar를 먼저
`docker image load --input app-image.tar`로 load하고 image ID를 확인합니다.
Java 경로는 /tmp 메타데이터 준비 뒤 생성된 최종 image ID를 사용합니다.
Paketo 중간 이미지나 다른 빌드의 tag로 바꾸지 말고 실제 로컬 시험 대상과 일치하는지 확인합니다.
tar SHA256, local image ID, ECR manifest digest는 서로 다른 식별자입니다.
{image_note}

```powershell
$imageId = '{image_id}'
docker image inspect $imageId --format '{{{{.Id}}}}'
$releaseTag = 'release-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry
docker image tag $imageId "$($repo):$releaseTag"
docker image push "$($repo):$releaseTag"
$digest = aws ecr describe-images --region $region --repository-name $repoName `
    --image-ids imageTag=$releaseTag --query 'imageDetails[0].imageDigest' --output text
$imageUri = "$($repo)@$digest"
$imageUri
```

비밀번호를 출력·저장하지 않습니다. immutable tag이므로 새 release tag를 사용합니다.
image ID 확인이 다르면 중단합니다. `$imageUri`는 실제 업로드 후 ECR이 반환한 manifest digest여야 합니다.

## 3. service 입력과 검토·적용

`terraform/service/terraform.tfvars.json`은 로컬에서 시험한 비민감 값입니다.
`terraform.tfvars.example`을 참고하여 **필수 외부값만** `inputs.auto.tfvars`에 입력합니다.
실제 `image_uri`, 리전 내 가용한 서로 다른 두 AZ, 검토한 접속 CIDR을 넣습니다.
HTTPS 기본값은 같은 리전의 실제 ACM ARN과 인증서에 맞는 도메인이 필요합니다.
도메인 DNS는 `alb_dns_name`에 연결해야 합니다. ALB의 aws 도메인을 임의 인증서로 HTTPS 사용하지 않습니다.
명시적 HTTP demo 선택 시에만 인증서를 생략하고 제한된 CIDR로 HTTP 시험합니다.
기관에서 IAM 생성이 금지되면 승인된 기존 execution role ARN을 사용하고
`create_execution_role=false`로 설정합니다. 이 템플릿은 그 역할을 수정하거나 삭제하지 않습니다.
ECR pull/로그 쓰기·PassRole 등 권한이 부족하면 원인을 기록하고 기관 관리자에게 확인합니다.
권한 우회를 위해 AdministratorAccess나 광범위한 task role을 추가하지 않습니다.

```powershell
Set-Location ../service
terraform init
terraform validate
terraform plan -out=service.tfplan
terraform show service.tfplan
terraform apply service.tfplan
terraform output
```

plan에서 새 VPC/public subnet 2개/ALB/ECS task/public IP/로그/IAM 수량을 검토합니다.
기존 자원의 교체·삭제가 보이면 적용을 중단합니다.
포트·preset·환경·image가 바뀌면 기존 로컬 시험 결과를 재사용하지 않습니다.

## 4. AWS에서 확인할 실제 결과

ECS task 시작 상태, 실제 image digest, 시작 로그, ALB target health와 지정 HTTP 경로를 확인합니다.
**Fargate /tmp volume은 로컬 mode=1777 tmpfs와 달라 non-root 쓰기 권한이 미검증입니다.**
Java 경로는 USER·ENTRYPOINT/CMD·rootfs layer를 보존하면서 최종 이미지에
`VOLUME ["/tmp"]`를 준비합니다. 이미지의 /tmp와 task definition의 containerPath를 연결하는
이 준비가 실제 AWS에서 기대한 권한으로 동작하는지 확인해야 합니다.
일반 Dockerfile 앱은 /tmp 디렉터리 권한과 필요한 VOLUME 선언을 앱 소유자가 검토합니다.
VOLUME 선언만으로 권한이 증명되지 않으며, 자체 샘플의 권한 관찰을 다른 앱에 일반화하지 않습니다.
Java 임시 파일이나 앱 시작 실패 시 로그를 검토하고, 이미지 수정이 필요하면 새 이미지로 재시험합니다.
root 실행이나 숨은 초기화 컨테이너를 자동 추가하지 않습니다.
가능하면 task 교체 후 같은 digest로 HTTP 응답을 재시험합니다. 데이터 보존까지 확인한 것은 아닙니다.
`AWS_VERIFICATION_RECORD.md`에 일시·조건·digest·검사 범위·결과·실패 원인을 기록합니다.

## 5. 정리 계획 검토

먼저 service를 정리하고, 그다음 **이 실습용 repository의 image만** 검토·삭제한 뒤 registry를 정리합니다.
다음 destroy plan은 사용자만 별도로 실행합니다. 적용 전 삭제 대상을 반드시 확인합니다.

```powershell
# In terraform/service
terraform plan -destroy -out=service-destroy.tfplan
terraform show service-destroy.tfplan
terraform apply service-destroy.tfplan
# Review/delete only this lab repository's images in the AWS console.
Set-Location ../registry
terraform plan -destroy -out=registry-destroy.tfplan
terraform show registry-destroy.tfplan
terraform apply registry-destroy.tfplan
```

ECR `force_delete=false`라서 image가 남으면 registry 삭제가 거부될 수 있습니다.
전체 계정 또는 공유 저장소를 정리하지 말고 해당 실습 repo와 image를 재확인합니다.
기존 execution role·기존 인증서는 소유자의 정책에 따르며 이 템플릿이 삭제하지 않습니다.
남은 ALB/ENI/log/ECR/기타 자원도 확인합니다. task 종료만으로 모든 비용이 중단되는 것은 아닙니다.
"""
