"""Read-only assessment and content-addressed, tool-owned build snapshots.

The scanner is deliberately smaller than Docker's entire ignore/parser language.
Unsupported ignore syntax blocks approval instead of silently widening a context.
"""

import fnmatch
import hashlib
import json
import os
import re
import stat
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .models import BuildPlan, ProjectAssessment, fingerprint
from .redaction import has_secret_candidate, redact


@dataclass(frozen=True)
class InputLimits:
    max_files: int = 5_000
    max_entries: int = 20_000
    max_total_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 32 * 1024 * 1024
    max_depth: int = 20
    max_seconds: float = 30
    max_analysis_bytes: int = 512 * 1024


LIMITS = InputLimits()
_EXCLUDED_DIRS = {
    ".git",
    ".aws",
    ".ssh",
    ".venv",
    "venv",
    "node_modules",
    ".terraform",
    ".work",
    ".tools",
    "exports",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".gradle",
    ".m2",
    "uploads",
    "upload",
    "user-data",
    "backups",
    "logs",
    "target",
    "build",
    "dist",
}
_EXCLUDED_PATTERNS = (
    ".env*",
    "*.tfstate*",
    "*.tfplan",
    "*.plan",
    "*.log",
    "*.db",
    "*.sqlite*",
    "*.dump",
    "*.sql",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    "credentials",
    "credentials.*",
    "settings.xml",
    "*.crt",
    "*.cer",
    "*.der",
)
_TEXT_SUFFIXES = {".java", ".xml", ".properties", ".yml", ".yaml", ".py", ".js", ".ts", ".json", ".toml"}


class InputError(ValueError):
    pass


@dataclass
class _Scan:
    files: dict[str, dict] = field(default_factory=dict)
    text: dict[str, str] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    environment: set[str] = field(default_factory=set)
    total_bytes: int = 0

    @property
    def digest(self) -> str:
        return fingerprint({"files": self.files, "excluded": sorted(self.excluded)})


