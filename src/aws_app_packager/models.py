"""Small shared contracts. No cloud clients or subprocesses belong here."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ResourcePreset(Model):
    name: Literal["small", "medium", "large"]
    vcpu: float
    memory_mib: int
    cpu_units: int
    desired_count: Literal[1] = 1


class ProjectAssessment(Model):
    root: Path
    label: str
    method: Literal["paketo_java", "dockerfile", "unsupported"]
    source_fingerprint: str
    candidates: list[str] = Field(default_factory=list)
    port_candidates: list[int] = Field(default_factory=list)
    health_path: str | None = None
    environment_names: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0


class BuildPlan(Model):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    project_label: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    source_root: Path
    snapshot: Path
    method: Literal["paketo_java", "dockerfile"]
    source_fingerprint: str
    approval_fingerprint: str
    manifest_path: Path
    build_approved: bool = False
    builder: str = ""


class ImageArtifact(Model):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    project_label: str
    build_method: Literal["paketo_java", "dockerfile"]
    source_fingerprint: str
    local_tag: str = Field(pattern=r"^aws-app-packager/[a-z0-9-]+:[a-f0-9]{32}$")
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    os: Literal["linux"] = "linux"
    architecture: Literal["amd64"] = "amd64"
    created_at: str = Field(default_factory=utc_now)
    size_bytes: int = Field(ge=0)
    user: str
    builder_info: str = ""
    tool_versions: dict[str, str] = Field(default_factory=dict)
    manifest_path: Path
    status: Literal["BUILT"] = "BUILT"


class RuntimeConditions(Model):
    preset: Literal["small", "medium", "large"] = "medium"
    container_port: int = Field(ge=1, le=65535)
    health_path: str
    environment: dict[str, str] = Field(default_factory=dict)
    expected_status: Literal[200] = 200
    expected_marker: str | None = Field(default=None, max_length=256, pattern=r"^[^\r\n]*$")
    timeout_seconds: int = Field(default=90, ge=5, le=180)
    restart: bool = True
    network_mode: Literal["internal", "authored_sample_bridge", "approved_project_bridge"] = "internal"

    @field_validator("health_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if not re.fullmatch(r"/[A-Za-z0-9/_~.\-]*", value) or value.startswith("//"):
            raise ValueError("시험 경로는 query 없는 로컬 상대 경로여야 합니다.")
        if ".." in value.split("/") or len(value) > 200:
            raise ValueError("시험 경로가 허용 범위를 벗어났습니다.")
        return value

    @field_validator("environment")
    @classmethod
    def non_sensitive_environment(cls, value: dict[str, str]) -> dict[str, str]:
        # Explicit small allowlist: no arbitrary secret entry or host inheritance.
        allowed = {"PORT", "SERVER_PORT", "TZ", "LANG", "BPL_JVM_THREAD_COUNT", "BPL_JVM_HEAD_ROOM"}
        if len(value) > 6:
            raise ValueError("환경설정 항목이 너무 많습니다.")
        for key, item in value.items():
            if key not in allowed or not re.fullmatch(r"[A-Za-z0-9_./+\-]{1,80}", item):
                raise ValueError("허용된 비민감 환경설정만 사용할 수 있습니다.")
        return value


class PortMappingEvidence(Model):
    phase: Literal["start", "restart"]
    recorded_at: str = Field(default_factory=utc_now)
    inspect_output: str = ""
    inspect_returncode: int
    docker_port_output: str = ""
    docker_port_returncode: int
    host_port: int | None = Field(default=None, ge=1, le=65535)
    verified: bool = False


class RuntimeCheck(Model):
    job_id: str
    execution_job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    image_id: str
    source_fingerprint: str
    conditions: RuntimeConditions
    conditions_fingerprint: str
    status: Literal["NOT_RUN", "PASS", "FAIL", "CANCELLED", "STALE", "BLOCKED", "ENVIRONMENT_BLOCKED"]
    outcome: Literal[
        "NOT_RUN", "CONTAINER_START_FAILED", "ENVIRONMENT_BLOCKED_PORT_MAPPING",
        "INITIAL_HTTP_FAILED", "RESTART_HTTP_FAILED", "PASS", "CANCELLED", "BLOCKED",
        "STALE", "CLEANUP_FAILED",
    ] = "NOT_RUN"
    started_at: str = Field(default_factory=utc_now)
    finished_at: str | None = None
    http_status: int | None = None
    initial_http_status: int | None = None
    initial_http_passed: bool | None = None
    restart_http_status: int | None = None
    image_verified: bool = False
    container_started: bool = False
    restart_performed: bool = False
    host_port: int | None = Field(default=None, ge=1, le=65535)
    network_driver: str | None = None
    network_internal: bool | None = None
    port_evidence: list[PortMappingEvidence] = Field(default_factory=list)
    failure_layer: str | None = None
    restart_passed: bool = False
    message: str = ""
    logs: str = ""
    cleanup_status: Literal["NOT_NEEDED", "CLEANED", "NEEDS_ATTENTION"] = "NOT_NEEDED"
    environment: str = "local Docker, Linux/amd64; internal bridge; loopback HTTP"
    limitations: list[str] = Field(default_factory=lambda: ["업무 기능·데이터 보존·AWS 실제 동작 미검증"])


class DeploymentSpec(Model):
    profile: Literal["ecs_fargate_http_service_v1"] = "ecs_fargate_http_service_v1"
    template_version: str = "0.1.0"
    project_label: str = Field(pattern=r"^[a-z][a-z0-9-]{0,23}$")
    preset: Literal["small", "medium", "large"] = "medium"
    container_port: int = Field(ge=1, le=65535)
    health_path: str
    environment: dict[str, str] = Field(default_factory=dict)
    region: str = Field(default="ap-northeast-2", pattern=r"^[a-z]{2}(?:-[a-z]+)+-\d$")
    exposure_mode: Literal["https_existing_certificate", "http_demo"] = "https_existing_certificate"
    allowed_cidrs: list[str] = Field(default_factory=list)
    create_execution_role: bool = True
    existing_execution_role_arn: str | None = None
    domain_name: str | None = None
    certificate_arn: str | None = None
    image_uri: str | None = None
    availability_zones: list[str] = Field(default_factory=list)
    image_os: Literal["linux"] = "linux"
    image_architecture: Literal["amd64"] = "amd64"

    _path = field_validator("health_path")(RuntimeConditions.safe_path.__func__)
    _env = field_validator("environment")(RuntimeConditions.non_sensitive_environment.__func__)


class ExportBundle(Model):
    schema_version: str = "0.1.0"
    directory: Path
    zip_path: Path
    files: dict[str, str]
    external_inputs: list[str]
    image_status: str = "BUILT"
    local_status: str = "PASS"
    template_status: str = "STATIC_CHECKED"
    terraform_cli_status: str = "NOT_RUN_FOR_THIS_BUNDLE"
    aws_status: Literal["AWS_NOT_TESTED"] = "AWS_NOT_TESTED"
