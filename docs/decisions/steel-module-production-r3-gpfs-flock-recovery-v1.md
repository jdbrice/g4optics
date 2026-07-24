# Steel-module R3 GPFS flock recovery v1

Status: completed on OSC. The accepted execution-v5 R3 evidence described in
Section 6 closes this recovery. This record authorized only the recovery needed
to obtain one successful, no-Geant4 R3 container-isolation preflight. It did
not and does not authorize a production intent, Geant4 execution, or a BC-S1
production submission.

This is an additive amendment to
`steel-module-production-r3-writer-probe-recovery-v1.md`. It does not change
the 32 BC-S1 tasks, 8,000 requested events, 64 production seeds, simulation
source, executable, runtime image, Geant4 data, or physics configuration.

## 1. Observed execution-v4 failure

The validated execution-v4 identity was:

```text
execution_id   sm-v1-production-bc-s1-execution-v4-dd37f569f5e8
execution_hash c729dfab23ebc6af2da4e1b52b746f78bbb3dd786f1ff57bbe5c0e4b274e625f
job_id         50558158
job_name       g4sm-r3-c729dfab23eb
```

The held job was independently validated before one explicit release. It was
non-array, `PENDING / JobHeldUser`, `Requeue=0`, account `PAS2524`
case-insensitively, and used the frozen execution-v4 launcher, working
directory, and output path. The requested resources were one node, one CPU,
1 GiB, and ten minutes.

After release, the terminal scheduler rows were exactly:

```text
50558158        FAILED     1:0
50558158.batch  FAILED     1:0
50558158.extern COMPLETED  0:0
```

The terminal queue snapshot was empty. The Slurm log contained exactly:

```text
Cannot run steel-module R3 container probe: [Errno 9] Bad file descriptor
```

The execution-v4 `attempts/` directory remained empty and the canonical raw
job workspace was never created. Therefore the failure occurred before the
protected probe body, Apptainer launch, mountpoint creation, or any Geant4,
event, or production-seed consumption.

The externally frozen input hashes are:

```text
held scontrol fa5c8bfe8417dbf1842fb57424a77dd70cd5a5a01bd684c5b1ac4af1ecc6e3eb
sacct         8df470b4fff19dbcca1d4c259e53ba7dd2377ba3cfc6b73f67aaf639df616ca4
squeue        e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
Slurm output  0c0d9783c71ba16ba4472d34a0bf2464d244258a401a04a5a4a6f471e5c27c4d
```

## 2. Root cause confirmed on OSC GPFS

The portable control-lock helper opened every lock with `O_RDONLY`, then
requested either a shared or exclusive `flock`. Execution-v4 also froze its
control-lock file and manifest mode as `0400`.

An OSC GPFS scratch capability test reproduced the production error exactly:

```text
0400 file + O_RDONLY + LOCK_EX -> OSError: [Errno 9] Bad file descriptor
0600 file + O_RDWR   + LOCK_EX -> PASS
```

The R3 runner first loads the execution under a shared lock and then requests
an exclusive lock before creating the raw workspace. The exclusive request on
the read-only descriptor is therefore the exact stage consistent with every
observed artifact. This is a GPFS lock-access-mode compatibility defect, not a
container, Geant4, physics, or detector-model failure.

## 3. Lock policy

Historical successors remain immutable. In particular, execution-v4's lock
must not be chmodded, replaced, or rewritten in place.

The corrected portable-lock contract is:

- shared acquisition uses `O_RDONLY` and accepts the recorded historical
  mode;
- exclusive acquisition requires a recorded `0600` lock and uses `O_RDWR`;
- `0400 + LOCK_EX` fails before `open` or `flock` and never attempts chmod or
  repair;
- path/fd identity, type, mode, size, link count, token content, and SHA-256
  are checked before acquisition, while held, and before release;
- optional `LOCK_NB` does not change the shared-versus-exclusive decision.

The execution root remains read-only inside the container. A host-side `0600`
lock therefore does not weaken the container writer-rejection test or add a
new writable bind.

## 4. Immutable failure and successor policy

Execution-v4 must receive a content-addressed pre-workspace failure bundle
that binds its complete identity and immutable snapshot, the held identity,
the exact three scheduler rows, empty terminal queue, exact log, absent raw
workspace, empty mutable roots, and zero compute/physics consumption.

Although its mutable roots remained pristine, execution-v4 has been used by a
real released scheduler job. It is therefore one-shot historical evidence and
must not be retried, receive readiness, create an intent, or run production.

A clean execution-v5 may be materialized only after that failure bundle is
sealed. It must bind the v4 failure, preserve every task/seed/simulation/runtime
identity, use the corrected `0600` portable lock, and begin with empty
`intents/`, `attempts/`, and `finalized/` roots. It may run exactly one new
held, non-array, no-Geant4 R3 preflight after held identity review.

A successful R3 probe retains its authenticated mountpoint in place and closes
that successor with `future_successor_required=true`. Consequently, a
successful execution-v5 is accepted preflight evidence, not a production
workspace. Any later BC-S1 production would require a separately reviewed and
authorized clean successor (expected execution-v6), outside this recovery.

## 5. Stop conditions

At every stage, stop without resubmission if identity, checksum, held-job,
accounting, raw-probe, or immutable-boundary validation fails. Never call
`sbatch` a second time for an ambiguous submission, never release an
unvalidated held job, and never reuse an execution after a released probe.

The recovery phase ends only when the successful no-Geant4 evidence bundle is
checksum-valid and records:

```text
accepted_compute_preflight_evidence true
geant4_invoked                     false
execution_closed                   true
future_successor_required          true
production_intents                 0
production_jobs                    []
```

## 6. Accepted completion checkpoint

The recovery ended with the following immutable execution-v5 and scheduler
identities:

```text
execution_id   sm-v1-production-bc-s1-execution-v5-704c313624aa
execution_hash dd774195e93a5c5e479815fd265c2de629fa71f22a30cae0a13284e0e828b73e
job_id         50561809
evidence_id    sm-v1-r3-container-probe-732cc54bbeb7
evidence_hash  732cc54bbeb7f42a2c537d7c343db6ca090b169b6ec700c21ec2d4b98af73c24
```

The full evidence hash above is read from the checksum-valid `evidence.json`
inside the accepted evidence bundle; it is not inferred from the twelve-digit
ID suffix. The bundle and its recursive checksum manifest passed semantic and
checksum validation on OSC. It records:

```text
accepted_compute_preflight_evidence true
apptainer_invoked                   true
geant4_invoked                      false
execution_closed                    true
future_successor_required           true
production_intent_count             0
production_attempt_count            0
```

Job `50561809` was the single authorized, non-array R3 compute preflight. It
proved the frozen container and mount-isolation path without invoking Geant4,
committing events, or consuming production seeds. Its retained authenticated
probe workspace closes execution-v5 permanently; execution-v5 is evidence and
must never become a production workspace.

Any later BC-S1 production therefore requires a new clean execution with its
own independently reviewed authority. The accepted next design is the
Phase-2C execution-v6 contract in
`steel-module-production-phase2c-v1.md`; this completed recovery record does
not itself grant that authority.
