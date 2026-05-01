#!/usr/bin/env python3
"""Parse ABCX files and show diagnostics.

Usage:
    python3 scripts/parse_abcx.py <file>
    python3 scripts/parse_abcx.py -          # read from stdin
    python3 scripts/parse_abcx.py <file> --abc   # also show converted ABC
    python3 scripts/parse_abcx.py <file> --json  # raw JSON output (no colors)
"""

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARSE_JS = os.path.join(SCRIPT_DIR, "parse_abcx.js")

RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def parse_file(path: str, show_abc: bool = False) -> dict:
    env = os.environ.copy()
    if show_abc:
        env["SHOW_ABC"] = "1"

    result = subprocess.run(
        ["node", PARSE_JS, path],
        capture_output=True,
        text=True,
        env=env,
    )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Failed to parse output from parse_abcx.js:\n{result.stdout}", file=sys.stderr)
        print(f"stderr: {result.stderr}", file=sys.stderr)
        sys.exit(2)


def show_report(data: dict, source_path: str) -> None:
    errors = data.get("errors", [])
    warnings = data.get("warnings", [])
    voices = data.get("voices", [])
    meter = data.get("meter")
    is_abcx = data.get("isAbcx", False)

    print(f"{BOLD}{os.path.basename(source_path)}{RESET}  {'(ABCX)' if is_abcx else '(ABC)'}")

    if voices:
        print(f"  Voices: {', '.join(voices)}")
    if meter is not None:
        print(f"  Meter: {meter}")

    if not errors and not warnings:
        print(f"  {BOLD}OK{RESET} — no errors or warnings")
        return

    if errors:
        print(f"\n{BOLD}{RED}{len(errors)} error(s){RESET}")
        for e in errors:
            loc = f"L{e['line'] + 1}:{e['column']}" if e.get("line") is not None else ""
            print(f"  {RED}✗{RESET} {loc}  {e['message']}")

    if warnings:
        print(f"\n{BOLD}{YELLOW}{len(warnings)} warning(s){RESET}")
        for w in warnings:
            loc = f"L{w['line'] + 1}:{w['column']}" if w.get("line") is not None else ""
            print(f"  {YELLOW}⚠{RESET} {loc}  {w['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse ABCX and show diagnostics")
    parser.add_argument("file", help="ABCX file to parse (use - for stdin)")
    parser.add_argument("--abc", action="store_true", help="Show converted ABC output")
    parser.add_argument("--json", action="store_true", dest="raw_json", help="Output raw JSON (no colors)")
    args = parser.parse_args()

    if args.file == "-":
        source = sys.stdin.read()
        env = os.environ.copy()
        if args.abc:
            env["SHOW_ABC"] = "1"
        result = subprocess.run(
            ["node", PARSE_JS],
            input=source,
            capture_output=True,
            text=True,
            env=env,
        )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            print(f"Failed to parse output:\n{result.stdout}", file=sys.stderr)
            sys.exit(2)
    else:
        abspath = os.path.abspath(args.file)
        data = parse_file(abspath, show_abc=args.abc)

    if args.raw_json:
        print(json.dumps(data, indent=2))
    else:
        show_report(data, args.file)
        if args.abc and "abc" in data:
            print(f"\n{BOLD}--- Converted ABC ---{RESET}")
            print(data["abc"])

    sys.exit(1 if data.get("errors") else 0)


if __name__ == "__main__":
    main()
