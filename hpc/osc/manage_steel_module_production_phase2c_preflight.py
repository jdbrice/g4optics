#!/usr/bin/env python3
"""Manage the one isolated held Phase-2C no-Geant4 preflight."""

from __future__ import annotations

import argparse
import getpass
import json
import shlex
import subprocess
import sys
from pathlib import Path

from steel_module_production_phase2c_preflight_lib import (
    authorization_payload,
    freeze_terminal_accounting,
    list_authorizations,
    load_authorization,
    load_formal_context,
    preflight_state,
    preview_release_preflight,
    query_and_verify_held,
    reconcile_preflight,
    release_command,
    release_preflight,
    submission_command,
    submit_authorization,
    write_authorization,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", allow_abbrev=False)

    prepare = sub.add_parser("prepare", allow_abbrev=False)
    prepare_mode = prepare.add_mutually_exclusive_group(required=True)
    prepare_mode.add_argument("--check-only", action="store_true")
    prepare_mode.add_argument("--write-authorization", action="store_true")
    prepare.add_argument("--actor", default=getpass.getuser())

    submit = sub.add_parser("submit", allow_abbrev=False)
    submit.add_argument("--authorization-sha256", required=True)
    submit_mode = submit.add_mutually_exclusive_group(required=True)
    submit_mode.add_argument("--check-only", action="store_true")
    submit_mode.add_argument("--submit", action="store_true")
    submit.add_argument("--actor", default=getpass.getuser())

    held = sub.add_parser("verify-held", allow_abbrev=False)
    held.add_argument("--authorization-sha256", required=True)
    held.add_argument("--actor", default=getpass.getuser())

    release = sub.add_parser("release", allow_abbrev=False)
    release.add_argument("--authorization-sha256", required=True)
    release_mode = release.add_mutually_exclusive_group(required=True)
    release_mode.add_argument("--check-only", action="store_true")
    release_mode.add_argument("--release", action="store_true")
    release.add_argument("--actor", default=getpass.getuser())

    reconcile = sub.add_parser("reconcile", allow_abbrev=False)
    reconcile.add_argument("--authorization-sha256", required=True)
    reconcile.add_argument("--actor", default=getpass.getuser())

    accounting = sub.add_parser("freeze-accounting", allow_abbrev=False)
    accounting.add_argument("--authorization-sha256", required=True)
    accounting.add_argument("--actor", default=getpass.getuser())
    return parser.parse_args()


def _status(context: object) -> int:
    values = list_authorizations(context)
    print(f"twin_id: {context.twin_id}")
    print(f"twin_hash: {context.twin_hash}")
    print(f"production_equivalence_hash: {context.production_equivalence_hash}")
    print("production_authority: false")
    print(f"preflight_authorizations: {len(values)}")
    for digest in values:
        state = preflight_state(context, digest)
        print(
            f"{digest}: {state.status} job={state.job_id or '-'} "
            f"scheduler_contacted={str(state.scheduler_contacted).lower()}"
        )
    return 0


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        context = load_formal_context(repo_root)
        if args.command == "status":
            return _status(context)

        if args.command == "prepare":
            if args.check_only:
                value = authorization_payload(context, actor=args.actor)
                mode = "check-only"
            else:
                value = write_authorization(context, actor=args.actor)
                mode = "written"
            print(f"preflight authorization: {mode}")
            print(f"authorization_id: {value['authorization_id']}")
            print(f"authorization_sha256: {value['authorization_hash']}")
            print(f"job_name: {value['scheduler']['job_name']}")
            print("shape: one non-array task")
            print("Geant4 authorized: false")
            print("No scheduler command was invoked.")
            return 0

        authorization = load_authorization(
            context, args.authorization_sha256
        )
        state = preflight_state(context, args.authorization_sha256)
        if args.command == "submit":
            if args.check_only:
                if state.status != "prepared":
                    raise ValueError("submit check requires a prepared authorization")
                print("Phase-2C preflight submit check: PASS")
                print("command:", shlex.join(submission_command(context, authorization)))
                print("No scheduler command was invoked.")
            else:
                job_id = submit_authorization(
                    context,
                    authorization_hash=args.authorization_sha256,
                    actor=args.actor,
                )
                print("Phase-2C held preflight submission: PASS")
                print(f"job_id: {job_id}")
                print("job remains held: true")
                print("release performed: false")
            return 0

        if args.command == "verify-held":
            row = query_and_verify_held(
                context,
                authorization_hash=args.authorization_sha256,
                actor=args.actor,
            )
            print("Phase-2C held preflight identity: PASS")
            print(f"job_id: {state.job_id}")
            print(f"job_name: {row['JobName']}")
            print(f"account: {row['ObservedAccount']}")
            print(f"account_comparison: {row['AccountComparison']}")
            print("state: PENDING / JobHeldUser")
            print("non_array: true")
            print("release performed: false")
            return 0

        if args.command == "release":
            if state.status != "verified-held":
                raise ValueError("release requires a separately verified held job")
            if args.check_only:
                row, snapshot = preview_release_preflight(
                    context,
                    authorization_hash=args.authorization_sha256,
                )
                print("Phase-2C preflight release check: PASS")
                print(f"held_job_id: {state.job_id}")
                print(f"held_job_name: {row['JobName']}")
                print(f"held_account: {row['ObservedAccount']}")
                print(f"held_scontrol_sha256: {snapshot['stdout_sha256']}")
                print("command:", shlex.join(release_command(state)))
                print("Held identity was re-read; no scheduler mutation was invoked.")
            else:
                job_id = release_preflight(
                    context,
                    authorization_hash=args.authorization_sha256,
                    actor=args.actor,
                )
                print("Phase-2C preflight release: PASS")
                print(f"job_id: {job_id}")
                print("No sbatch command was invoked by release.")
            return 0

        if args.command == "reconcile":
            result = reconcile_preflight(
                context,
                authorization_hash=args.authorization_sha256,
                actor=args.actor,
            )
            print(result)
            print("No sbatch command was invoked during reconciliation.")
            return 0

        if args.command == "freeze-accounting":
            value = freeze_terminal_accounting(
                context,
                authorization_hash=args.authorization_sha256,
                actor=args.actor,
            )
            print("Phase-2C preflight accounting freeze: PASS")
            print(f"job_id: {value['job_id']}")
            print(f"terminal_state: {value['terminal_state']}")
            print(f"exit_code: {value['exit_code']}")
            print(
                "accepted_terminal_success: "
                + str(value["accepted_terminal_success"]).lower()
            )
            print("No scheduler mutation was performed.")
            return 0
        raise AssertionError("unreachable Phase-2C preflight command")
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(
            f"Cannot manage steel-module Phase-2C preflight: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
