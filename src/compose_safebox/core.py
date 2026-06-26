from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


COMPOSE_FILENAMES = {
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
}

SECRET_FILENAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_ed25519",
}

SECRET_PATTERNS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.crt",
    "*.sqlite",
    "*.db",
    "*.dump",
)

DEFAULT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
}

DATABASE_HINTS = (
    "mariadb",
    "mongo",
    "mysql",
    "postgres",
    "postgresql",
    "redis",
    "sqlite",
)


@dataclass(frozen=True)
class Mount:
    source: str
    target: Optional[str]
    kind: str
    exists: bool


@dataclass(frozen=True)
class ComposeProject:
    name: str
    root: str
    compose_file: str
    env_files: list[str] = field(default_factory=list)
    mounts: list[Mount] = field(default_factory=list)
    likely_databases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    level: str
    project: str
    message: str


def discover_compose_files(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in DEFAULT_SKIP_DIRS]
        for filename in filenames:
            if filename in COMPOSE_FILENAMES:
                found.append(Path(current, filename))
    return sorted(found)


def scan(root: Path) -> list[ComposeProject]:
    return [inspect_project(path) for path in discover_compose_files(root)]


def inspect_project(compose_file: Path) -> ComposeProject:
    compose_file = compose_file.expanduser().resolve()
    project_root = compose_file.parent
    text = compose_file.read_text(encoding="utf-8", errors="replace")
    env_files = _detect_env_files(project_root, text)
    mounts = _detect_mounts(project_root, text)
    db_services = _detect_database_services(text)
    return ComposeProject(
        name=project_root.name,
        root=str(project_root),
        compose_file=str(compose_file),
        env_files=[str(path) for path in env_files],
        mounts=mounts,
        likely_databases=db_services,
    )


def check(projects: Iterable[ComposeProject]) -> list[Finding]:
    findings: list[Finding] = []
    for project in projects:
        if not project.env_files:
            findings.append(
                Finding(
                    "info",
                    project.name,
                    "No .env or env_file entry detected.",
                )
            )
        else:
            findings.append(
                Finding(
                    "warn",
                    project.name,
                    ".env files are intentionally excluded from backups unless --include-env is used.",
                )
            )

        for mount in project.mounts:
            if mount.kind == "named-volume":
                findings.append(
                    Finding(
                        "warn",
                        project.name,
                        f"Named volume '{mount.source}' is recorded but not exported by this MVP.",
                    )
                )
            elif not mount.exists:
                findings.append(
                    Finding(
                        "warn",
                        project.name,
                        f"Bind mount source '{mount.source}' does not exist on disk.",
                    )
                )

        if project.likely_databases:
            names = ", ".join(project.likely_databases)
            findings.append(
                Finding(
                    "warn",
                    project.name,
                    f"Database-like service detected ({names}); prefer a logical dump or stop the service before file backups.",
                )
            )
    return findings


def create_backup(
    root: Path,
    output: Path,
    *,
    include_env: bool = False,
    include_missing: bool = False,
) -> dict[str, object]:
    projects = scan(root)
    manifest = {
        "tool": "compose-safebox",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root.expanduser().resolve()),
        "include_env": include_env,
        "projects": [asdict(project) for project in projects],
        "findings": [asdict(item) for item in check(projects)],
    }

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="compose-safebox-") as tmp:
        stage = Path(tmp)
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        files_root = stage / "files"
        files_root.mkdir()
        for project in projects:
            _copy_project(project, files_root, include_env=include_env, include_missing=include_missing)

        mode = "w:gz" if output.suffix != ".tar" else "w"
        with tarfile.open(output, mode) as archive:
            archive.add(manifest_path, arcname="manifest.json")
            archive.add(files_root, arcname="files")

    manifest["archive"] = str(output)
    return manifest


def read_manifest(archive_path: Path) -> dict[str, object]:
    archive_path = archive_path.expanduser().resolve()
    with tarfile.open(archive_path, "r:*") as archive:
        manifest_member = archive.getmember("manifest.json")
        extracted = archive.extractfile(manifest_member)
        if extracted is None:
            raise ValueError("manifest.json is empty")
        return json.loads(extracted.read().decode("utf-8"))


def restore_plan(archive_path: Path) -> str:
    manifest = read_manifest(archive_path)
    lines = [
        "# compose-safebox restore plan",
        "",
        f"Archive: {archive_path}",
        f"Created: {manifest.get('created_at', 'unknown')}",
        "",
        "1. Extract the archive on the destination host:",
        f"   tar -xzf {archive_path.name} -C ./compose-safebox-restore",
        "",
        "2. Review manifest.json and recreate any missing .env files from your password manager.",
        "",
        "3. Copy each project directory from files/ to the destination path you want to own.",
        "",
        "4. For database services, restore from logical dumps when possible before starting containers.",
        "",
        "5. Start one project at a time and check logs:",
        "   docker compose up -d",
        "   docker compose logs --tail=100",
        "",
        "Detected projects:",
    ]
    for project in manifest.get("projects", []):
        if not isinstance(project, dict):
            continue
        lines.append(f"- {project.get('name', 'unknown')}: {project.get('compose_file', 'unknown')}")
        for mount in project.get("mounts", []):
            if isinstance(mount, dict):
                lines.append(
                    f"  mount: {mount.get('kind')} {mount.get('source')} -> {mount.get('target')}"
                )
    return "\n".join(lines)


