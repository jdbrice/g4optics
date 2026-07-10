#!/usr/bin/env python3
"""Replace selected rows in a complete surface attempt with recovery jobs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", required=True)
    parser.add_argument("--recovery", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-plans", type=int, default=12)
    parser.add_argument("--expected-replacements", type=int, default=4)
    return parser.parse_args()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not fields or not rows:
        raise ValueError(f"empty manifest: {path}")
    return fields, rows


def latest_unique(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    selected = rows[-count:]
    plans = [row.get("plan", "").strip() for row in selected]
    if len(selected) != count or not all(plans) or len(set(plans)) != count:
        raise ValueError(f"latest {count} rows do not contain unique plans")
    return selected


def main() -> int:
    args = parse_args()
    primary_fields, primary_rows = read_rows(Path(args.primary))
    recovery_fields, recovery_rows = read_rows(Path(args.recovery))
    if recovery_fields != primary_fields:
        raise ValueError("primary and recovery manifest headers differ")

    selected = latest_unique(primary_rows, args.expected_plans)
    replacements = latest_unique(recovery_rows, args.expected_replacements)
    by_plan = {row["plan"].strip(): row for row in replacements}
    primary_plans = {row["plan"].strip() for row in selected}
    unknown = sorted(set(by_plan) - primary_plans)
    if unknown:
        raise ValueError(f"recovery plans are absent from primary attempt: {unknown}")

    composed = [by_plan.get(row["plan"].strip(), row) for row in selected]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=primary_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(composed)
    print(f"Composed {len(composed)} plans with {len(replacements)} replacements: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
