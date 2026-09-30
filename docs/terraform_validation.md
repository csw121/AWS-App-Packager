# Terraform 템플릿 개발 검증

프로그램의 사용자 경로는 Terraform CLI를 실행하지 않습니다. 개발팀 전용 명령:

```powershell
.venv\Scripts\python.exe devtools\validate_templates.py
.venv\Scripts\python.exe devtools\validate_templates.py --run-cli
```

첫 명령은 정적 정책 검사만 합니다. 두 번째는 저장된 SHA256 allowlist와 일치하는
직접 작성한 `.tf`/`.tftest.hcl`만 새 작업 디렉터리에 복사합니다. 사용자 경로를 입력받지 않으며
다른 `.tf.json`, `.tftest.json`, `.auto.tfvars`는 복사하지 않습니다.
프리셋 JSON은 `src/aws_app_packager/presets.py`의 PRESETS에서 생성합니다.
변경한 템플릿은 내용을 재검토하고 `devtools/trusted_template_hashes.json`을 갱신해야 합니다.

실행 범위는 `fmt -check -diff -recursive`, `init -backend=false -input=false`, `validate`,
`test`입니다. 모든 test run은 `command = plan`을 명시하며 유일한 AWS provider 전체를 mock합니다.
provider alias/data source/외부 module/provisioner/backend는 허용하지 않습니다.
ProcessRunner가 AWS credentials/profile 및 호스트 HOME을 상속하지 않고
프로젝트 전용 HOME·Terraform 설정 파일을 사용합니다. AWS metadata 접근도 비활성화합니다.
provider 다운로드는 네트워크 정책을 따릅니다. 실제 AWS plan/apply/destroy는 실행하지 않습니다.

2026-09-29 Windows에서 Terraform **1.14.8**, AWS provider **6.14.0**으로
두 root의 fmt/init/validate와 **registry 1개 + service 10개 명시적 mock plan**을 통과했습니다.
정확한 명령·결과·템플릿 hash는 `docs/template_validation.json`에 기록되어 있습니다.
테스트는 CPU/RAM/포트/digest image/USER/health path/SG/TLS/신규·기존 IAM과
잘못된 HTTP 공개·인증서·role·UID·image 입력의 거부를 확인합니다.
이것은 AWS 실제 적용 검증이나 사용자별 bundle CLI 검증이 아닙니다.
생성 bundle은 항상 `terraform_cli_status=NOT_RUN_FOR_THIS_BUNDLE`, `aws_status=AWS_NOT_TESTED`입니다.

공식 근거 (2026-09-29 확인):

- [AWS provider 6.14.0 ECS service 문서](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/v6.14.0/website/docs/r/ecs_service.html.markdown)
- [Terraform provider mocking](https://developer.hashicorp.com/terraform/language/tests/mocking)
- [Terraform validate](https://developer.hashicorp.com/terraform/cli/commands/validate)
- [Fargate ephemeral storage](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-storage.html)
- [ECS bind mounts 및 기본 소유권](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html)

Fargate task volume은 로컬 /tmp mode=1777 tmpfs와 동일하지 않습니다.
Java 경로는 Paketo 이미지의 USER·ENTRYPOINT/CMD·rootfs layer를 보존하고
`VOLUME ["/tmp"]` 메타데이터를 준비한 최종 이미지를 로컬 시험 대상으로 사용합니다.
자체 Dockerfile 샘플도 `/tmp`를 선언합니다. Terraform은 동일한 `/tmp` containerPath에
task-scoped volume을 연결합니다. 이 HCL 구조의 validate/mock 성공은 이미지 준비나
실제 Fargate volume 권한·앱 시작을 검증한 결과가 아닙니다.
일반 Dockerfile 앱은 디렉터리 권한이 별도 사용자 조건이며 VOLUME 선언만으로 권한이 증명되지 않습니다.
서비스 시작·mount 권한은 학교 계정의 별도 실제 검증이 필요하며, 코드가 권한 상승으로 우회하지 않습니다.