def safe_label(value: str) -> str:
    label = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:24].rstrip("-")
    if not label or not label[0].isalpha():
        label = "app-" + (label or hashlib.sha256(value.encode()).hexdigest()[:8])
    return label[:24].rstrip("-")


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _check_ancestors(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if _is_link(ancestor):
            raise InputError("심볼릭 링크 또는 junction/reparse 경로는 지원하지 않습니다.")


def _validate_root(root: Path) -> Path:
    if not root.is_absolute() or str(root).startswith(("\\\\", "//")):
        raise InputError("UNC가 아닌 로컬 앱 절대경로가 필요합니다.")
    if not root.is_dir():
        raise InputError("앱 폴더를 찾을 수 없습니다.")
    _check_ancestors(root)
    resolved = root.resolve(strict=True)
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise InputError("드라이브 또는 사용자 홈 전체를 앱으로 선택할 수 없습니다.")
    for owned in (config.WORK_DIR.resolve(), config.EXPORT_DIR.resolve(), config.TOOLS_DIR.resolve()):
        if resolved == owned or resolved.is_relative_to(owned) or owned.is_relative_to(resolved):
            raise InputError("앱과 도구의 작업·출력 폴더는 서로 포함할 수 없습니다.")
    return resolved


def _excluded_name(name: str, is_dir: bool) -> bool:
    lower = name.lower()
    return (
        (is_dir and lower in _EXCLUDED_DIRS)
        or any(fnmatch.fnmatchcase(lower, pattern) for pattern in _EXCLUDED_PATTERNS)
        or lower == "project.toml"
    )


def _ignore_patterns(root: Path, scan: _Scan) -> list[str]:
    ignore = root / "Dockerfile.dockerignore"
    if not ignore.exists():
        ignore = root / ".dockerignore"
    if not ignore.exists():
        return []
    if _is_link(ignore) or ignore.stat().st_size > LIMITS.max_analysis_bytes:
        raise InputError("ignore 파일의 링크 또는 크기를 확인해야 합니다.")
    try:
        text = ignore.read_text(encoding="utf-8-sig")
    except UnicodeError as error:
        raise InputError("ignore 파일을 UTF-8로 읽지 못했습니다.") from error
    patterns = []
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value == ".":
            continue
        if value.startswith("!") or any(char in value for char in ("\\", "[", "]")):
            scan.blockers.append(
                "복잡한 .dockerignore 규칙(!, escape, 문자 집합)은 v0.1에서 지원하지 않습니다."
            )
            continue
        if ".." in value.split("/"):
            scan.blockers.append(".dockerignore의 상위 경로 규칙을 확인해야 합니다.")
            continue
        patterns.append(value.strip("/"))
    return patterns


def _ignored(relative: str, patterns: list[str]) -> bool:
    parts = relative.split("/")
    for pattern in patterns:
        if "/" not in pattern:
            if any(fnmatch.fnmatchcase(part, pattern) for part in parts):
                return True
        else:
            for length in range(1, len(parts) + 1):
                candidate = "/".join(parts[:length])
                if fnmatch.fnmatchcase(candidate, pattern):
                    return True
                if pattern.startswith("**/") and fnmatch.fnmatchcase(candidate, pattern[3:]):
                    return True
    return False


def _read_checked(path: Path, root: Path, deadline: float) -> tuple[bytes, os.stat_result]:
    if time.monotonic() > deadline:
        raise InputError("입력 읽기 시간 제한에 도달했습니다.")
    _check_ancestors(path)
    if not path.resolve(strict=True).is_relative_to(root):
        raise InputError("앱 루트 밖의 파일 참조를 차단했습니다.")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
        raise InputError("일반 파일만 지원하며 하드 링크는 지원하지 않습니다.")
    if before.st_size > LIMITS.max_file_bytes:
        raise InputError("단일 파일 크기 제한에 도달했습니다.")
    data = bytearray()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise InputError("읽는 중 파일이 변경되었습니다. 다시 확인해 주세요.")
        while chunk := stream.read(65_536):
            data.extend(chunk)
            if len(data) > LIMITS.max_file_bytes or time.monotonic() > deadline:
                raise InputError("파일 크기 또는 읽기 시간 제한에 도달했습니다.")
        after = path.lstat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ) or _is_link(path):
            raise InputError("읽는 중 파일이 변경되었습니다. 다시 확인해 주세요.")
    return bytes(data), before


def _scan(root: Path, *, apply_exclusions: bool = True) -> _Scan:
    scan = _Scan()
    visited = 0
    deadline = time.monotonic() + LIMITS.max_seconds
    patterns = _ignore_patterns(root, scan) if apply_exclusions else []

    def visit(directory: Path, depth: int) -> None:
        nonlocal visited
        if depth > LIMITS.max_depth:
            raise InputError("입력 폴더 깊이 제한에 도달했습니다.")
        if time.monotonic() > deadline:
            raise InputError("입력 읽기 시간 제한에 도달했습니다.")
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > LIMITS.max_entries or time.monotonic() > deadline:
                    raise InputError("입력 항목 수 또는 읽기 시간 제한에 도달했습니다.")
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                is_dir = entry.is_dir(follow_symlinks=False)
                if apply_exclusions and (_excluded_name(entry.name, is_dir) or _ignored(relative, patterns)):
                    scan.excluded.append(relative + ("/" if is_dir else ""))
                    # Env examples are still excluded; only variable *names* are retained.
                    if entry.name.startswith(".env") and not _is_link(path) and entry.is_file():
                        if path.stat().st_size <= LIMITS.max_analysis_bytes:
                            data, _ = _read_checked(path, root, deadline)
                            scan.environment.update(
                                re.findall(
                                    r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=",
                                    data.decode("utf-8", "replace"),
                                )
                            )
                    continue
                if _is_link(path):
                    raise InputError("심볼릭 링크 또는 junction/reparse 항목이 있어 빌드를 보류합니다.")
                if is_dir:
                    visit(path, depth + 1)
                    continue
                if len(scan.files) >= LIMITS.max_files:
                    raise InputError("빌드 입력 파일 수 제한에 도달했습니다.")
                data, info = _read_checked(path, root, deadline)
                scan.total_bytes += len(data)
                if scan.total_bytes > LIMITS.max_total_bytes:
                    raise InputError("빌드 입력 전체 크기 제한에 도달했습니다.")
                scan.files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                if b"\x00" not in data:
                    text = data.decode("utf-8", errors="replace")
                    if has_secret_candidate(text):
                        scan.blockers.append(
                            f"비밀정보 후보가 있는 입력: {redact(relative)} (값은 표시하지 않음)"
                        )
                    scan.environment.update(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text))
                    if path.suffix.lower() in _TEXT_SUFFIXES or path.name == "Dockerfile":
                        if len(data) <= LIMITS.max_analysis_bytes:
                            scan.text[relative] = text
                        else:
                            scan.warnings.append(f"분석 텍스트 크기 제한: {redact(relative)}")
                if not stat.S_ISREG(info.st_mode):
                    raise InputError("일반 파일이 아닌 입력을 차단했습니다.")

    visit(root, 0)
    return scan


