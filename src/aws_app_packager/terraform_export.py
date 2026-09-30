"""Offline template export and optional disk-streamed local image save."""

import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, reports
from .models import DeploymentSpec, ExportBundle, ImageArtifact, RuntimeCheck, fingerprint, utc_now
from .presets import PRESETS
from .redaction import redact

PROVIDER_VERSION = "6.14.0"
TEMPLATE_FILES = ("versions.tf", "main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example")
MAX_SMALL_BUNDLE_BYTES = 2 * 1024 * 1024


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def preset_json() -> str:
    """The HCL runtime reads this generated data; it has no second preset table."""
    return _json({
        name: {"cpu": size.cpu_units, "memory": size.memory_mib, "desired_count": size.desired_count}
        for name, size in PRESETS.items()
    })


def _raise_if_cancelled(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise RuntimeError("이미지 tar 저장이 취소되었습니다.")


def file_sha256(path: Path, *, cancel=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            _raise_if_cancelled(cancel)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            _raise_if_cancelled(cancel)
            digest.update(chunk)
    _raise_if_cancelled(cancel)
    return digest.hexdigest()


def _no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.exists():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("출력 경로에 symlink/junction/reparse point를 사용할 수 없습니다.")


def _owned_output(path: Path) -> Path:
    root = config.EXPORT_DIR.absolute()
    _no_links(root)
    _no_links(path.absolute())
    candidate = path.resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise ValueError("내보내기는 도구 소유 exports 폴더 안에서만 저장할 수 있습니다.")
    return candidate


def validate_template_structure() -> dict[str, str]:
    """Bounded static policy check, explicitly not an HCL parser or CLI validation."""
    hashes: dict[str, str] = {}
    for root in ("registry", "service"):
        for name in TEMPLATE_FILES:
            path = config.TEMPLATE_DIR / root / name
            _no_links(path)
            text = path.read_text(encoding="utf-8")
            if len(text.encode("utf-8")) > 100_000:
                raise ValueError("템플릿 크기 제한 초과")
            if name.endswith(".tf"):
                forbidden = (
                    r'(?m)^\s*(?:provisioner|backend|module|data)\s+"|'
                    r'\b(?:local-exec|remote-exec|user_data|access_key|secret_key)\b'
                )
                if re.search(forbidden, text):
                    raise ValueError("검토 범위를 벗어난 Terraform 블록입니다.")
                if any(item != "aws" for item in re.findall(r'provider\s+"([^"]+)"', text)):
                    raise ValueError("AWS 외 provider는 허용되지 않습니다.")
            hashes[f"{root}/{name}"] = file_sha256(path)
        versions = (config.TEMPLATE_DIR / root / "versions.tf").read_text(encoding="utf-8")
        if f'"= {PROVIDER_VERSION}"' not in versions or '"hashicorp/aws"' not in versions:
            raise ValueError("고정된 provider 버전이 변경되었습니다.")
    hashes["service/presets.json"] = hashlib.sha256(preset_json().encode("utf-8")).hexdigest()
    return hashes


def _assert_test_matches(artifact: ImageArtifact, check: RuntimeCheck, spec: DeploymentSpec) -> None:
    if (
        check.status != "PASS" or check.http_status != 200 or not check.finished_at
        or check.job_id != artifact.job_id or check.image_id != artifact.image_id
        or check.source_fingerprint != artifact.source_fingerprint
        or check.conditions_fingerprint != fingerprint(check.conditions)
        or spec.template_version != config.TEMPLATE_VERSION
        or (check.conditions.restart and not check.restart_passed)
        or check.cleanup_status != "CLEANED"
        or (spec.image_os, spec.image_architecture) != (artifact.os, artifact.architecture)
    ):
        raise ValueError("이미지·소스·실행조건이 일치하는 실제 로컬 HTTP 통과 결과가 필요합니다.")
    for field in ("preset", "container_port", "health_path", "environment"):
        if getattr(check.conditions, field) != getattr(spec, field):
            raise ValueError("실행조건이 변경되었습니다. 선택 조건으로 다시 시험하세요.")
    started = datetime.fromisoformat(check.started_at)
    finished = datetime.fromisoformat(check.finished_at)
    if started < datetime.fromisoformat(artifact.created_at) or finished < started:
        raise ValueError("시험 시각이 이미지 생성 시각과 일치하지 않습니다.")
    if not re.fullmatch(r"[1-9][0-9]*(?::[0-9]+)?", artifact.user):
        raise ValueError("검증된 숫자 nonzero image UID와 선택적인 숫자 GID가 필요합니다.")


def _external_inputs(spec: DeploymentSpec) -> list[str]:
    required = ["승인된 학교 AWS 계정·profile, 권한·PassRole·할당량·주소 충돌 및 별도 비용 검토"]
    if len(spec.allowed_cidrs) > 20:
        raise ValueError("접속 CIDR은 최대 20개입니다.")
    for cidr in spec.allowed_cidrs:
        network = ipaddress.ip_network(cidr, strict=True)
        if network.version != 4 or network.prefixlen == 0:
            raise ValueError("제한된 IPv4 접속 CIDR이 필요합니다. /0은 허용하지 않습니다.")
    if not spec.allowed_cidrs:
        if spec.exposure_mode == "http_demo":
            raise ValueError("HTTP 실습용 모드에는 제한된 접속 CIDR을 명시해야 합니다.")
        required.append("allowed_cidrs: 검토한 접속자의 제한된 IPv4 CIDR 목록")
    if spec.availability_zones:
        if len(spec.availability_zones) != 2 or len(set(spec.availability_zones)) != 2 or any(
            not re.fullmatch(re.escape(spec.region) + "[a-z]", item) for item in spec.availability_zones
        ):
            raise ValueError("선택 리전의 서로 다른 2개 AZ 이름이 필요합니다.")
    else:
        required.append("availability_zones: 선택 리전에서 사용할 서로 다른 AZ 2개")
    if spec.image_uri:
        pattern = (
            r"[0-9]{12}\.dkr\.ecr\." + re.escape(spec.region)
            + r"\.amazonaws\.com/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[a-f0-9]{64}"
        )
        if not re.fullmatch(pattern, spec.image_uri):
            raise ValueError("실제 push 후 확인한 같은 리전의 ECR manifest digest URI만 허용합니다.")
    else:
        required.append("image_uri: registry 준비 → 같은 이미지 수동 push → 실제 ECR manifest digest 확인")
    if spec.create_execution_role and spec.existing_execution_role_arn:
        raise ValueError("새 역할 생성과 기존 역할 사용을 동시에 지정할 수 없습니다.")
    if spec.existing_execution_role_arn and not re.fullmatch(
        r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]+", spec.existing_execution_role_arn
    ):
        raise ValueError("기존 execution role ARN 형식을 확인하세요.")
    if not spec.create_execution_role and not spec.existing_execution_role_arn:
        required.append("existing_execution_role_arn: 기관에서 승인한 기존 execution role ARN")
    if spec.certificate_arn and not re.fullmatch(
        r"arn:aws:acm:" + re.escape(spec.region) + r":[0-9]{12}:certificate/[a-f0-9-]+", spec.certificate_arn
    ):
        raise ValueError("선택 리전의 실제 ACM 인증서 ARN이 필요합니다.")
    if spec.domain_name and not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", spec.domain_name
    ):
        raise ValueError("인증서에 맞는 도메인 이름을 확인하세요.")
    if spec.exposure_mode == "https_existing_certificate":
        if not spec.certificate_arn:
            required.append("certificate_arn: 같은 리전의 준비된 ACM 인증서 ARN")
        if not spec.domain_name:
            required.append("domain_name: 인증서와 일치하는 도메인 및 수동 DNS 연결")
    elif spec.certificate_arn or spec.domain_name:
        raise ValueError("HTTP 실습용 모드에는 사용하지 않는 인증서·도메인을 넣지 마세요.")
    required.append("Fargate /tmp ephemeral volume의 non-root 쓰기 권한, task 시작과 ALB HTTP 실제 검증")
    return required


