from __future__ import annotations

import tarfile
from pathlib import Path

from compose_safebox.core import create_backup, restore_plan, scan


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_detects_compose_bind_mount_named_volume_env_and_database(tmp_path: Path) -> None:
    project = tmp_path / "paperless"
    write(project / "data" / "example.txt", "hello")
    write(project / ".env", "SECRET=yes\n")
    write(
        project / "docker-compose.yml",
        """
services:
  db:
    image: postgres:16
    volumes:
      - pg-data:/var/lib/postgresql/data
  app:
    image: example/app
    volumes:
      - ./data:/app/data
volumes:
  pg-data:
""".strip(),
    )

    projects = scan(tmp_path)

    assert len(projects) == 1
    found = projects[0]
    assert found.name == "paperless"
    assert found.likely_databases == ["db"]
    assert str(project / ".env") in found.env_files
    assert any(mount.kind == "bind" and mount.exists for mount in found.mounts)
    assert any(mount.kind == "named-volume" and mount.source == "pg-data" for mount in found.mounts)


def test_backup_excludes_real_env_by_default_and_writes_redacted_marker(tmp_path: Path) -> None:
    project = tmp_path / "immich"
    write(project / ".env", "PASSWORD=secret\n")
    write(project / ".env.example", "PASSWORD=change-me\n")
    write(project / "uploads" / "image.txt", "not actually an image")
    write(
        project / "compose.yml",
        """
services:
  app:
    image: example/app
    volumes:
      - ./uploads:/uploads
""".strip(),
    )
    archive = tmp_path / "backup.tar.gz"

    manifest = create_backup(tmp_path, archive)

    assert archive.exists()
    assert manifest["include_env"] is False

    with tarfile.open(archive, "r:*") as tar:
        names = set(tar.getnames())

    assert "manifest.json" in names
    assert "files/immich/compose.yml" in names
    assert "files/immich/.env.example" in names
    assert "files/immich/.env.redacted" in names
    assert "files/immich/.env" not in names
    assert "files/immich/bind-mounts/uploads/image.txt" in names


def test_restore_plan_reads_manifest(tmp_path: Path) -> None:
    project = tmp_path / "whoami"
    write(
        project / "docker-compose.yaml",
        """
services:
  web:
    image: traefik/whoami
""".strip(),
    )
    archive = tmp_path / "backup.tar.gz"
    create_backup(tmp_path, archive)

    plan = restore_plan(archive)

    assert "compose-safebox restore plan" in plan
    assert "whoami" in plan
    assert "docker compose up -d" in plan