def _dockerfile(scan: _Scan, assessment: ProjectAssessment) -> None:
    text = scan.text.get("Dockerfile")
    if text is None:
        assessment.blockers.append("Dockerfile을 제한된 텍스트 범위에서 읽지 못했습니다.")
        return
    assessment.method = "dockerfile"
    assessment.evidence.append("Dockerfile declaration: EXPOSE는 실제 listener 확인이 아닙니다.")
    instructions = re.sub(r"\\\s*\n", " ", text)
    stages: set[str] = set()
    for line in instructions.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if re.match(r"#\s*(?:syntax|escape)\s*=", stripped, re.I):
                assessment.blockers.append("사용자 지정 Dockerfile frontend/escape는 지원하지 않습니다.")
            continue
        if re.search(r"--(?:mount|network|security)\s*=", stripped, re.I):
            assessment.blockers.append("Dockerfile RUN mount/network/security 확장은 지원하지 않습니다.")
        if re.match(r"FROM\s", stripped, re.I):
            stage = re.search(r"\sAS\s+([a-zA-Z0-9_-]+)\s*$", stripped, re.I)
            if stage:
                stages.add(stage[1])
        if re.match(r"VOLUME\b", stripped, re.I):
            declaration = stripped[len("VOLUME") :].strip()
            try:
                volumes = json.loads(declaration) if declaration.startswith("[") else declaration.split()
            except ValueError:
                volumes = None
            if volumes != ["/tmp"]:
                assessment.blockers.append(
                    "Dockerfile VOLUME은 명시적인 단일 /tmp만 지원합니다. "
                    "다른 저장 경로는 별도 준비가 필요합니다."
                )
            else:
                assessment.evidence.append("Dockerfile declaration: 단일 VOLUME /tmp")
                assessment.warnings.append(
                    "사용자 Dockerfile 이미지의 /tmp는 1777 또는 "
                    "이미지 non-root UID가 쓸 수 있는 권한이어야 합니다. "
                    "VOLUME 선언만으로 실제 권한을 확인한 것은 아닙니다. 원본을 자동 수정하지 않습니다."
                )
        if re.match(r"EXPOSE\s", stripped, re.I):
            for value in stripped.split()[1:]:
                if re.fullmatch(r"\d+(?:/tcp)?", value):
                    port = int(value.split("/")[0])
                    if 1 <= port <= 65535:
                        assessment.port_candidates.append(port)
        if re.match(r"(?:ADD|COPY)\s", stripped, re.I):
            source_stage = re.search(r"--from=([^\s]+)", stripped)
            if source_stage:
                if source_stage[1] not in stages and not source_stage[1].isdigit():
                    assessment.blockers.append("외부 COPY context/image 참조는 지원하지 않습니다.")
                continue
            source_text = re.sub(r"^(?:ADD|COPY)\s+", "", stripped, flags=re.I)
            source_text = re.sub(r"--[a-z-]+=[^\s]+\s+", "", source_text)
            try:
                sources = (
                    json.loads(source_text)[:-1] if source_text.startswith("[") else source_text.split()[:-1]
                )
            except (ValueError, TypeError):
                assessment.blockers.append("COPY/ADD 경로를 해석하지 못했습니다.")
                continue
            for source in sources:
                if not isinstance(source, str) or re.search(r"://|^git@", source):
                    assessment.blockers.append("원격 ADD/COPY 입력은 지원하지 않습니다.")
                elif source.startswith(("/", "\\")) or ".." in source.replace("\\", "/").split("/"):
                    assessment.blockers.append("COPY/ADD의 절대·상위 경로는 지원하지 않습니다.")
                elif "$" in source:
                    assessment.blockers.append("동적인 COPY/ADD 입력 경로는 지원하지 않습니다.")
                elif source not in {".", "./"}:
                    prefix = source.removeprefix("./").rstrip("/")
                    if not any(
                        name == prefix or name.startswith(prefix + "/") or fnmatch.fnmatchcase(name, prefix)
                        for name in scan.files
                    ):
                        assessment.blockers.append(
                            "COPY/ADD 입력이 복사 범위에 없습니다. 제외 목록을 확인하세요."
                        )
    assessment.warnings.append(
        "Dockerfile의 RUN/ADD와 기본 이미지 다운로드는 코드·네트워크 실행을 포함합니다."
    )


