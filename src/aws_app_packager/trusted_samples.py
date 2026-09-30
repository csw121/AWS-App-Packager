"""Narrow opt-in authorization for the two reviewed samples on a normal bridge."""

import os

from . import config
from .models import ImageArtifact, RuntimeConditions
from .project_input import assess_project
from .runtime_approval import verify_image_provenance

# Reviewed source fingerprints, including the exact sample file names and contents.
# Changing a sample requires reviewing it and deliberately updating this allowlist.
TRUSTED_SAMPLES = {
    "spring-http": (
        "paketo_java", "packager-spring-v1",
        "a9cb1472a3d1effe51ddd6a0bc72bb7aacc4d6ef32f287c171e27c07e084fa86",
    ),
    "docker-http": (
        "dockerfile", "packager-python-v1",
        "5f096cd73c93fbe0db3830dce21979a50b37c088e15285656de24c95e818a0e2",
    ),
}


def verify_trusted_sample(artifact: ImageArtifact, conditions: RuntimeConditions) -> None:
    """Check source and recorded build provenance before allowing outbound networking.

    This checks local evidence, not a signature against malicious host mutation.
    The caller must still inspect the actual Docker image/tag before execution.
    """
    if os.environ.get("RUN_LOCAL_CONTAINER_TESTS") != "1":
        raise ValueError("일반 bridge 자체 샘플 시험에는 RUN_LOCAL_CONTAINER_TESTS=1 승인이 필요합니다.")
    expected = TRUSTED_SAMPLES.get(artifact.project_label)
    if expected is None:
        raise ValueError("일반 bridge는 검토된 두 자체 샘플에만 허용합니다.")
    method, marker, source_hash = expected
    if (artifact.build_method != method or artifact.source_fingerprint != source_hash
            or conditions.container_port != 8080 or conditions.health_path != "/health"
            or conditions.expected_marker != marker or conditions.environment or not conditions.restart):
        raise ValueError("승인된 자체 샘플의 소스·포트·HTTP·재시작 조건이 아닙니다.")
    assessment = assess_project(config.ROOT / "samples" / artifact.project_label)
    if assessment.blockers or assessment.source_fingerprint != source_hash:
        raise ValueError("검토한 자체 샘플의 원본이 변경되었습니다.")
    verify_image_provenance(artifact)