def _detect_env_files(project_root: Path, compose_text: str) -> list[Path]:
    candidates = []
    for name in (".env", ".env.local", ".env.example"):
        path = project_root / name
        if path.exists():
            candidates.append(path.resolve())

    for match in re.finditer(r"env_file:\s*(?:\n\s*-\s*)?([^\n#]+)", compose_text):
        value = _clean_yaml_scalar(match.group(1))
        if value:
            candidates.append(_resolve_path(project_root, value))

    return sorted(set(candidates))


def _detect_mounts(project_root: Path, compose_text: str) -> list[Mount]:
    mounts: list[Mount] = []
    lines = compose_text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- "):
            mount = _parse_short_mount(project_root, stripped[2:])
            if mount is not None:
                mounts.append(mount)
        if re.match(r"source:\s*", stripped):
            mount = _parse_long_mount(project_root, lines[index : index + 5])
            if mount is not None:
                mounts.append(mount)

    unique = {(mount.source, mount.target, mount.kind): mount for mount in mounts}
    return sorted(unique.values(), key=lambda item: (item.kind, item.source, item.target or ""))


def _parse_short_mount(project_root: Path, value: str) -> Optional[Mount]:
    value = _clean_yaml_scalar(value)
    if ":" not in value or value.startswith("${"):
        return None
    source, target, *_rest = value.split(":")
    if not source or source.startswith("/var/run/docker.sock"):
        return None
    return _mount_from_source(project_root, source, target)


def _parse_long_mount(project_root: Path, lines: list[str]) -> Optional[Mount]:
    source = None
    target = None
    is_bind = False
    for line in lines:
        stripped = line.strip()
        if stripped == "type: bind":
            is_bind = True
        elif stripped.startswith("source:") or stripped.startswith("src:"):
            source = _clean_yaml_scalar(stripped.split(":", 1)[1])
        elif stripped.startswith("target:") or stripped.startswith("dst:") or stripped.startswith("destination:"):
            target = _clean_yaml_scalar(stripped.split(":", 1)[1])
    if source is None:
        return None
    mount = _mount_from_source(project_root, source, target)
    if is_bind and mount.kind == "named-volume":
        resolved = _resolve_path(project_root, source)
        return Mount(str(resolved), target, "bind", resolved.exists())
    return mount


def _mount_from_source(project_root: Path, source: str, target: Optional[str]) -> Mount:
    if source.startswith((".", "/", "~")):
        resolved = _resolve_path(project_root, source)
        return Mount(str(resolved), target, "bind", resolved.exists())
    return Mount(source, target, "named-volume", False)


def _detect_database_services(compose_text: str) -> list[str]:
    services: list[str] = []
    current_service: Optional[str] = None
    for line in compose_text.splitlines():
        if re.match(r"^\s{2}[A-Za-z0-9_.-]+:\s*$", line):
            current_service = line.strip().rstrip(":")
        image_match = re.search(r"image:\s*['\"]?([^'\"\s]+)", line)
        if current_service and image_match:
            image = image_match.group(1).lower()
            if any(hint in image for hint in DATABASE_HINTS):
                services.append(current_service)
    return sorted(set(services))


def _copy_project(
    project: ComposeProject,
    files_root: Path,
    *,
    include_env: bool,
    include_missing: bool,
) -> None:
    destination = files_root / _safe_name(project.name)
    destination.mkdir(parents=True, exist_ok=True)

    compose_path = Path(project.compose_file)
    shutil.copy2(compose_path, destination / compose_path.name)

    for env_file in project.env_files:
        env_path = Path(env_file)
        if env_path.name == ".env.example" or include_env:
            if env_path.exists():
                shutil.copy2(env_path, destination / env_path.name)
        else:
            redacted = destination / f"{env_path.name}.redacted"
            redacted.write_text(
                "# compose-safebox intentionally did not copy this secret file.\n",
                encoding="utf-8",
            )

    mounts_root = destination / "bind-mounts"
    for mount in project.mounts:
        if mount.kind != "bind":
            continue
        source = Path(mount.source)
        if not source.exists():
            if include_missing:
                (mounts_root / _safe_name(source.name)).mkdir(parents=True, exist_ok=True)
            continue
        relative_target = mounts_root / _safe_name(source.name or "root")
        if source.is_dir():
            shutil.copytree(
                source,
                relative_target,
                ignore=_ignore_sensitive,
                symlinks=True,
                dirs_exist_ok=True,
            )
        elif not _is_sensitive(source):
            relative_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, relative_target)


def _ignore_sensitive(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in DEFAULT_SKIP_DIRS or _is_sensitive(Path(name))}


def _is_sensitive(path: Path) -> bool:
    name = path.name
    if name in SECRET_FILENAMES:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def _resolve_path(project_root: Path, value: str) -> Path:
    cleaned = os.path.expandvars(value)
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _clean_yaml_scalar(value: str) -> str:
    return value.strip().strip("'\"")


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return safe or "item"