def _java(scan: _Scan, assessment: ProjectAssessment) -> None:
    text = scan.text.get("pom.xml", "")
    if not text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        assessment.blockers.append("pom.xml을 안전하게 해석할 수 없습니다.")
        return
    try:
        document = ET.fromstring(text)
    except ET.ParseError:
        assessment.blockers.append("pom.xml XML 파싱 실패: 지원 여부를 확인할 수 없습니다.")
        return
    for element in document.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    if document.find("modules") is not None:
        assessment.blockers.append("Maven 다중 모듈은 지원하지 않습니다. 앱 루트를 다시 선택하세요.")
    packaging = document.findtext("packaging", "jar").strip()
    if packaging != "jar":
        assessment.blockers.append("Maven jar 단일 웹앱만 지원합니다.")
    relative_parent = document.findtext("parent/relativePath", "").strip()
    if relative_parent:
        assessment.blockers.append("로컬 parent POM 참조는 지원하지 않습니다.")
    versions = [
        (element.text or "").strip()
        for element in document.iter()
        if element.tag
        in {"java.version", "maven.compiler.release", "maven.compiler.source", "maven.compiler.target"}
    ]
    if not versions or any(version != "21" for version in versions):
        assessment.blockers.append("Java 21 설정이 없거나 JDK 설정이 충돌합니다.")
    artifacts = {(element.text or "").strip() for element in document.iter("artifactId")}
    if not artifacts.intersection({"spring-boot-starter-web", "spring-boot-starter-webflux"}):
        assessment.blockers.append("지원 범위인 Spring Boot HTTP dependency를 확인하지 못했습니다.")
    if "spring-boot-maven-plugin" not in artifacts:
        assessment.blockers.append("Spring Boot Maven 실행 JAR plugin 선언을 확인해야 합니다.")
    if artifacts.intersection(
        {
            "spring-boot-starter-data-jpa",
            "spring-boot-starter-jdbc",
            "postgresql",
            "mysql-connector-j",
            "spring-boot-starter-data-redis",
            "spring-boot-starter-data-mongodb",
        }
    ):
        assessment.blockers.append("외부 DB dependency declaration: 추가 실행 환경 확인이 필요합니다.")
    assessment.method = "paketo_java"
    assessment.evidence.append("pom.xml declaration: Java 21 / 단일 Maven Spring Boot HTTP 후보")
    assessment.warnings.append(
        "Maven plugin과 buildpack 빌드는 승인한 앱 코드를 실행하고 의존성을 다운로드합니다."
    )


