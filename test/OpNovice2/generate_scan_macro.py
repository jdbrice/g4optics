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


def parse_insert_location(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid --insert-missing-before {value!r}; expected COMMAND=ANCHOR"
        )
    command, anchor = value.split("=", 1)
    command = command.strip()
    anchor = anchor.strip()
    if not command.startswith("/") or not anchor.startswith("/"):
        raise argparse.ArgumentTypeError(
            f"Invalid --insert-missing-before {value!r}; commands must start with /"
        )
    return command, anchor


def parse_file_insert(value: str) -> tuple[Path, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid --insert-file-before {value!r}; expected FILE=ANCHOR"
        )
    file_path, anchor = value.split("=", 1)
    file_path = file_path.strip()
    anchor = anchor.strip()
    if not file_path:
        raise argparse.ArgumentTypeError(
            f"Invalid --insert-file-before {value!r}; file path is empty"
        )
    if not anchor.startswith("/"):
        raise argparse.ArgumentTypeError(
            f"Invalid anchor in --insert-file-before {value!r}; anchor must start with /"
        )
    return Path(file_path), anchor


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
    removed_commands: set[str] | None = None,
    file_insertions: list[tuple[Path, str]] | None = None,
    insert_before: str = DEFAULT_INSERT_BEFORE,
    insert_before_by_command: dict[str, str] | None = None,
) -> list[str]:
    if insert_before_by_command is None:
        insert_before_by_command = {}
    if removed_commands is None:
        removed_commands = set()
    if file_insertions is None:
        file_insertions = []

    output = [
        line for line in template_lines
        if (command_name(line) not in removed_commands)
    ]

    command_locations: dict[str, list[int]] = {}
    for index, line in enumerate(output):
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
        insert_groups: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
        for command, arguments in inserted:
            anchor = insert_before_by_command.get(command, insert_before)
            insert_groups.setdefault(anchor, []).append((command, arguments))

        for anchor, commands in insert_groups.items():
            insert_index = None
            for index, line in enumerate(output):
                if command_name(line) == anchor:
                    insert_index = index
                    break
            if insert_index is None:
                insert_index = len(output)
                if output and output[-1].strip():
                    output.append("\n")
                    insert_index = len(output)
            insert_lines = [
                format_command_line(command, arguments) for command, arguments in commands
            ]
            output[insert_index:insert_index] = insert_lines

    for file_path, anchor in file_insertions:
        try:
            insert_lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError as exc:
            raise ValueError(f"Cannot read inserted macro fragment {file_path}: {exc}") from exc
        if insert_lines and not insert_lines[-1].endswith("\n"):
            insert_lines[-1] += "\n"

        insert_index = None
        for index, line in enumerate(output):
            if command_name(line) == anchor:
                insert_index = index
                break
        if insert_index is None:
            insert_index = len(output)
            if output and output[-1].strip():
                output.append("\n")
                insert_index = len(output)
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
    parser.add_argument(
        "--insert-missing-before",
        action="append",
        default=[],
        type=parse_insert_location,
        metavar="COMMAND=ANCHOR",
        help=(
            "For one missing --set command, insert before ANCHOR instead of "
            "the default insertion point. Repeat as needed."
        ),
    )
    parser.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Remove every active occurrence of a Geant4 macro command.",
    )
    parser.add_argument(
        "--insert-file-before",
        action="append",
        default=[],
        type=parse_file_insert,
        metavar="FILE=ANCHOR",
        help="Insert a macro fragment file before ANCHOR. Repeat as needed.",
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
        removed_commands=set(args.remove),
        file_insertions=args.insert_file_before,
        insert_before=args.insert_before,
        insert_before_by_command=dict(args.insert_missing_before),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(generated_lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
