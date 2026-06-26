# compose-safebox Agent Notes

## Overview

`compose-safebox` is a small Python CLI that helps Docker Compose homelab users
inventory, check, back up, and plan restoration of Compose projects without
accidentally publishing secrets.

## Commands

- Install locally: `python -m pip install -e .[dev]`
- Run tests: `python -m pytest`
- Run CLI from source: `python -m compose_safebox --help`

## Conventions

- Keep the runtime dependency-free unless a dependency is clearly worth it.
- Treat `.env`, key files, database dumps, and generated backups as sensitive.
- Do not include real `.env` values in archives, fixtures, docs, or commits.
- Prefer dry-run and explicit restore-plan behavior over destructive restore
  automation.
- Add focused tests for scan/check/archive behavior when changing core logic.
