import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from aws_app_packager import config, project_input
from aws_app_packager.project_input import (
    InputError,
    assess_project,
    current_fingerprint,
    prepare_build_plan,
    safe_label,
    verify_plan,
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "tool" / ".work")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "tool" / "exports")
    monkeypatch.setattr(config, "TOOLS_DIR", tmp_path / "tool" / ".tools")
    root = tmp_path / "공백 있는 앱"
    root.mkdir()
    (root / "Dockerfile").write_text(
        "FROM python:3.13.7-slim\nCOPY server.py /app/server.py\nUSER 10001\nEXPOSE 8080\n"
        'CMD ["python", "/app/server.py"]\n',
        encoding="utf-8",
    )
    (root / "server.py").write_text('ROUTE = "/health"\n', encoding="utf-8")
    return root


def test_snapshot_excludes_sensitive_files_and_does_not_change_source(app):
    (app / ".env.example").write_text("API_TOKEN=must-not-copy\nPORT=8080\n")
    (app / ".git").mkdir()
    (app / ".git" / "history").write_text("unrelated")
    (app / "asset.png").write_bytes(b"\x00\x01\x02\xff")
    assessment = assess_project(app)
    assert not assessment.blockers
    assert assessment.environment_names == ["API_TOKEN", "PORT"]
    assert assessment.port_candidates == [8080]
    original = (app / "Dockerfile").read_bytes()
    plan = prepare_build_plan(assessment, uuid.uuid4().hex, build_approved=True)
    verify_plan(plan)
    assert not (plan.snapshot / ".env.example").exists()
    assert not (plan.snapshot / ".git").exists()
    assert (plan.snapshot / "asset.png").read_bytes() == b"\x00\x01\x02\xff"
    assert (app / "Dockerfile").read_bytes() == original
    assert str(app) not in plan.manifest_path.read_text()


def test_build_needs_approval(app):
    with pytest.raises(InputError, match="승인"):
        prepare_build_plan(assess_project(app), uuid.uuid4().hex, build_approved=False)


@pytest.mark.parametrize("target", ["source", "snapshot", "manifest", "approval", "builder"])
def test_changed_input_invalidates_plan(app, target):
    plan = prepare_build_plan(assess_project(app), uuid.uuid4().hex, build_approved=True)
    if target == "source":
        (app / "server.py").write_text("CHANGED = True\n")
    elif target == "snapshot":
        (plan.snapshot / "server.py").write_text("CHANGED = True\n")
    elif target == "manifest":
        manifest = json.loads(plan.manifest_path.read_text())
        manifest["files"]["server.py"]["sha256"] = "0" * 64
        plan.manifest_path.write_text(json.dumps(manifest))
    elif target == "approval":
        plan.build_approved = False
    else:
        plan.builder = "unapproved/builder:latest"
    with pytest.raises(InputError):
        verify_plan(plan)


def test_source_change_between_assessment_and_approval_is_blocked(app):
    assessed = assess_project(app)
    (app / "new.py").write_text("NEW = True\n")
    with pytest.raises(InputError, match="변경"):
        prepare_build_plan(assessed, uuid.uuid4().hex, build_approved=True)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("max_files", 1),
        ("max_file_bytes", 4),
        ("max_total_bytes", 4),
        ("max_depth", 0),
        ("max_entries", 1),
        ("max_seconds", 0),
    ],
)
def test_scan_limits_fail_closed(app, monkeypatch, setting, value):
    (app / "nested").mkdir()
    (app / "nested" / "file").write_text("data")
    monkeypatch.setattr(project_input, "LIMITS", replace(project_input.LIMITS, **{setting: value}))
    assert assess_project(app).blockers


def test_symlink_or_reparse_input_fails_closed(app, monkeypatch):
    original = project_input._is_link
    monkeypatch.setattr(project_input, "_is_link", lambda path: path.name == "server.py" or original(path))
    assert any("reparse" in item for item in assess_project(app).blockers)


def test_hardlink_input_fails_closed(app):
    os.link(app / "server.py", app / "linked.py")
    assert any("하드 링크" in item for item in assess_project(app).blockers)


def test_root_and_output_overlap_rejected(app):
    with pytest.raises(InputError):
        assess_project(Path(app.anchor))
    with pytest.raises(InputError):
        assess_project(Path.home())
    config.WORK_DIR.mkdir(parents=True)
    with pytest.raises(InputError):
        assess_project(config.WORK_DIR)
    with pytest.raises(InputError):
        assess_project(app.parent)
    with pytest.raises(InputError):
        assess_project(Path("relative-input"))
    with pytest.raises(InputError):
        assess_project(Path(r"\\server\share\app"))


def test_secret_candidate_blocks_without_disclosing_value(app):
    secret = "actual-private-value-12345"
    (app / "config.properties").write_text(f"api_key={secret}\n")
    assessment = assess_project(app)
    assert any("비밀정보 후보" in item for item in assessment.blockers)
    assert secret not in assessment.model_dump_json()
    with pytest.raises(InputError):
        prepare_build_plan(assessment, uuid.uuid4().hex, build_approved=True)


