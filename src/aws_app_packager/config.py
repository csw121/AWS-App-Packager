from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = ROOT / ".work"
EXPORT_DIR = ROOT / "exports"
TOOLS_DIR = ROOT / ".tools"
TEMPLATE_DIR = ROOT / "templates" / "terraform"
TEMPLATE_VERSION = "0.1.0"
# Updated only after official release/digest verification; never silently use latest.
# Docker Hub official repository metadata verified 2026-09-29, Linux/amd64.
PAKETO_BUILDER = (
    "paketobuildpacks/builder-jammy-base:0.4.579@"
    "sha256:c696f4078229f82f7e3faf9fd806554f897dcdc09686c9fee3edd0ff914560ff"
)
PAKETO_RUN_IMAGE = (
    "paketobuildpacks/run-jammy-base:0.1.227@"
    "sha256:ff484d9146f670e30c5186f767b7adc1f94eabc2c93114c3f7b0dbb632cd36a5"
)
