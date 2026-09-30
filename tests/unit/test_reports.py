"""Report evidence boundaries: metadata preparation never means verified AWS permissions."""

import pytest

from aws_app_packager import reports
from aws_app_packager.models import (
    DeploymentSpec,
    ImageArtifact,
    RuntimeCheck,
    RuntimeConditions,
    fingerprint,
)


@pytest.fixture
def artifact(tmp_path):
    return ImageArtifact(
        job_id="a" * 32, project_label="report-test", build_method="paketo_java",
        source_fingerprint="b" * 64, local_tag="aws-app-packager/report-test:" + "a" * 32,
        image_id="sha256:" + "c" * 64, size_bytes=1, user="1000:1000",
        manifest_path=tmp_path / "manifest.json", builder_info="official builder | paketo_tmp_volume_v1",
    )


@pytest.mark.parametrize("render", [reports.readme_first, reports.aws_spec])
def test_prepared_java_reports_final_image_but_aws_permissions_unverified(artifact, render):
    spec = DeploymentSpec(project_label="report-test", container_port=8080, health_path="/health")
    report = render(spec, artifact)
    assert "`paketo_tmp_volume_v1` 준비 기록" in report
    assert "USER·ENTRYPOINT/CMD·ENV·rootfs layer를 보존" in report
    assert "실제 AWS volume 권한 성공을 뜻하지 않습니다" in report
    assert "권한 계약을 자동 해결하지 못합니다" not in report
    assert "해결하지 않은 이미지/volume 권한 계약" not in report


def test_historical_java_artifact_does_not_inherit_finalizer_claim(artifact):
    artifact.builder_info = "official builder before metadata finalizer"
    spec = DeploymentSpec(project_label="report-test", container_port=8080, health_path="/health")
    report = reports.readme_first(spec, artifact)
    assert "메타데이터 최종화 확인이 없습니다" in report
    assert "`paketo_tmp_volume_v1` 준비 기록이 있습니다" not in report
    assert "과거 이미지에 새 준비 단계가 적용됐다고 간주하지 않습니다" in report


def test_dockerfile_report_does_not_generalize_authored_sample_permissions(artifact):
    artifact.build_method = "dockerfile"
    artifact.builder_info = "user-authored Dockerfile"
    note = reports._tmp_image_note(artifact)
    assert "사용자 준비 조건" in note
    assert "VOLUME 선언만으로 쓰기 권한이 증명되지 않습니다" in note
    assert "paketo_tmp_volume_v1" not in note


def test_runbook_uses_final_image_and_keeps_aws_record_unperformed(artifact):
    runbook = reports.manual_runbook(artifact)
    assert artifact.image_id in runbook
    assert "Paketo 중간 이미지나 다른 빌드의 tag로 바꾸지 말고" in runbook
    assert "실제 AWS volume 권한 성공을 뜻하지 않습니다" in runbook
    assert "자체 샘플의 권한 관찰을 다른 앱에 일반화하지 않습니다" in runbook
    assert "AWS_NOT_TESTED" in reports.VERIFICATION_RECORD
    rows = [line for line in reports.VERIFICATION_RECORD.splitlines() if line.startswith("|")][2:]
    assert rows and all(line.endswith("| 미실시 |") for line in rows)


def test_local_report_keeps_normal_bridge_and_actual_observation_fields_separate(artifact):
    conditions = RuntimeConditions(container_port=8080, health_path="/health",
                                   network_mode="authored_sample_bridge")
    check = RuntimeCheck(
        job_id=artifact.job_id, image_id=artifact.image_id, source_fingerprint=artifact.source_fingerprint,
        conditions=conditions, conditions_fingerprint=fingerprint(conditions), status="PASS",
        network_driver="bridge", network_internal=False, host_port=43210,
        initial_http_status=200, restart_http_status=200, http_status=200,
        image_verified=True, container_started=True, restart_passed=True, cleanup_status="CLEANED",
        port_evidence=[{"phase": "start", "inspect_returncode": 0, "docker_port_returncode": 0,
                        "docker_port_output": "127.0.0.1:43210\n", "host_port": 43210, "verified": True}],
    )
    output = reports.local_test_result(check)
    assert "authored_sample_bridge" in output and "bridge / False" in output
    assert "start: 127.0.0.1:43210" in output
    assert "최초 / 재시작 후 HTTP 상태 | 200 / 200" in output
    assert "CLEANED" in output and "AWS_NOT_TESTED" in output
