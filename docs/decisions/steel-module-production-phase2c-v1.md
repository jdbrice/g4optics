# Steel Module Production Phase-2C: Production-ready execution-v6

Status: D6 decision checkpoint accepted and C6 implementation complete; OSC
preflight, clean execution-v6 materialization, and R6 readiness remain pending.

Decision checkpoint date: 2026-07-19

This is an additive successor to the completed R3 GPFS recovery recorded in
`steel-module-production-r3-gpfs-flock-recovery-v1.md`. It defines how the
accepted execution-v5 preflight evidence may lead to a clean, future-submittable
BC-S1 execution-v6. It does not itself create scheduler or production
authority.

## 1. Objective and fixed boundary

Phase-2C converts the accepted execution-v5 R3 evidence into an independently
validated execution-v6 while keeping the future production workspace pristine:

```text
accepted v5 evidence
        -> production-critical C6 snapshot
        -> sacrificial preflight twin
        -> held no-Geant4 R3 -> twin closed
        -> byte-equivalence proof
        -> clean execution-v6
        -> lock-only R6 readiness
        -> STOP
```

The authorized BC-S1 study remains exactly:

```text
layout                    back-center
tile thicknesses          4 mm and 24 mm
blocks per configuration  16
events per block          250
logical tasks             32
requested events          8,000
production seeds          the original 64-seed Phase-2A mapping
first production mode     predecessor-retry
```

Simulation source, physics, executable, runtime image, Geant4 data, and
environment identities remain unchanged. Execution-v5 and every earlier
execution are permanently closed, read-only historical evidence.

Each sacrificial twin may use at most one separately and explicitly authorized
held, non-array, no-Geant4 compute preflight. A checksum-valid failed twin may
lead to a new content-addressed retry twin and a new authorization, but never to
a second `sbatch` for the same twin. Phase-2C does not create a production
intent, submit BC-S1 production, invoke Geant4, produce production results, or
unlock `FIXED`, `BC-S2`, `BC-S3`, or `BC-S4`. It ends at an R6 readiness lock
with zero production intents and zero production Slurm jobs.

## 2. Accepted predecessor evidence

The completed recovery supplies the immutable predecessor:

```text
execution_id   sm-v1-production-bc-s1-execution-v5-704c313624aa
execution_hash dd774195e93a5c5e479815fd265c2de629fa71f22a30cae0a13284e0e828b73e
R3 job         50561809
evidence_id    sm-v1-r3-container-probe-732cc54bbeb7
evidence_hash  732cc54bbeb7f42a2c537d7c343db6ca090b169b6ec700c21ec2d4b98af73c24
```

The checksum-valid evidence records Apptainer invoked, Geant4 not invoked,
execution-v5 closed, and zero production intents and attempts. The complete
evidence hash, rather than only its shortened ID suffix, is an input identity
for every Phase-2C object.

## 3. Identity model and canonical objects

Phase-2C uses these versioned authority and evidence schemas:

- `steel-module-production-phase2c-preflight-twin-v1`;
- `steel-module-production-managed-execution-v6`;
- `steel-module-production-phase2c-preflight-evidence-v1`;
- `steel-module-production-phase2c-preflight-failure-evidence-v1`;
- `steel-module-production-phase2c-production-equivalence-v1`;
- `steel-module-production-phase2c-c6-osc-acceptance-v1`;
- `steel-module-production-phase2c-readiness-lock-v1`.

The canonical OSC locations are:

```text
$WORK/campaigns/steel-module-production-bc-s1-preflight-v6
$WORK/campaigns/steel-module-production-bc-s1-preflight-v6-retry-<NN>-<hash12>
$WORK/campaigns/steel-module-production-bc-s1-execution-v6
$WORK/evidence/steel-module-production-phase2c/
$WORK/evidence/steel-module-production-phase2c/c6-acceptance/c6-acceptance-<hash12>/
hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json
```

Content-addressed identities use these forms:

```text
sm-v1-production-bc-s1-preflight-v6-<hash12>
sm-v1-production-bc-s1-preflight-v6-retry-<NN>-<hash12>
sm-v1-production-bc-s1-execution-v6-<hash12>
sm-v1-phase2c-preflight-<hash12>
sm-v1-phase2c-preflight-failure-<hash12>
sm-v1-phase2c-c6-acceptance-<hash12>
```

The preflight twin and execution-v6 share one
`production_equivalence_hash`. It covers all production-relevant bytes and
semantics:

- `tasks.tsv`, `scan_args.txt`, and the complete task/seed mapping;
- recursive checksum manifests for simulation and control sources;
- runtime image, Geant4 data, executable, and environment identities;
- critical blob hashes for the production worker, batch launcher, task-result
  recorder, container builder, loaders, and readiness verifier;
- the portable-lock protocol, `0600` mode, empty-content hash, size, and
  one-link policy;
- account, array shape, resources, held submission, no-requeue, and clean
  export policy.

Instance-specific data is deliberately excluded from the equivalence hash:
execution ID and path, twin/v6 role, numeric control-lock device/inode values,
the R3 job and journal, and mutable-root records. Device and inode remain
diagnostic only; lock protocol, type, mode, content, size, and link count remain
strict authority.

