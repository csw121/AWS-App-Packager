import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_CONTAINER_TESTS") != "1",
    reason="Opt-in real authored samples: RUN_LOCAL_CONTAINER_TESTS=1 is required",
)]


def test_real_authored_samples():
    from devtools.local_integration import run_suite

    report, path = run_suite()
    assert report["status"] == "PASS", f"Actual outcome {report['status']}; inspect {path}"
