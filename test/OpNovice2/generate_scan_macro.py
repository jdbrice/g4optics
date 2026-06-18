#!/usr/bin/env python3
"""Generate one OpNovice2 scan macro from a template.

This script treats Geant4 macro commands as commands, not as full-line string
templates. Existing commands are replaced by command name; missing commands are
inserted before /run/initialize.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
from typing import Iterable


DEFAULT_INSERT_BEFORE = "/run/initialize"


def parse_set(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid --set {value!r}; expected COMMAND=ARGUMENTS"
        )
    command, arguments = value.split("=", 1)
    command = command.strip()
    if not command.startswith("/"):
        raise argparse.ArgumentTypeError(
            f"Invalid command in --set {value!r}; command must start with /"
        )
    return command, arguments.strip()


def split_active_and_comment(line: str) -> tuple[str, str, str]:
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    comment_index = body.find("#")
    if comment_index < 0:
        return body, "", newline
    return body[:comment_index].rstrip(), body[comment_index:], newline


def command_name(line: str) -> str | None:
    active, _comment, _newline = split_active_and_comment(line)
    stripped = active.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split(None, 1)[0]


def format_command_line(command: str, arguments: str, old_line: str | None = None) -> str:
    indent = ""
    comment = ""
    newline = "\n"
    if old_line is not None:
        active, old_comment, old_newline = split_active_and_comment(old_line)
        indent = active[: len(active) - len(active.lstrip())]
        comment = old_comment
        newline = old_newline or "\n"

    line = f"{indent}{command}"
    if arguments:
        line += f" {arguments}"
    if comment:
        line += f"  {comment}"
    return line + newline


def ordered_sets(pairs: Iterable[tuple[str, str]]) -> OrderedDict[str, str]:
    result: OrderedDict[str, str] = OrderedDict()
    for command, arguments in pairs:
        if command in result:
            raise ValueError(f"Duplicate --set command: {command}")
        result[command] = arguments
    return result


def generate_macro(
    template_lines: list[str],
    replacements: OrderedDict[str, str],
    required_commands: set[str],
    insert_before: str = DEFAULT_INSERT_BEFORE,
) -> list[str]:
    command_locations: dict[str, list[int]] = {}
    for index, line in enumerate(template_lines):
        command = command_name(line)
        if command is None:
            continue
        command_locations.setdefault(command, []).append(index)

    for command in replacements:
        locations = command_locations.get(command, [])
        if len(locations) > 1:
            raise ValueError(
                f"Template contains {len(locations)} active occurrences of {command}; "
                "refusing ambiguous replacement"
            )

    output = list(template_lines)
    inserted: list[tuple[str, str]] = []
    for command, arguments in replacements.items():
        locations = command_locations.get(command, [])
        if locations:
            output[locations[0]] = format_command_line(
                command, arguments, old_line=output[locations[0]]
            )
        else:
            inserted.append((command, arguments))

    if inserted:
        insert_index = None
        for index, line in enumerate(output):
            if command_name(line) == insert_before:
                insert_index = index
                break
        if insert_index is None:
            insert_index = len(output)
            if output and output[-1].strip():
                output.append("\n")
        insert_lines = [format_command_line(command, arguments) for command, arguments in inserted]
        output[insert_index:insert_index] = insert_lines

    generated_commands = {
        command for line in output if (command := command_name(line)) is not None
    }
    missing = sorted(required_commands - generated_commands)
    if missing:
        raise ValueError(f"Generated macro is missing required commands: {', '.join(missing)}")

    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        type=parse_set,
        metavar="COMMAND=ARGUMENTS",
        help="Replace or insert one Geant4 macro command. Repeat as needed.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Require an active command to exist in the generated macro.",
    )
    parser.add_argument(
        "--insert-before",
        default=DEFAULT_INSERT_BEFORE,
        help="Command before which missing --set commands are inserted.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    replacements = ordered_sets(args.set)
    required_commands = set(args.require)

    template_lines = args.template.read_text(encoding="utf-8").splitlines(keepends=True)
    generated_lines = generate_macro(
        template_lines,
        replacements,
        required_commands,
        insert_before=args.insert_before,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(generated_lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