## 4. Materialization and immutable publication

The formal interfaces are:

```text
materialize-preflight-v6 --check-only | --materialize
validate-preflight-v6

materialize-execution-v6 --check-only | --materialize
validate-execution-v6
```

Formal mode exposes no path, source, account, task, or scheduler override. The
twin binds the accepted execution-v5 evidence but can never acquire production
authority. Execution-v6 may be materialized only after a successful,
checksum-valid Phase-2C preflight evidence bundle closes that twin.

After the successful preflight, the execution-v6 materializer copies frozen
sources exclusively from the checksum-valid twin static tree. It must not
re-archive a later checkout. This makes the production equivalence proof a
byte-level relationship rather than an assertion about two separately made
archives.

The twin and execution-v6 each receive a newly created `0600` portable lock;
no historical inode is copied. Publication uses a sibling temporary directory,
flushes file and directory data, and performs a no-replace atomic rename.
Existing targets, symlinks, path escape, concurrent publication, and partial
output all fail closed.

Execution-v6 begins with exactly empty `intents/`, `attempts/`, and
`finalized/` roots. Its validator re-proves the complete chain:

```text
Phase-2A
  -> original pre-simulation incident
  -> execution-v2/v3/v4 histories
  -> successful execution-v5 R3 evidence
  -> Phase-2C preflight twin
  -> successful Phase-2C R3 evidence
  -> production equivalence hash
  -> clean execution-v6
```

## 5. Isolated Phase-2C preflight control plane

The preflight manager is separate from the production manager and supports:

```text
status
prepare --check-only | --write-authorization
submit --check-only | --submit
verify-held
release --check-only | --release
reconcile
freeze-accounting
```

Its immutable journal lives under the external Phase-2C evidence root. It does
not prewrite an intent or attempt into either the sacrificial twin or the clean
execution-v6.

The scheduler contract is fixed:

```text
shape          one non-array task
submission     --hold --parsable --no-requeue --export=NONE
account        PAS2524 (scheduler reads may return ASCII lowercase pas2524)
resources      1 CPU, 1 GiB, 10 minutes
job name       g4sm-p2c-<twin-hash12>
payload        frozen no-Geant4 launcher
```

There is no command, source, path, account, or resource override. Held identity
must be verified before a separately authorized release. A successful evidence
bundle must prove:

```text
apptainer_invoked           true
geant4_invoked              false
events_consumed             0
production_seeds_consumed   0
twin_execution_closed       true
production_equivalence_hash exact
```

An ambiguous or failed submission never triggers another automatic `sbatch`.
Only the already submitted job may be reconciled. A terminal failed job is
eligible for reviewed failure sealing after terminal accounting is frozen. A
zero-match ambiguity is retry-eligible only after two `squeue` plus `sacct`
observations separated by at least ten minutes. Multiple matching scheduler
jobs enter permanent quarantine and are not retry-eligible.

If held-job identity verification itself fails, the rejected job is never
released or adopted. The manager deliberately has no automatic cancellation
path: an operator must obtain separate authorization to cancel the exact job
ID recorded in the immutable journal, wait for terminal accounting, and record
that intervention in the reviewed failure rationale before sealing the twin.

After failure evidence is sealed, that twin is permanently closed and cannot
materialize execution-v6. A retry requires a new content-addressed twin, copied
only from the checksum-valid failed twin, and a new explicit preflight
authorization. Each retry twin again has a one-job limit. Failed job identities
remain nonproduction exclusions in the final lineage; retry does not create an
execution-v7 production chain.

## 6. Execution-v6 production admission

The unified production manager and frozen worker may formally target only the
canonical execution-v6. Before R6, the manager, worker, recorder, finalizer,
and downstream tools must reject the execution before creating a task
directory, invoking Apptainer, or invoking Geant4.

After valid R6 readiness, the first possible production intent is fixed to
`predecessor-retry`. It must select all 32 tasks, all 8,000 events, and the
original 64 seeds. `initial`, one-task smoke, any task subset, and altered seed
mapping are rejected. Future `resume` or `retry-failed` becomes eligible only
after a checksum-valid terminal production attempt exists.

A predecessor-retry intent cancelled before scheduler contact, or a submission
ambiguity closed by the required two zero-match observations, consumes no
production authority. A later exact `predecessor-retry` may replace a lineage
containing only those harmless closed intents. Any recorded job, active state,
terminal production attempt, or permanent quarantine prevents that shortcut.

The production array remains held after submission verification. Reconciliation
may recover or confirm that held identity but cannot release execution-v6;
only the separately invoked, checksum-bound `release-intent` route may call
`scontrol release`. Immediately before that call, the immutable journal records
`release-invoked`; workers may admit only that exact verified state or the
subsequent `job-released` state. Pre-release ambiguity remains inadmissible.
An uncertain return after invocation retains worker authority for the same
already-released array and must be reconciled without another `sbatch`.

