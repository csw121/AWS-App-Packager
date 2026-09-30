"""Build only the approved local context, and load into the local daemon."""
from ..preflight import docker_arguments


def build_arguments(plan, state, tag: str) -> list[str]:
    return docker_arguments(
        state, "build", "--platform", "linux/amd64", "--tag", tag,
        "--label", f"io.aws-app-packager.job={plan.job_id}",
        "--label", "io.aws-app-packager.owner=v0.1", "--file",
        str(plan.snapshot / "Dockerfile"), str(plan.snapshot),
    )