def export_bundle(artifact: ImageArtifact, check: RuntimeCheck, spec: DeploymentSpec) -> ExportBundle:
    """Create a small offline bundle only for matching successful local test evidence."""
    _assert_test_matches(artifact, check, spec)
    external_inputs = _external_inputs(spec)
    template_hashes = validate_template_structure()
    suffix = uuid.uuid4().hex[:8]
    directory = _owned_output(config.EXPORT_DIR / f"{spec.project_label}-{artifact.job_id}-{suffix}")
    staging = _owned_output(config.EXPORT_DIR / f".export-{suffix}.tmp")
    staging.mkdir(parents=True, exist_ok=False)
    zip_path = directory.with_suffix(".zip")
    zip_temp = staging.parent / f".export-{suffix}.zip.tmp"
    try:
        for root in ("registry", "service"):
            target = staging / "terraform" / root
            target.mkdir(parents=True)
            for name in TEMPLATE_FILES:
                shutil.copyfile(config.TEMPLATE_DIR / root / name, target / name)
        (staging / "terraform/service/presets.json").write_text(preset_json(), encoding="utf-8")
        variables = {
            "project_name": spec.project_label, "aws_region": spec.region, "preset": spec.preset,
            "container_port": spec.container_port, "health_path": spec.health_path,
            "expected_status_code": check.conditions.expected_status, "environment": spec.environment,
            "container_user": artifact.user, "exposure_mode": spec.exposure_mode,
            "create_execution_role": spec.create_execution_role,
        }
        for field in (
            "allowed_cidrs", "availability_zones", "image_uri", "certificate_arn",
            "domain_name", "existing_execution_role_arn",
        ):
            if value := getattr(spec, field):
                variables[field] = value
        (staging / "terraform/service/terraform.tfvars.json").write_text(_json(variables), encoding="utf-8")
        registry = {
            "aws_region": spec.region, "repository_name": f"{spec.project_label}-{artifact.job_id[:8]}",
        }
        (staging / "terraform/registry/terraform.tfvars.json").write_text(_json(registry), encoding="utf-8")
        # Never export personal absolute paths, raw builder output, or a host context manifest.
        image_data = artifact.model_dump(mode="json", exclude={"manifest_path"})
        image_data["builder_info"] = redact(artifact.builder_info)
        image_data["tool_versions"] = {key: redact(value) for key, value in artifact.tool_versions.items()}
        image_data["ecr_manifest_digest"] = "NOT_KNOWN_UNTIL_MANUAL_PUSH"
        runtime_data = check.model_dump(mode="json")
        for key in ("logs", "message", "environment"):
            runtime_data[key] = redact(runtime_data[key])
        runtime_data["limitations"] = [redact(item) for item in check.limitations]
        docs = {
            "README_FIRST.md": reports.readme_first(spec, artifact),
            "AWS_SPEC.md": reports.aws_spec(spec, artifact),
            "deployment-spec.json": _json(spec.model_dump(mode="json")),
            "LOCAL_TEST_RESULT.md": reports.local_test_result(check),
            "local-test-result.json": _json(runtime_data),
            "REQUIRED_INPUTS.md": reports.required_inputs(external_inputs),
            "MANUAL_AWS_RUNBOOK.md": reports.manual_runbook(artifact),
            "AWS_VERIFICATION_RECORD.md": reports.VERIFICATION_RECORD,
            "image/image.json": _json(image_data),
        }
        for name, contents in docs.items():
            file = staging / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(contents, encoding="utf-8")
        files = {
            path.relative_to(staging).as_posix(): file_sha256(path)
            for path in staging.rglob("*") if path.is_file()
        }
        manifest = {
            "schema_version": "0.1.0", "profile": spec.profile, "template_version": spec.template_version,
            "created_at": utc_now(), "source_fingerprint": artifact.source_fingerprint,
            "image_id": artifact.image_id, "conditions_fingerprint": check.conditions_fingerprint,
            "files": files, "template_sha256": template_hashes, "external_inputs": external_inputs,
            "image_status": "BUILT", "local_status": "PASS", "template_status": "STATIC_CHECKED",
            "terraform_cli_status": "NOT_RUN_FOR_THIS_BUNDLE", "aws_status": "AWS_NOT_TESTED",
            "terraform_cli_history": "See project docs/template_validation.json; not bundle CLI validation.",
            "provider_version": PROVIDER_VERSION,
        }
        (staging / "manifest.json").write_text(_json(manifest), encoding="utf-8")
        files["manifest.json"] = file_sha256(staging / "manifest.json")
        if sum((staging / name).stat().st_size for name in files) > MAX_SMALL_BUNDLE_BYTES:
            raise ValueError("문서/Terraform ZIP 크기 제한을 초과했습니다.")
        with zipfile.ZipFile(zip_temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                if ".." in Path(name).parts or name.startswith(("/", "\\")):
                    raise ValueError("ZIP 경로 경계 오류")
                archive.write(staging / name, arcname=name)
        os.replace(staging, directory)
        os.replace(zip_temp, zip_path)
        return ExportBundle(
            directory=directory, zip_path=zip_path, files=files, external_inputs=external_inputs,
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        zip_temp.unlink(missing_ok=True)
        raise


def save_image_tar(artifact: ImageArtifact, destination: Path, runner=None, cancel=None,
                   on_progress=None, on_stage=None) -> dict[str, object]:
    """Stream docker image save to a temporary disk file, never into a UI download buffer."""
    from .builders.base import assert_same_image
    from .preflight import docker_arguments, require_local_docker, tool_environment
    from .process_runner import ProcessRunner

    _raise_if_cancelled(cancel)
    runner = runner or ProcessRunner()
    destination = _owned_output(Path(destination))
    metadata_path = destination.with_suffix(".tar.json")
    if destination.suffix.lower() != ".tar" or destination.exists() or metadata_path.exists():
        raise ValueError("새 .tar 출력 경로가 필요합니다. 기존 파일을 덮어쓰지 않습니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A conservative headroom check, not a prediction of the exact tar size.
    if shutil.disk_usage(destination.parent).free < max(artifact.size_bytes * 2, 64 * 1024 * 1024):
        raise ValueError(
            "이미지 저장을 위한 디스크 여유 공간이 부족합니다. tar 크기는 정확히 예측할 수 없습니다."
        )
    state = require_local_docker(runner=runner)
    assert_same_image(artifact, runner=runner, state=state)
    _raise_if_cancelled(cancel)
    token = uuid.uuid4().hex
    temporary = destination.with_name(f".{destination.name}.{token}.tmp")
    metadata_temporary = metadata_path.with_name(f".{metadata_path.name}.{token}.tmp")
    try:
        result = runner.run(
            docker_arguments(state, "image", "save", "--output", str(temporary), artifact.image_id),
            timeout=600, env=tool_environment(state), cancel=cancel, on_progress=on_progress,
        )
        if result.returncode != 0 or result.timed_out or result.cancelled:
            raise RuntimeError("이미지 tar 저장 실패: " + redact(result.output, 2000))
        _raise_if_cancelled(cancel)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("Docker가 완료된 이미지 tar 파일을 만들지 못했습니다.")
        if on_stage:
            on_stage("이미지 파일 SHA256 확인")
        digest = file_sha256(temporary, cancel=cancel)
        metadata = {
            "filename": destination.name, "size_bytes": temporary.stat().st_size,
            "sha256": digest, "local_image_id": artifact.image_id, "created_at": utc_now(),
            "ecr_manifest_digest": "NOT_KNOWN_UNTIL_MANUAL_PUSH",
        }
        metadata_temporary.write_text(_json(metadata), encoding="utf-8")
        if on_stage:
            on_stage("이미지 파일 저장 확정")
        _raise_if_cancelled(cancel)
        # Commit the prepared pair only after the final cancellation gate. No
        # cancellation checks in this short publish section can leave half a pair.
        os.replace(temporary, destination)
        os.replace(metadata_temporary, metadata_path)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)
        metadata_temporary.unlink(missing_ok=True)
