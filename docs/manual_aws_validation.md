# 학교 AWS 수동 검증 순서

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

로컬 시험 image ID는 `sha256:REPLACE_WITH_TESTED_LOCAL_IMAGE_ID`입니다. 다른 PC라면 선택 저장한 tar를 먼저
`docker image load --input app-image.tar`로 load하고 image ID를 확인합니다.
Java 경로는 /tmp 메타데이터 준비 뒤 생성된 최종 image ID를 사용합니다.
Paketo 중간 이미지나 다른 빌드의 tag로 바꾸지 말고 실제 로컬 시험 대상과 일치하는지 확인합니다.
tar SHA256, local image ID, ECR manifest digest는 서로 다른 식별자입니다.
실제 내보내기의 이미지 준비 기록을 확인하세요.

```powershell
$imageId = 'sha256:REPLACE_WITH_TESTED_LOCAL_IMAGE_ID'
docker image inspect $imageId --format '{{.Id}}'
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

---

# AWS 수동 검증 기록 — AWS_NOT_TESTED

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
