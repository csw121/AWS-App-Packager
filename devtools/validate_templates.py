r"""Developer-only validation of a fixed, reviewed template allowlist; never user Terraform.

Usage: .venv\Scripts\python.exe devtools\validate_templates.py --run-cli
Only fmt, init -backend=false, validate, and all-provider-mocked test plan runs are permitted.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aws_app_packager import config  # noqa: E402
from aws_app_packager.models import utc_now  # noqa: E402
from aws_app_packager.process_runner import ProcessRunner  # noqa: E402
from aws_app_packager.redaction import redact  # noqa: E402
from aws_app_packager.terraform_export import (  # noqa: E402
    PROVIDER_VERSION,
    file_sha256,
    preset_json,
    validate_template_structure,
)


def verify_trusted_templates() -> dict[str, str]:
    validate_template_structure()
    expected = json.loads((ROOT / "devtools/trusted_template_hashes.json").read_text(encoding="utf-8"))
    actual = {}
    for path in sorted(config.TEMPLATE_DIR.rglob("*")):
        if path.is_file() and (path.suffix == ".tf" or path.name.endswith(".tftest.hcl")):
            if path.is_symlink() or path.stat().st_size > 100_000:
                raise ValueError("Untrusted template path or size")
            relative = path.relative_to(config.TEMPLATE_DIR).as_posix()
            actual[relative] = file_sha256(path)
            text = path.read_text(encoding="utf-8")
            # This intentionally narrow audit matches this profile, not arbitrary HCL.
            if re.search(
                r'(?m)^\s*(?:data|module|backend|provisioner)\s+"|'
                r'^\s*(?:user_data|alias)\s*=|\b(?:local-exec|remote-exec)\b', text,
            ):
                raise ValueError("Forbidden remote/executable/provider structure")
            if path.name.endswith(".tftest.hcl"):
                if re.findall(r'mock_provider\s+"([^"]+)"', text) != ["aws"]:
                    raise ValueError("Every test file must explicitly mock the sole AWS provider")
                if re.search(r'(?m)^\s*provider\s+"', text):
                    raise ValueError("Real test providers are forbidden")
                runs = re.findall(r'\brun\s+"[^"]+"\s*\{', text)
                plans = re.findall(r'\bcommand\s*=\s*plan\b', text)
                if not runs or len(runs) != len(plans) or re.search(r'\bcommand\s*=\s*apply\b', text):
                    raise ValueError("Every run must explicitly command=plan with a mocked provider")
    actual["service/presets.json"] = hashlib.sha256(preset_json().encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(
            "Trusted template hashes changed. Review the fixed templates before updating the allowlist."
        )
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-cli", action="store_true", help="Run only the reviewed, isolated mock validation",
    )
    options = parser.parse_args()
    hashes = verify_trusted_templates()
    print("STATIC_POLICY_CHECKED: fixed own templates, explicit AWS mocks, command=plan in every test run")
    if not options.run_cli:
        print("CLI_NOT_RUN: add --run-cli for approved provider download and isolated mock validation")
        return 0
    executable = shutil.which("terraform")
    if not executable:
        print("CLI_NOT_RUN: Terraform executable not found")
        return 2
    workspace = config.WORK_DIR / "template-validation" / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    rc = workspace / "terraform.rc"
    rc.write_text(
        'disable_checkpoint = true\nprovider_installation {\n'
        '  direct {\n    include = ["registry.terraform.io/hashicorp/aws"]\n  }\n}\n',
        encoding="utf-8",
    )
    runner = ProcessRunner()
    environment = {"TF_CLI_CONFIG_FILE": str(rc), "AWS_EC2_METADATA_DISABLED": "true"}
    version = runner.run([executable, "version", "-json"], timeout=30, env=environment)
    if version.returncode:
        raise RuntimeError("Terraform version inspection failed: " + version.output)
    version_data = json.loads(version.output)
    record = {
        "recorded_at": utc_now(), "scope": "reviewed own templates copied to isolated developer workspace",
        "terraform_version": version_data["terraform_version"], "provider_version": PROVIDER_VERSION,
        "template_sha256": hashes,
        "credentials": "stripped by ProcessRunner safe_environment; metadata disabled",
        "aws_status": "AWS_NOT_TESTED", "mock_test_status": "NOT_RUN", "steps": [],
        "per_user_bundle_cli_status": "NOT_RUN_FOR_THIS_BUNDLE",
    }
    passed = True
    for root in ("registry", "service"):
        destination = workspace / root
        destination.mkdir()
        # Copy exactly reviewed files. Never discover .tf.json/.tftest.json/auto.tfvars.
        for relative in hashes:
            if relative.startswith(root + "/") and relative != "service/presets.json":
                target = workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(config.TEMPLATE_DIR / relative, target)
        if root == "service":
            (destination / "presets.json").write_text(preset_json(), encoding="utf-8")
        for arguments in (
            ["fmt", "-check", "-diff", "-recursive"],
            ["init", "-backend=false", "-input=false", "-no-color"],
            ["validate", "-no-color"],
            ["test", "-no-color"],
        ):
            result = runner.run(
                [executable, f"-chdir={destination}", *arguments], timeout=600, env=environment,
            )
            ok = result.returncode == 0 and not result.timed_out and not result.cancelled
            record["steps"].append({
                "root": root, "command": ["terraform", *arguments],
                "status": "PASS" if ok else "FAIL", "returncode": result.returncode,
                "timed_out": result.timed_out, "output": redact(result.output, 10_000),
            })
            print(f"{root}: {' '.join(arguments)}: {'PASS' if ok else 'FAIL'}", flush=True)
            if not ok:
                passed = False
                print(redact(result.output, 4000), flush=True)
                break
    record["mock_test_status"] = "MOCK_PLAN_PASS" if passed else "FAILED_OR_BLOCKED"
    record["validation_workspace"] = workspace.relative_to(ROOT).as_posix()
    output = ROOT / "docs/template_validation.json"
    output.parent.mkdir(exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print("AWS_NOT_TESTED: no real plan/apply/destroy/AWS CLI/API was executed")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
