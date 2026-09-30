"""Shared image verification, with no mock success path in the application."""
import json
import uuid
from typing import Protocol

from ..models import BuildPlan, ImageArtifact, utc_now
from ..preflight import docker_arguments, require_local_docker, tool_environment
from ..process_runner import ProcessRunner
from ..project_input import verify_plan


class BuildError(RuntimeError):
    def __init__(self, message: str, *, logs: str = "", status: str = "FAIL"):
        super().__init__(message)
        self.logs = logs
        self.status = status


class BuildAdapter(Protocol):
    def build_arguments(self, plan, state, tag: str) -> list[str]: ...


def _atomic_text(path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(text)
    temporary.replace(path)


def inspect_image(reference: str, runner, state) -> dict:
    # No image environment variables, credentials or full command in inspection logs.
    template = ('{"id":{{json .Id}},"os":{{json .Os}},'
                '"architecture":{{json .Architecture}},"size":{{json .Size}},'
                '"user":{{json (index .Config "User")}},'
                '"rootfs_layers":{{json .RootFS.Layers}},'
                '"onbuild_count":{{if (index .Config "OnBuild")}}'
                '{{len (index .Config "OnBuild")}}{{else}}0{{end}},'
                '"volumes":[{{$separator := ""}}'
                '{{range $path, $_ := (index .Config "Volumes")}}'
                '{{$separator}}{{json $path}}{{$separator = ","}}{{end}}]}')
    response = runner.run(docker_arguments(state, "image", "inspect", "--format", template,
                                          reference), timeout=30, env=tool_environment(state))
    if response.returncode:
        raise BuildError("생성한 이미지의 실제 식별정보를 확인할 수 없습니다.", logs=response.output)
    try:
        value = json.loads(response.output.strip())
        if value["os"] != "linux" or value["architecture"] != "amd64":
            raise BuildError("생성 이미지는 지원하는 Linux/amd64 플랫폼이 아닙니다.")
        if not isinstance(value["size"], int) or value["size"] < 0:
            raise ValueError("size")
        if not isinstance(value["volumes"], list) or any(not isinstance(p, str) for p in value["volumes"]):
            raise ValueError("volumes")
        return value
    except (KeyError, ValueError, TypeError) as exc:
        raise BuildError("이미지 inspect 결과를 안전하게 해석하지 못했습니다.") from exc


def assert_same_image(artifact: ImageArtifact, runner=None, state=None) -> dict:
    runner = runner or ProcessRunner()
    state = state or require_local_docker(runner)
    inspected = inspect_image(artifact.local_tag, runner, state)
    if inspected["id"] != artifact.image_id:
        raise BuildError("이미지 태그가 다른 image ID를 가리킵니다. 다시 빌드·시험하세요.", status="STALE")
    return inspected


def build_image(plan: BuildPlan, cancel=None, on_stage=None, *, runner=None,
                on_progress=None) -> ImageArtifact:
    from . import dockerfile, paketo_java

    runner = runner or ProcessRunner()
    verify_plan(plan)
    if on_stage:
        on_stage("실행 도구 확인")
    state = require_local_docker(runner)
    tag = f"aws-app-packager/{plan.project_label}:{plan.job_id}"
    build_tag = tag
    if cancel and cancel.is_set():
        raise BuildError("이미지 생성이 취소되었습니다.", status="CANCELLED")
    if on_stage:
        on_stage("이미지 생성")
    if plan.method == "paketo_java":
        if not state["pack_ready"]:
            raise BuildError("공식 pack CLI를 먼저 준비해야 합니다.", status="BLOCKED")
        # Absolute explicit descriptor prevents project.toml automatic discovery.
        descriptor = plan.manifest_path.parent / "pack-controlled.toml"
        descriptor.write_text('[ _ ]\nschema-version = "0.2"\n', encoding="utf-8")
        build_tag = f"aws-app-packager/{plan.project_label}-intermediate:{plan.job_id}"
        args = paketo_java.build_arguments(plan, state, build_tag, descriptor)
    else:
        args = dockerfile.build_arguments(plan, state, tag)
    verify_plan(plan)
    response = runner.run(args, cwd=plan.snapshot, timeout=1800, cancel=cancel,
                          env=tool_environment(state), on_progress=on_progress)
    # Preserve bounded, already-redacted command evidence even if later inspect
    # fails. A zero process exit alone is not an ImageArtifact or completed build.
    _atomic_text(plan.manifest_path.parent / "build.log", response.output)
    evidence = {
        "job_id": plan.job_id, "method": plan.method, "source_fingerprint": plan.source_fingerprint,
        "local_tag": tag, "build_tag": build_tag, "returncode": response.returncode,
        "cancelled": response.cancelled, "timed_out": response.timed_out,
        "command_finished_at": utc_now(), "image_inspect_status": "NOT_RUN",
        "tool_versions": {"docker": state["docker_version"], "pack": state["pack_version"]},
    }
    _atomic_text(plan.manifest_path.parent / "build-result.json", json.dumps(evidence, indent=2))
    if response.cancelled:
        raise BuildError("이미지 생성이 취소되었습니다.", logs=response.output, status="CANCELLED")
    if response.timed_out:
        raise BuildError("이미지 생성 제한 시간을 초과했습니다.", logs=response.output)
    if response.returncode:
        raise BuildError("이미지 생성에 실패했습니다. 빌드 로그의 원인을 수정한 뒤 재시도하세요.",
                         logs=response.output)
    verify_plan(plan)
    try:
        if on_stage:
            on_stage("생성 이미지 식별정보 확인")
        inspected = inspect_image(build_tag, runner, state)
        if plan.method == "paketo_java":
            if on_stage:
                on_stage("이미지 임시 쓰기 영역 메타데이터 준비")
            inspected, finalization, finalization_log = paketo_java.finalize_image(
                plan, state, build_tag, inspected, tag, runner, cancel=cancel, on_progress=on_progress,
            )
            evidence["finalization"] = finalization
            _atomic_text(plan.manifest_path.parent / "finalization.log", finalization_log)
    except BuildError as exc:
        evidence["image_inspect_status"] = "FAIL"
        _atomic_text(plan.manifest_path.parent / "build-result.json", json.dumps(evidence, indent=2))
        exc.logs = (response.output + "\n" + exc.logs)[-65536:]
        raise
    artifact = ImageArtifact(
        job_id=plan.job_id, project_label=plan.project_label, build_method=plan.method,
        source_fingerprint=plan.source_fingerprint, local_tag=tag, image_id=inspected["id"],
        os=inspected["os"], architecture=inspected["architecture"],
        size_bytes=inspected["size"], user=inspected["user"] or "",
        builder_info=(plan.builder + " | paketo_tmp_volume_v1") if plan.method == "paketo_java"
        else "approved Dockerfile snapshot",
        tool_versions={"docker": state["docker_version"], "pack": state["pack_version"]},
        manifest_path=plan.manifest_path,
    )
    _atomic_text(plan.manifest_path.parent / "image.json", artifact.model_dump_json(indent=2))
    evidence["image_inspect_status"] = "PASS"
    evidence["image_id"] = artifact.image_id
    _atomic_text(plan.manifest_path.parent / "build-result.json", json.dumps(evidence, indent=2))
    return artifact
