from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from compose_safebox.cli import main
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


def test_scan_detects_multiple_env_files_and_long_mount_with_target_first(tmp_path: Path) -> None:
    project = tmp_path / "actual"
    write(project / "env" / "common.env", "A=1\n")
    write(project / "env" / "prod.env", "B=1\n")
    write(project / "config" / "settings.yml", "ok: true\n")
    write(
        project / "compose.yaml",
        """
services:
  app:
    image: example/app
    env_file:
      - ./env/common.env
      - ./env/prod.env
    volumes:
      - type: bind
        target: /app/config
        source: ./config
""".strip(),
    )

    found = scan(tmp_path)[0]

    assert str(project / "env" / "common.env") in found.env_files
    assert str(project / "env" / "prod.env") in found.env_files
    assert any(
        mount.kind == "bind"
        and mount.source == str(project / "config")
        and mount.target == "/app/config"
        for mount in found.mounts
    )


def test_backup_preserves_env_file_relative_paths_when_redacting(tmp_path: Path) -> None:
    project = tmp_path / "envpaths"
    write(project / "env" / "common.env", "A=1\n")
    write(project / "env" / "prod.env", "B=1\n")
    write(
        project / "compose.yml",
        """
services:
  app:
    image: example/app
    env_file:
      - ./env/common.env
      - ./env/prod.env
""".strip(),
    )
    archive = tmp_path / "backup.tar.gz"

    create_backup(tmp_path, archive)

    with tarfile.open(archive, "r:*") as tar:
        names = set(tar.getnames())

    assert "files/envpaths/env/common.env.redacted" in names
    assert "files/envpaths/env/prod.env.redacted" in names
    assert "files/envpaths/common.env.redacted" not in names


def test_scan_ignores_non_volume_lists_that_contain_colons(tmp_path: Path) -> None:
    project = tmp_path / "commands"
    write(
        project / "compose.yml",
        """
services:
  app:
    image: example/app
    command:
      - "--log-format=json:pretty"
      - "--listen=:8080"
""".strip(),
    )

    found = scan(tmp_path)[0]

    assert found.mounts == []


def test_backup_preserves_nested_bind_mount_names_to_avoid_collisions(tmp_path: Path) -> None:
    project = tmp_path / "nested"
    write(project / "one" / "data" / "a.txt", "one")
    write(project / "two" / "data" / "b.txt", "two")
    write(
        project / "compose.yml",
        """
services:
  app:
    image: example/app
    volumes:
      - ./one/data:/one
      - ./two/data:/two
""".strip(),
    )
    archive = tmp_path / "backup.tar.gz"

    create_backup(tmp_path, archive)

    with tarfile.open(archive, "r:*") as tar:
        names = set(tar.getnames())

    assert "files/nested/bind-mounts/one/data/a.txt" in names
    assert "files/nested/bind-mounts/two/data/b.txt" in names


def test_cli_requires_explicit_secret_confirmation_for_include_env(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"

    with pytest.raises(SystemExit) as exc:
        main(["backup", "--root", str(tmp_path), "--out", str(archive), "--include-env"])

    assert exc.value.code == 2


def test_cli_allows_include_env_with_double_confirmation(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"

    main([
        "backup",
        "--root",
        str(tmp_path),
        "--out",
        str(archive),
        "--include-env",
        "--i-understand-env-secrets",
    ])

    assert archive.exists()
