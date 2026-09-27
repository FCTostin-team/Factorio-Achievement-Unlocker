"""Command line interface for checked Factorio patch preparation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .engine import PatchError, build_plan, prepare, restore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factorio-achievement-unlocker",
        description="Create and launch a verified, separate Factorio executable.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "prepare", "launch"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path, help="Path to the original factorio or factorio.exe")
        command.add_argument("--data", choices=("modded", "vanilla"), default="modded",
                             help="Achievement data file to use (default: modded)")
        if name != "inspect":
            command.add_argument("--output", type=Path, help="Path for the managed patched copy")
        if name == "launch":
            command.add_argument("--game-arg", action="append", default=[], metavar="VALUE",
                                 help="Pass an argument to Factorio; repeat for multiple arguments")
    commands.add_parser("restore").add_argument("output", type=Path, help="Managed patched copy to remove")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            restore(args.output)
            print(f"Removed managed copy: {args.output.absolute()}")
            return 0
        if args.command == "inspect":
            plan = build_plan(args.source.read_bytes(), args.data)
            print(f"Format: {plan.format_name.upper()}, data: {plan.data_mode}, SHA-256: {plan.source_hash}")
            for edit in plan.edits:
                print(f"  0x{edit.offset:X}: {edit.name}: {edit.before.hex()} -> {edit.after.hex()}")
            print(f"Ready: {len(plan.edits)} edit(s) validated; no file changed")
            return 0
        output, plan, created = prepare(args.source, args.output, args.data)
        print(f"{'Created' if created else 'Up to date'}: {output}")
        print(f"Validated {len(plan.edits)} edit(s); original: {args.source.resolve()}")
        if args.command == "launch":
            result = subprocess.run([str(output), *args.game_arg], cwd=args.source.resolve().parent, check=False)
            return result.returncode
        return 0
    except (PatchError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