def assess_project(root: Path) -> ProjectAssessment:
    root = _validate_root(Path(root))
    assessment = ProjectAssessment(
        root=root, label=safe_label(root.name), method="unsupported", source_fingerprint=""
    )
    try:
        scan = _scan(root)
    except (InputError, OSError) as error:
        assessment.blockers.append(redact(str(error)))
        return assessment
    assessment.source_fingerprint = scan.digest
    assessment.file_count = len(scan.files)
    assessment.total_bytes = scan.total_bytes
    assessment.excluded = sorted(scan.excluded)
    assessment.warnings.extend(scan.warnings)
    assessment.blockers.extend(scan.blockers)
    assessment.environment_names = sorted(scan.environment)
    assessment.candidates = sorted(
        {
            str(Path(name).parent).replace("\\", "/")
            for name in scan.files
            if Path(name).name in {"pom.xml", "Dockerfile"} and "/" in name
        }
    )
    if "Dockerfile" in scan.files:
        _dockerfile(scan, assessment)
    elif "pom.xml" in scan.files:
        _java(scan, assessment)
    else:
        assessment.blockers.append("지원하는 루트 Dockerfile 또는 Java 21 Maven 웹앱이 없습니다.")
    for name, text in scan.text.items():
        if Path(name).name.startswith("application") and Path(name).suffix in {
            ".properties",
            ".yaml",
            ".yml",
        }:
            for match in re.finditer(r"(?m)^\s*server\.port\s*[=:]\s*(\d+)\s*$", text):
                port = int(match[1])
                if 1 <= port <= 65535:
                    assessment.port_candidates.append(port)
                    assessment.evidence.append(f"config declaration: {name}: server.port")
            if Path(name).suffix in {".yaml", ".yml"}:
                match = re.search(r"(?m)^server:\s*\n[ \t]+port:\s*(\d+)\s*$", text)
                if match and 1 <= int(match[1]) <= 65535:
                    assessment.port_candidates.append(int(match[1]))
                    assessment.evidence.append(f"limited YAML declaration: {name}: server.port")
                assessment.warnings.append(
                    "YAML은 server.port 단서만 제한적으로 읽으며 동적 우선순위는 미확인입니다."
                )
            if re.search(r"(?i)(?:datasource|spring\.data\.(?:redis|mongodb)|jdbc:|mongodb:|redis:)", text):
                assessment.blockers.append(
                    "DB 설정 declaration: 실제 연결 URL은 표시하지 않으며 추가 실행 환경이 필요합니다."
                )
        if name.endswith(".java") and re.search(r"@(?:GetMapping|RequestMapping)\s*\(\s*\"/health\"", text):
            assessment.health_path = "/health"
            assessment.evidence.append(f"source declaration: {name}: /health (실제 응답 미확인)")
        if name.endswith(".py") and re.search(r"[\"']/health[\"']", text):
            assessment.health_path = "/health"
            assessment.evidence.append(f"source literal: {name}: /health (실제 응답 미확인)")
    assessment.port_candidates = sorted(set(assessment.port_candidates))
    if len(assessment.port_candidates) > 1:
        assessment.warnings.append("서로 다른 포트 선언이 있습니다. 로컬 시험 포트를 직접 확인하세요.")
    if not assessment.port_candidates:
        assessment.warnings.append("앱의 접속 설정을 확인해야 합니다. 포트는 사용자 입력이 필요합니다.")
    assessment.blockers = list(dict.fromkeys(assessment.blockers))
    return assessment


def current_fingerprint(root: Path) -> str:
    scan = _scan(_validate_root(Path(root)))
    if scan.blockers:
        raise InputError("입력에 비밀정보 후보 또는 지원하지 않는 규칙이 있습니다.")
    return scan.digest


def _approval_values(plan: BuildPlan) -> dict:
    return {
        "job_id": plan.job_id,
        "project_label": plan.project_label,
        "source_root": str(plan.source_root),
        "snapshot": str(plan.snapshot),
        "method": plan.method,
        "source_fingerprint": plan.source_fingerprint,
        "builder": plan.builder,
        "build_approved": plan.build_approved,
    }