def test_variable_name_is_not_a_secret(app):
    (app / "config.properties").write_text("api_key=${API_KEY}\n")
    assert not assess_project(app).blockers


@pytest.mark.parametrize(
    "filename,content",
    [
        ("Dockerfile", "ENV PASSWORD actual-private-value"),
        ("config.xml", "<password>actual-private-value</password>"),
    ],
)
def test_obvious_whitespace_env_and_xml_secrets_block_build(app, filename, content):
    with (app / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n" + content + "\n")
    assessment = assess_project(app)
    assert any("비밀정보 후보" in item for item in assessment.blockers)
    assert "actual-private-value" not in assessment.model_dump_json()


def test_dockerignore_preserved_and_enforced(app):
    (app / ".dockerignore").write_text("ignored/\n**/*.cache\n")
    (app / "ignored").mkdir()
    (app / "ignored" / "file").write_text("not copied")
    (app / "thing.cache").write_text("not copied")
    plan = prepare_build_plan(assess_project(app), uuid.uuid4().hex, build_approved=True)
    assert not (plan.snapshot / "ignored").exists()
    assert not (plan.snapshot / "thing.cache").exists()
    assert (plan.snapshot / ".dockerignore").read_text() == "ignored/\n**/*.cache\n"


def test_dockerignore_cannot_unexclude_sensitive_files(app):
    (app / ".dockerignore").write_text("!.env\n")
    (app / ".env").write_text("PASSWORD=secret-value\n")
    assessment = assess_project(app)
    assert assessment.blockers
    assert ".env" in assessment.excluded


@pytest.mark.parametrize(
    "instruction",
    [
        "COPY ../outside /app/",
        "COPY .env /app/",
        "ADD https://example.com/payload /app/",
        "RUN --mount=type=ssh id",
        "RUN --network=host id",
        "COPY --from=unknown:latest /bin/x /x",
        "VOLUME /data",
    ],
)
def test_unsupported_dockerfile_operations_block(app, instruction):
    with (app / "Dockerfile").open("a") as stream:
        stream.write(instruction + "\n")
    assert assess_project(app).blockers


@pytest.mark.parametrize("instruction", ["VOLUME /tmp", 'VOLUME ["/tmp"]', "volume\t/tmp"])
def test_single_tmp_volume_is_supported_with_explicit_permission_warning(app, instruction):
    with (app / "Dockerfile").open("a", encoding="utf-8") as stream:
        stream.write(instruction + "\n")
    assessment = assess_project(app)
    assert not assessment.blockers
    assert any("1777" in warning and "확인한 것은 아닙니다" in warning for warning in assessment.warnings)
    plan = prepare_build_plan(assessment, uuid.uuid4().hex, build_approved=True)
    assert (plan.snapshot / "Dockerfile").read_bytes() == (app / "Dockerfile").read_bytes()


@pytest.mark.parametrize(
    "instruction",
    [
        'VOLUME ["/data"]',
        'VOLUME ["/tmp", "/data"]',
        'VOLUME ["/tmp", "/tmp"]',
        "VOLUME /tmp /data",
        "VOLUME ${TMP_DIR}",
        'VOLUME ["${TMP_DIR}"]',
        "VOLUME /tmp/",
        "VOLUME ../tmp",
        "VOLUME [/tmp]",
        "VOLUME",
    ],
)
def test_other_multiple_dynamic_or_invalid_volumes_are_blocked(app, instruction):
    with (app / "Dockerfile").open("a", encoding="utf-8") as stream:
        stream.write(instruction + "\n")
    assessment = assess_project(app)
    assert any("VOLUME" in blocker for blocker in assessment.blockers)


def test_project_descriptor_cannot_control_pack(app):
    (app / "project.toml").write_text('[build]\nbuilder="untrusted/builder"\n')
    plan = prepare_build_plan(assess_project(app), uuid.uuid4().hex, build_approved=True)
    assert not (plan.snapshot / "project.toml").exists()


def test_java_support_and_parse_failure(app):
    (app / "Dockerfile").unlink()
    pom = (
        "<project><properties><java.version>21</java.version></properties>"
        "<dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies>"
        "<build><plugins><plugin><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build>"
        "</project>"
    )
    (app / "pom.xml").write_text(pom)
    assessment = assess_project(app)
    assert assessment.method == "paketo_java" and not assessment.blockers
    (app / "pom.xml").write_text(pom.replace("21", "17"))
    assert assess_project(app).blockers
    (app / "pom.xml").write_text("<broken")
    assert any("파싱 실패" in item for item in assess_project(app).blockers)


def test_external_database_requires_environment(app):
    (app / "application.properties").write_text("spring.datasource.url=jdbc:postgresql://private/db\n")
    assessment = assess_project(app)
    assert any("추가 실행 환경" in item for item in assessment.blockers)
    assert "private/db" not in assessment.model_dump_json()


def test_safe_label_and_fingerprint_are_stable(app):
    assert safe_label("'; rm -rf /!") == "rm-rf"
    assert safe_label("한글").startswith("app-")
    assert current_fingerprint(app) == current_fingerprint(app)
