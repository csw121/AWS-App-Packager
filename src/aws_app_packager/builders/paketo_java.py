"""Only the configured official builder and our own empty project descriptor."""
import hashlib
import re

from ..config import PAKETO_BUILDER, PAKETO_RUN_IMAGE
from ..preflight import docker_arguments, tool_environment


def build_arguments(plan, state, tag: str, descriptor) -> list[str]:
    if plan.builder != PAKETO_BUILDER:
        raise ValueError("검토되지 않은 builder는 사용할 수 없습니다.")
    return [state["pack_path"], "build", tag, "--path", str(plan.snapshot),
            "--builder", PAKETO_BUILDER, "--run-image", PAKETO_RUN_IMAGE,
            "--descriptor", str(descriptor), "--platform", "linux/amd64",
            "--env", "BP_JVM_VERSION=21", "--trust-builder", "--no-color",
            "--pull-policy", "if-not-present"]


def finalize_image(plan, state, intermediate_tag, intermediate, final_tag, runner, *, cancel=None,
                   on_progress=None):
    """Metadata only: inherit the exact config/rootfs and declare the existing /tmp.

    No RUN, USER, ENTRYPOINT, CMD, ENV, source copy, downloads, or app execution.
    The reviewed pinned Jammy run image's /tmp mode 1777 is separately recorded
    by an authored-sample diagnostic, not claimed as actual AWS verification.
    """
    from .base import BuildError, inspect_image

    source_id = intermediate["id"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", source_id):
        raise BuildError("중간 이미지 ID 형식을 확인할 수 없습니다.")
    if intermediate["onbuild_count"] or intermediate["volumes"] not in ([], ["/tmp"]):
        raise BuildError("Paketo 중간 이미지의 ONBUILD 또는 추가 volume을 허용하지 않습니다.")
    if not re.fullmatch(r"[0-9]+(?::[0-9]+)?", intermediate["user"] or ""):
        raise BuildError("Paketo 중간 이미지의 숫자 USER를 확인할 수 없습니다.")
    if int(intermediate["user"].split(":")[0]) == 0:
        raise BuildError("Paketo 중간 이미지가 root USER입니다.")
    if inspect_image(intermediate_tag, runner, state)["id"] != source_id:
        raise BuildError("중간 이미지 태그가 변경되었습니다.", status="STALE")
    context = plan.manifest_path.parent / "image-finalization"
    context.mkdir(exist_ok=False)
    dockerfile = f'FROM {source_id}\nVOLUME ["/tmp"]\n'
    (context / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    args = docker_arguments(
        state, "build", "--platform", "linux/amd64",
        "--pull=false", "--network", "none",
        "--label", f"io.aws-app-packager.job={plan.job_id}",
        "--label", "io.aws-app-packager.owner=v0.1", "--tag", final_tag,
        "--file", str(context / "Dockerfile"), str(context),
    )
    response = runner.run(args, cwd=context, timeout=120, cancel=cancel, env=tool_environment(state),
                          on_progress=on_progress)
    if response.returncode or response.timed_out or response.cancelled:
        raise BuildError("Paketo 이미지의 임시 쓰기 메타데이터 준비에 실패했습니다.",
                         logs=response.output, status="CANCELLED" if response.cancelled else "FAIL")
    final = inspect_image(final_tag, runner, state)
    if (inspect_image(intermediate_tag, runner, state)["id"] != source_id
            or final["user"] != intermediate["user"]
            or final["rootfs_layers"] != intermediate["rootfs_layers"]
            or final["volumes"] != ["/tmp"] or final["onbuild_count"]):
        raise BuildError("메타데이터 전용 최종 이미지가 원래 실행 사용자·파일 계층을 보존하지 않았습니다.")
    evidence = {
        "status": "PASS", "kind": "paketo_tmp_volume_v1", "intermediate_tag": intermediate_tag,
        "intermediate_image_id": source_id, "final_image_id": final["id"],
        "dockerfile_sha256": hashlib.sha256(dockerfile.encode()).hexdigest(),
        "changes": ["VOLUME /tmp", "tool ownership labels"],
        "preserved": ["USER", "ENTRYPOINT", "CMD", "ENV", "rootfs_layers"],
        "aws_status": "AWS_NOT_TESTED",
    }
    return final, evidence, response.output
