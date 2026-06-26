# compose-safebox

A boring, safe backup planner for Docker Compose homelabs.

`compose-safebox` helps you answer a deceptively annoying question:

> If this server died today, do I know where my Compose files, bind mounts, and
> restore notes are?

It scans Docker Compose projects, reports risky backup assumptions, creates a
reviewable archive, and prints a restore plan. It is intentionally conservative:
real `.env` files are excluded by default.

## Status

Early alpha. Useful for small homelabs and self-hosted experiments, but review
the output before trusting it with anything important.

## Install

From GitHub:

```bash
python3 -m pip install git+https://github.com/Qintaris/compose-safebox.git
```

From source:

```bash
git clone https://github.com/Qintaris/compose-safebox.git
cd compose-safebox
python3 -m pip install -e .
```

## Quick Start

Scan a folder:

```bash
compose-safebox scan --root /srv
```

Check for backup risks:

```bash
compose-safebox check --root /srv
```

Create a backup archive:

```bash
compose-safebox backup --root /srv --out ./safebox-backup.tar.gz
```

Print a restore plan:

```bash
compose-safebox restore-plan ./safebox-backup.tar.gz
```

## What It Backs Up

- `compose.yml`, `compose.yaml`, `docker-compose.yml`, and
  `docker-compose.yaml`
- detected bind mount sources
- `.env.example`
- a `manifest.json` with projects, mounts, warnings, and restore metadata

## What It Does Not Back Up By Default

- real `.env` files
- private keys, certificates, database dumps, SQLite files
- named Docker volumes
- live database consistency

Named volumes are recorded in the manifest, but this MVP does not export them.
For database-like services such as Postgres, MariaDB, MongoDB, Redis, or MySQL,
prefer logical dumps or stop services before taking file-level backups.

## JSON Output

Use `--json` with `scan`, `check`, or `backup`:

```bash
compose-safebox scan --root /srv --json
```

## Security Model

The default behavior is designed to avoid the classic homelab mistake: creating
a convenient backup that silently contains every password.

Use `--include-env` only when you intentionally want real `.env` files in the
archive and know where the archive will be stored.

## Development

```bash
python3 -m pip install -e .[dev]
python3 -m pytest
```

## License

MIT