def prepare_build_plan(
    assessment: ProjectAssessment,
    job_id: str,
    build_approved: bool = False,
) -> BuildPlan:
    if not build_approved:
        raise InputError("앱 코드와 다운로드를 포함한 빌드 승인이 필요합니다.")
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise InputError("잘못된 작업 식별자입니다.")
    fresh = assess_project(assessment.root)
    if fresh.blockers or fresh.method == "unsupported":
        raise InputError("입력의 차단사항을 해결한 후 다시 승인해 주세요.")
    if (fresh.source_fingerprint, fresh.method, fresh.label) != (
        assessment.source_fingerprint,
        assessment.method,
        assessment.label,
    ):
        raise InputError("승인 후 원본 또는 빌드 방식이 변경되었습니다. 다시 확인·승인해 주세요.")
    root = _validate_root(assessment.root)
    scan = _scan(root)
    if scan.digest != assessment.source_fingerprint or scan.blockers:
        raise InputError("복사 준비 중 원본이 변경되었습니다. 다시 확인·승인해 주세요.")
    job_dir = config.WORK_DIR / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    _check_ancestors(job_dir)
    snapshot = job_dir / "snapshot"
    snapshot.mkdir()
    deadline = time.monotonic() + LIMITS.max_seconds
    for relative, metadata in scan.files.items():
        source = root / relative
        data, info = _read_checked(source, root, deadline)
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise InputError("복사 중 원본이 변경되었습니다. 새 작업으로 다시 승인해 주세요.")
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(data)
        if os.name != "nt":
            destination.chmod(info.st_mode & 0o777)
    if current_fingerprint(root) != assessment.source_fingerprint:
        raise InputError("복사 중 원본이 변경되었습니다. 새 작업으로 다시 승인해 주세요.")
    snapshot_scan = _scan(snapshot, apply_exclusions=False)
    if snapshot_scan.files != scan.files:
        raise InputError("빌드 복사본 지문이 원본과 일치하지 않습니다.")
    manifest_path = job_dir / "build-context-manifest.json"
    plan = BuildPlan(
        job_id=job_id,
        project_label=fresh.label,
        source_root=root,
        snapshot=snapshot,
        method=fresh.method,
        source_fingerprint=scan.digest,
        approval_fingerprint="",
        manifest_path=manifest_path,
        build_approved=True,
        builder=config.PAKETO_BUILDER if fresh.method == "paketo_java" else "Dockerfile",
    )
    plan.approval_fingerprint = fingerprint(_approval_values(plan))
    manifest = {
        "schema_version": "0.1.0",
        "job_id": job_id,
        "source_fingerprint": scan.digest,
        "approval_fingerprint": plan.approval_fingerprint,
        "files": scan.files,
        "excluded": sorted(scan.excluded),
    }
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")
    temporary.replace(manifest_path)
    return plan


def verify_plan(plan: BuildPlan) -> None:
    expected = config.WORK_DIR.resolve() / "jobs" / plan.job_id
    if (
        plan.snapshot != expected / "snapshot"
        or plan.manifest_path != expected / "build-context-manifest.json"
    ):
        raise InputError("도구 소유 작업 복사본만 빌드할 수 있습니다.")
    _check_ancestors(plan.snapshot)
    _check_ancestors(plan.manifest_path)
    if not plan.build_approved or fingerprint(_approval_values(plan)) != plan.approval_fingerprint:
        raise InputError("빌드 승인 지문이 일치하지 않습니다. 다시 승인해 주세요.")
    if current_fingerprint(plan.source_root) != plan.source_fingerprint:
        raise InputError("원본이 변경되어 빌드 승인이 오래되었습니다. 다시 승인해 주세요.")
    if plan.manifest_path.stat().st_size > 2 * 1024 * 1024:
        raise InputError("빌드 manifest 크기가 허용 범위를 벗어났습니다.")
    try:
        manifest = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError("빌드 manifest를 읽을 수 없습니다.") from error
    if manifest.get("approval_fingerprint") != plan.approval_fingerprint:
        raise InputError("manifest 승인 지문이 일치하지 않습니다.")
    scanned = _scan(plan.snapshot, apply_exclusions=False)
    if scanned.blockers or scanned.files != manifest.get("files"):
        raise InputError("빌드 복사본이 변경되었습니다. 새 복사본을 승인해 주세요.")
    if fingerprint({"files": scanned.files, "excluded": manifest.get("excluded")}) != plan.source_fingerprint:
        raise InputError("빌드 manifest와 승인한 원본 지문이 일치하지 않습니다.")
