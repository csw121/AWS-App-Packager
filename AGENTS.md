# AWS App Packager v0.1

This is a new standalone local project. Read the fresh-start specification in this directory. Do not access or copy prior projects, environments, or Git history.

Scope: Streamlit, Pydantic 2, Python 3.13; Java 21 Maven through official Paketo and existing Dockerfile to a single Linux/amd64 HTTP container. One shared runtime and two-root ECS/Fargate Terraform export. No AI, pricing, autoscaling, cloud execution, or extra frameworks.

Never execute AWS authentication/API/CLI, image push, or real Terraform plan/apply/destroy. Only reviewed own templates may run fmt/init -backend=false/validate and explicit mocked plan tests with credentials stripped. Do not run user Terraform. Real container integration is opt-in and limited to authored samples or approved snapshots. Never prune shared Docker resources.

Use argument arrays with shell=False, bounded redacted output, safe source snapshots, source/approval fingerprints, explicit local runtime approval, loopback binding, owned-resource labels, and selective cleanup. No unapproved system installation or settings changes.

Tests: `.venv\Scripts\python.exe -m pytest`; `.venv\Scripts\python.exe -m ruff check .`. Default tests must not invoke Docker/pack/Terraform/AWS. Integration requires RUN_LOCAL_CONTAINER_TESTS=1. Record real, mocked, blocked, and not-run results separately.

Authorized 2026-09-30 product update: explicitly approved UI build/runtime jobs may use an execution-job-owned normal user-defined bridge (`approved_project_bridge`). Bind approval to source/build evidence, exact runtime conditions, and execution job ID. UI analysis must remain read-only. Publish only `127.0.0.1::8080`, verify actual inspect and docker port mapping before HTTP and after restart, re-read any changed restart port, and clean only that execution job's owned container/network IDs. Preserve the older internal and authored-sample modes for existing records. Missing/unsafe mapping is ENVIRONMENT_BLOCKED_PORT_MAPPING, never IMAGE_BUILD_FAILED. No daemon/firewall/version/reset changes. Real integration remains opt-in, authored samples or approved inputs only; exercise the same service.start_build workflow as UI.
