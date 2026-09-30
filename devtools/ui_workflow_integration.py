"""Real opt-in samples through service.start_build, the ordinary UI worker entrypoint.

No mocks, previous-image reuse, direct build/runtime calls, AWS, or Terraform.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aws_app_packager import config, service  # noqa: E402
from aws_app_packager.jobs import atomic_json  # noqa: E402
from aws_app_packager.models import RuntimeConditions, fingerprint, utc_now  # noqa: E402
from aws_app_packager.preflight import check_environment, docker_arguments, tool_environment  # noqa: E402
from aws_app_packager.process_runner import ProcessRunner  # noqa: E402
from aws_app_packager.progress import describe_workflow  # noqa: E402
from aws_app_packager.trusted_samples import TRUSTED_SAMPLES  # noqa: E402


def verify_cleaned(execution_job_id, environment):
    """Read back only this execution's recorded container/network IDs after cleanup."""
    record_path = config.WORK_DIR / "jobs" / execution_job_id / "runtime-resources.json"
    if not record_path.is_file():
        return {"status": "NOT_CREATED", "checks": []}
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("job_id") != execution_job_id or record.get("owner") != "v0.1":
        raise ValueError("Cleanup record ownership mismatch")
    checks = []
    runner = ProcessRunner()
    for kind in ("container", "network"):
        reference = record.get(kind + "_id")
        if not reference:
            continue
        response = runner.run(
            docker_arguments(environment, kind, "inspect", "--format", "{{.Id}}", reference),
            timeout=20, env=tool_environment(environment),
        )
        absent = response.returncode != 0 and any(
            text in response.output.lower() for text in ("no such", "not found")
        )
        checks.append({"kind": kind, "id": reference, "absent": absent,
                       "returncode": response.returncode, "output": response.output})
    return {
        "status": "CLEANED" if record.get("cleaned") is True and len(checks) == 2
        and all(item["absent"] for item in checks) else "NEEDS_ATTENTION",
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", nargs="+", choices=list(TRUSTED_SAMPLES),
                        default=list(TRUSTED_SAMPLES))
    options = parser.parse_args()
    if os.environ.get("RUN_LOCAL_CONTAINER_TESTS") != "1":
        raise RuntimeError("Set RUN_LOCAL_CONTAINER_TESTS=1 for approved real authored sample execution")
    report_path = config.WORK_DIR / "integration" / f"ui-workflow-{uuid.uuid4().hex}.json"
    report = {
        "kind": "REAL_UI_WORKFLOW_INTEGRATION", "started_at": utc_now(),
        "entrypoint": "aws_app_packager.service.start_build",
        "workflow": "same JobManager worker, snapshot, approval, build and runtime as UI button",
        "mocked": False, "build_action": "FRESH_BUILD", "aws_status": "AWS_NOT_TESTED",
        "terraform_status": "NOT_RUN", "samples": [], "status": "RUNNING",
    }
    environment = check_environment()
    report["environment"] = {key: environment.get(key) for key in (
        "docker_ready", "docker_version", "docker_os", "docker_architecture", "pack_ready", "pack_version",
        "endpoint", "blockers",
    )}
    atomic_json(report_path, report)
    print(f"REPORT {report_path}", flush=True)
    if not environment["docker_ready"] or not environment["pack_ready"]:
        report.update(status="ENVIRONMENT_BLOCKED", finished_at=utc_now())
        atomic_json(report_path, report)
        return 2
    for name in options.samples:
        method, marker, digest = TRUSTED_SAMPLES[name]
        assessment = service.assess_project(config.ROOT / "samples" / name)
        if assessment.blockers or assessment.method != method or assessment.source_fingerprint != digest:
            report.update(status="SOURCE_BLOCKED", finished_at=utc_now(),
                          error="The authored sample differs from the reviewed source")
            atomic_json(report_path, report)
            return 2
        conditions = RuntimeConditions(
            container_port=8080, health_path="/health", expected_marker=marker, restart=True,
            network_mode="approved_project_bridge",
        )
        item = {"sample": name, "source_fingerprint": digest, "conditions": conditions.model_dump(),
                "started_at": utc_now(), "status": "RUNNING"}
        report["samples"].append(item)
        # No outer file lock: start_build acquires exactly the UI's own job lock.
        try:
            job_id = service.start_build(assessment, conditions, build_approved=True, runtime_approved=True)
        except Exception as exc:
            item.update(status="WORKFLOW_START_BLOCKED", error=type(exc).__name__, finished_at=utc_now())
            report.update(status="FAILED_OR_BLOCKED", finished_at=utc_now())
            atomic_json(report_path, report)
            return 2
        item["execution_job_id"] = job_id
        atomic_json(report_path, report)
        deadline = time.monotonic() + 2400
        previous = None
        last_print = 0.0
        while True:
            service.MANAGER.wait(timeout=1)
            state = service.MANAGER.snapshot()
            current = (state.get("status"), state.get("stage"))
            if current != previous or time.monotonic() - last_print > 30:
                process = state.get("process") or {}
                print(f"{name}: {current[0]} / {current[1]} / "
                      f"command {process.get('elapsed_seconds', 0):.0f}s / "
                      f"{process.get('phase_label') or ''}", flush=True)
                previous, last_print = current, time.monotonic()
            if state.get("status") != "RUNNING":
                break
            if time.monotonic() > deadline:
                service.MANAGER.cancel()
                item["deadline_cancel_requested"] = True
                atomic_json(report_path, report)
        # Wait until finally has persisted the terminal snapshot and released the job lock.
        service.MANAGER.wait(timeout=30)
        state = service.MANAGER.snapshot()
        result = service.MANAGER.result() or {}
        artifact, check = result.get("artifact"), result.get("check")
        item.update(finished_at=utc_now(), job_status=state.get("status"),
                    outcome=state.get("outcome"), workflow_display=describe_workflow(state),
                    job_record=f".work/jobs/{job_id}.json", error=state.get("error"))
        if artifact:
            item["artifact"] = artifact.model_dump(mode="json")
        if check:
            item["check"] = check.model_dump(mode="json")
        item["cleanup_verification"] = verify_cleaned(job_id, environment)
        mapping = {entry.phase: entry for entry in check.port_evidence} if check else {}
        passed = (
            state.get("status") == "DONE" and artifact and check
            and check.outcome == "PASS" and check.status == "PASS"
            and check.execution_job_id == job_id and check.job_id == artifact.job_id
            and check.conditions_fingerprint == fingerprint(conditions)
            and check.source_fingerprint == artifact.source_fingerprint == digest
            and check.image_id == artifact.image_id and check.image_verified and check.container_started
            and check.network_driver == "bridge" and check.network_internal is False
            and check.initial_http_passed and check.initial_http_status == 200
            and check.restart_performed and check.restart_passed and check.restart_http_status == 200
            and [entry.phase for entry in check.port_evidence] == ["start", "restart"]
            and check.host_port == mapping["restart"].host_port
            and all(e.verified for e in mapping.values())
            and check.cleanup_status == "CLEANED" and item["cleanup_verification"]["status"] == "CLEANED"
        )
        item["status"] = "PASS" if passed else state.get("outcome", "FAIL")
        if item["status"] in {"PASS", "NOT_RUN", "RUNNING"} and not passed:
            item["status"] = "EVIDENCE_FAILED"
        atomic_json(report_path, report)
        print(f"{name}: FINAL {item['status']}; image={artifact.image_id if artifact else 'NOT_BUILT'}",
              flush=True)
        for phase, evidence in mapping.items():
            print(f"{phase}: docker port = {evidence.docker_port_output.strip()}", flush=True)
    report.update(finished_at=utc_now(), status="PASS" if all(
        item["status"] == "PASS" for item in report["samples"]
    ) else "FAILED_OR_BLOCKED")
    atomic_json(report_path, report)
    print(f"FINAL {report['status']}: {report_path}", flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
