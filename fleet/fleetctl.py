#!/usr/bin/env python3
"""One-shot local commands for the public-safe fleet registry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

if __package__:
    from .registry import (
        RegistryLoadError,
        RegistryValidationError,
        load_registry,
        render_inventory_plan_json,
        render_plan_json,
        render_status_json,
        validate_registry,
    )
else:
    from registry import (
        RegistryLoadError,
        RegistryValidationError,
        load_registry,
        render_inventory_plan_json,
        render_plan_json,
        render_status_json,
        validate_registry,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, plan, or inspect a public-safe fleet registry."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command_name in ("validate", "plan", "status", "inventory-plan"):
        command = commands.add_parser(command_name)
        command.add_argument("--registry", required=True, type=Path)
        if command_name in {"status", "inventory-plan"}:
            command.add_argument("--host", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_registry(args.registry)
        if args.command == "validate":
            counts = validate_registry(registry)
            print(
                "valid: "
                + " ".join(
                    f"{name}={counts[name]}"
                    for name in ("policies", "hosts", "endpoints", "routes", "publications")
                )
            )
            return 0
        if args.command == "plan":
            print(render_plan_json(registry), end="")
            return 0
        if args.command == "status":
            print(render_status_json(registry, args.host), end="")
            return 0
        if args.command == "inventory-plan":
            print(render_inventory_plan_json(registry, args.host), end="")
            return 0
        raise AssertionError("unexpected command")
    except RegistryLoadError:
        sys.stderr.write("invalid registry: local JSON input could not be read\n")
    except RegistryValidationError as error:
        sys.stderr.write(f"invalid registry: validation failed ({len(error.errors)} issue(s))\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