The Phase-2C implementation must route the complete future evidence path for
execution-v6 without exercising it in this phase:

- task-result recording and terminal-accounting freeze;
- whole-child finalization with integrated event and seed audit;
- exact `BC-ONLY-S1` checkpoint construction;
- production checkpoint analysis;
- offline review rendering;
- append-only progression recording.

These are implementation and synthetic-test obligations, not authorization to
create real production evidence during Phase-2C.

## 7. C6 and R6 release model

Phase-2C has three version-control checkpoints:

1. **D6 decision checkpoint.** This English record and the completed GPFS
   recovery checkpoint are committed separately before implementation. A local
   ignored Chinese mirror is maintained for operator readability.
2. **C6 implementation commit.** Code, documentation, adversarial fixtures,
   fake-scheduler tests, and synthetic execution-v6 downstream validation are
   frozen. No tracked R6 readiness lock exists yet.
3. **R6 readiness-lock commit.** After OSC accepts C6, one twin's authorized
   preflight succeeds, evidence is sealed, and a clean equivalent execution-v6
   is materialized, the reviewed non-authoritative proposal supplies the sole
   tracked delta in R6. Any earlier failed preflight jobs remain sealed in retry
   lineage and do not become production authority.

R6 must be the direct non-merge child of final C6. Its only tracked difference
from C6 is:

```text
hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json
```

The lock binds C6 commit/tree and critical blobs, the complete execution-v5
evidence identity, twin identity, accepted preflight job/accounting/evidence,
the production equivalence hash, the clean execution-v6 identity and empty
mutable snapshot, task/event/seed and source/runtime identities, and OSC
checker evidence. The required terminal state is:

```text
managed_submission_ready    true
automatic_submission        false
production_intent_count     0
production_slurm_job_ids    []
preflight_slurm_job_ids     [<accepted job>]
real_production_submission  false
```

The R6 formal validator proves the lock-only direct-child relationship, clean
checkout, protected byte identities, preflight evidence, equivalence, and zero
production contact. It disables Git rename detection and requires one literal
add-only `100644` readiness-lock blob, while C6 must contain no lock at that
path. It also requires exactly one checksum-valid current-C6 acceptance bundle
inside the canonical external evidence root; an external copy, symlink, or
second current-C6 bundle invalidates readiness.

## 8. Required verification

Automated fixtures and OSC acceptance must cover, at minimum:

- tamper of execution-v5 evidence, twin, equivalence data, execution-v6, or
  R6 identity/checksum/path;
- symlink/path escape, partial publication, existing target, and concurrent
  publish rejection;
- mismatch of any production-critical blob between twin and execution-v6;
- diagnostic-only device/inode handling while enforcing lock protocol, mode,
  content, size, and link count;
- fake-scheduler success, timeout, invalid response, zero/one/multiple-job
  reconciliation, release ambiguity, and account-case comparison;
- forbidden-command sentinels proving fixtures never call real `sbatch` or
  `scontrol`;
- twin closure without creating or mutating execution-v6;
- refusal to materialize execution-v6 after failed preflight;
- exact empty mutable roots after successful execution-v6 materialization;
- pre-readiness rejection before task directory, Apptainer, or Geant4;
- exact first-intent selection of 32 tasks, 8,000 events, and 64 seeds under a
  valid R6 fixture, while rejecting smoke tasks and subsets;
- synthetic execution-v6 finalizer/checkpoint/analyzer end to end;
- regression coverage for v1-v5, the historical incident, pilot, production
  program, and v1/v2 analyzers.

OSC acceptance requires a clean C6 checkout; passing top-level and focused
checkers; a content-addressed C6 OSC acceptance bundle that records both passes
and zero scheduler/Geant4 contact; twin materialization and validation;
separately authorized held-job identity and release; terminal accounting and
evidence validation; execution-v6 equivalence and empty-root validation; and
final verification of the lock-only R6 child.

## 9. Rollout and mandatory stop

The fixed operational order is:

1. commit and push D6;
2. implement, test, commit, and push C6;
3. pull clean C6 on OSC and run all checkers;
4. freeze and validate the content-addressed C6 OSC acceptance bundle;
5. check then materialize the preflight twin without scheduler contact;
6. obtain separate human authorization and write the preflight authorization;
7. inspect the submit check, then explicitly submit that twin's held preflight;
8. verify held identity and obtain separate human authorization to release;
9. freeze terminal accounting and seal either success or reviewed failure
   evidence;
10. for a retry-eligible failure, materialize a new retry twin and return to
    step 6; for permanent quarantine, stop without execution-v6;
11. materialize and validate clean execution-v6 from the accepted twin;
12. produce and review the non-authoritative R6 proposal;
13. create, push, pull, and formally validate the lock-only R6 commit;
14. stop.

Phase-2C stops before any production intent, BC-S1 production `sbatch`, Geant4
invocation, production finalization or analysis, or later-child unlock. Only a
subsequent phase may proceed through read-only status, exact
`predecessor-retry` intent check, explicit intent write and hash review,
submission check, separate confirmation, and a held 32-task BC-S1 production
array.
