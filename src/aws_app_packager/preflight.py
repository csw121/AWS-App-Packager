"""Read-only discovery. The product never starts or installs a daemon."""
import json
import os
import platform
import shutil
import struct
import uuid
from pathlib import Path

from .config import ROOT, TOOLS_DIR, WORK_DIR
from .process_runner import ProcessRunner


class EnvironmentBlocked(RuntimeError):
    status = "BLOCKED"


def find_tool(name: str) -> str | None:
    suffix = ".exe" if os.name == "nt" else ""
    candidates = [TOOLS_DIR / (name + suffix), TOOLS_DIR / name / (name + suffix)]
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    if name == "docker" and os.name == "nt":
        candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
                          "Docker/Docker/resources/bin/docker.exe")
    for item in candidates:
        if item.is_file():
            return str(item.resolve())
    return None


def local_endpoint(endpoint: str) -> bool:
    """No remote TCP, SSH or network named pipe endpoints in this local tool."""
    if endpoint.startswith("npipe:////./pipe/"):
        return endpoint in {
            "npipe:////./pipe/docker_engine", "npipe:////./pipe/dockerDesktopLinuxEngine",
        }
    if endpoint.startswith("unix:///"):
        return not any(c in endpoint for c in ("\n", "\r", "\x00"))
    return False


def docker_arguments(state: dict, *args: str) -> list[str]:
    if not local_endpoint(state.get("endpoint", "")):
        raise EnvironmentBlocked("원격 Docker endpoint는 사용할 수 없습니다.")
    config = WORK_DIR / "tool-home" / "docker"
    config.mkdir(parents=True, exist_ok=True)
    return [state["docker_path"], "--host", state["endpoint"], "--config", str(config), *args]


def tool_environment(state: dict) -> dict[str, str]:
    return {"DOCKER_HOST": state["endpoint"],
            "DOCKER_CONFIG": str(WORK_DIR / "tool-home" / "docker"),
            "PACK_HOME": str(WORK_DIR / "tool-home" / "pack")}


def check_environment(runner=None, *, include_dev_tools=False) -> dict:
    runner = runner or ProcessRunner()
    result = {
        "python_version": platform.python_version(), "python_bits": struct.calcsize("P") * 8,
        "git_path": find_tool("git"), "docker_path": find_tool("docker"),
        "pack_path": find_tool("pack"), "terraform_path": find_tool("terraform"),
        "docker_ready": False, "pack_ready": False, "endpoint": "",
        "docker_version": "", "pack_version": "", "terraform_version": "",
        "disk_free_bytes": shutil.disk_usage(ROOT).free, "tools_writable": False,
        "blockers": [], "warnings": [], "diagnostics": [],
    }
    try:
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        probe = TOOLS_DIR / f".write-probe-{uuid.uuid4().hex}"
        with probe.open("x"):
            pass
        probe.unlink()
        result["tools_writable"] = True
    except OSError:
        result["warnings"].append("프로젝트 도구 폴더 쓰기 권한을 확인해야 합니다.")
    for name in ("pack", "terraform"):
        if name == "terraform" and not include_dev_tools:
            continue
        path = result[f"{name}_path"]
        if path:
            response = runner.run([path, "version"], timeout=15)
            if response.returncode == 0:
                result[f"{name}_version"] = response.output.strip()[:200]
                if name == "pack":
                    result["pack_ready"] = True
    docker = result["docker_path"]
    if not docker:
        result["blockers"].append("Docker CLI가 없습니다. Docker Desktop 설치를 별도로 준비하세요.")
        return result
    # Only inspect context metadata with the user's CLI config. All actual daemon
    # commands below use an empty tool-owned config and an explicit local endpoint.
    endpoint = os.environ.get("DOCKER_HOST", "")
    context = os.environ.get("DOCKER_CONTEXT", "")
    if context or not endpoint:
        user_config = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
        args = [docker, "--config", user_config, "context", "inspect"]
        if context:
            args.append(context)
        args.extend(["--format", '{{json .Endpoints.docker.Host}}'])
        inspected = runner.run(args, timeout=15)
        if inspected.returncode or inspected.timed_out or inspected.cancelled:
            result["blockers"].append(
                "Docker context 정보를 읽을 수 없습니다. 로컬 Docker 설정과 Desktop 상태를 확인하세요."
            )
            result["diagnostics"].append(inspected.output[:2000])
            return result
        try:
            endpoint = json.loads(inspected.output.strip())
        except (ValueError, TypeError):
            result["blockers"].append(
                "Docker context 응답 형식을 확인할 수 없습니다. Docker CLI 상태를 확인하세요."
            )
            return result
    if not isinstance(endpoint, str) or not local_endpoint(endpoint):
        result["blockers"].append(
            "Docker context가 로컬 named pipe/Unix socket이 아닙니다. 로컬 Linux context를 선택하세요."
        )
        return result
    result["endpoint"] = endpoint
    version = runner.run(docker_arguments(result, "version", "--format",
                         '{"Version":{{json .Server.Version}},"Os":{{json .Server.Os}},'
                         '"Arch":{{json .Server.Arch}}}'), timeout=20, env=tool_environment(result))
    if version.returncode:
        result["blockers"].append(
            "로컬 Docker daemon에 연결할 수 없습니다. Docker Desktop의 Linux 엔진을 시작하세요."
        )
        result["diagnostics"].append(version.output[:2000])
        return result
    try:
        server = json.loads(version.output.strip())
        result["docker_version"] = server["Version"]
        result["docker_os"] = server["Os"]
        result["docker_architecture"] = server["Arch"]
        if server["Os"] != "linux" or server["Arch"] not in {"amd64", "x86_64"}:
            result["blockers"].append("이 버전은 로컬 Linux/amd64 Docker 엔진이 필요합니다.")
        else:
            result["docker_ready"] = True
    except (KeyError, ValueError, TypeError):
        result["blockers"].append("Docker 서버의 OS/CPU 정보를 확인할 수 없습니다.")
    if not result["pack_ready"]:
        result["warnings"].append("Java 빌드에는 공식 pack CLI가 필요합니다. 프로젝트 .tools에 준비하세요.")
    if result["disk_free_bytes"] < 4 * 1024**3:
        result["warnings"].append("남은 디스크 공간이 4 GiB 미만입니다. 빌드/이미지 저장 공간을 확인하세요.")
    return result


def require_local_docker(runner=None) -> dict:
    state = check_environment(runner)
    if not state["docker_ready"]:
        raise EnvironmentBlocked(" ".join(state["blockers"]))
    return state
