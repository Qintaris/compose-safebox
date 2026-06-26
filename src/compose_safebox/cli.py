from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from . import __version__
from .core import check, create_backup, restore_plan, scan


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="compose-safebox",
        description="A boring, safe backup planner for Docker Compose homelabs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Discover Compose projects and mounts.")
    scan_parser.add_argument("--root", default=".", help="Root directory to scan.")
    scan_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    check_parser = subparsers.add_parser("check", help="Report backup risks and warnings.")
    check_parser.add_argument("--root", default=".", help="Root directory to scan.")
    check_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    backup_parser = subparsers.add_parser("backup", help="Create a safe tar.gz backup archive.")
    backup_parser.add_argument("--root", default=".", help="Root directory to scan.")
    backup_parser.add_argument("--out", required=True, help="Output .tar.gz or .tar archive.")
    backup_parser.add_argument(
        "--include-env",
        action="store_true",
        help="Include real .env files. Off by default to avoid secret leaks.",
    )
    backup_parser.add_argument(
        "--i-understand-env-secrets",
        action="store_true",
        help="Required with --include-env to confirm the archive may contain secrets.",
    )
    backup_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    restore_parser = subparsers.add_parser(
        "restore-plan",
        help="Print a human review plan from an existing backup archive.",
    )
    restore_parser.add_argument("archive", help="Backup archive created by compose-safebox.")

    args = parser.parse_args(argv)

    if args.command == "scan":
        projects = scan(Path(args.root))
        _print_json_or_text([asdict(project) for project in projects], args.json, _format_projects)
    elif args.command == "check":
        projects = scan(Path(args.root))
        findings = [asdict(item) for item in check(projects)]
        _print_json_or_text(findings, args.json, _format_findings)
        if any(item["level"] == "error" for item in findings):
            sys.exit(1)
    elif args.command == "backup":
        if args.include_env and not args.i_understand_env_secrets:
            parser.error("--include-env can archive secrets; add --i-understand-env-secrets to confirm")
        manifest = create_backup(
            Path(args.root),
            Path(args.out),
            include_env=args.include_env,
        )
        if args.json:
            print(json.dumps(manifest, indent=2, sort_keys=True))
        else:
            print(f"Backup created: {manifest['archive']}")
            print(f"Projects: {len(manifest['projects'])}")
            print("Review the restore plan before using this archive on another host.")
    elif args.command == "restore-plan":
        print(restore_plan(Path(args.archive)))


def _print_json_or_text(data: list[dict[str, object]], as_json: bool, formatter) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(formatter(data))


def _format_projects(projects: list[dict[str, object]]) -> str:
    if not projects:
        return "No Docker Compose projects found."
    lines: list[str] = []
    for project in projects:
        lines.append(f"{project['name']} ({project['root']})")
        lines.append(f"  compose: {project['compose_file']}")
        for env_file in project["env_files"]:
            lines.append(f"  env: {env_file}")
        for mount in project["mounts"]:
            lines.append(
                f"  mount: {mount['kind']} {mount['source']} -> {mount['target']} "
                f"(exists: {mount['exists']})"
            )
        if project["likely_databases"]:
            lines.append(f"  database-like services: {', '.join(project['likely_databases'])}")
    return "\n".join(lines)


def _format_findings(findings: list[dict[str, object]]) -> str:
    if not findings:
        return "No findings."
    return "\n".join(
        f"[{item['level'].upper()}] {item['project']}: {item['message']}" for item in findings
    )
