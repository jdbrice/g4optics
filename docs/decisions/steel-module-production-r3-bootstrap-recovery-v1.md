# Steel-module production R3 bootstrap recovery v1

Status: implementation and OSC read-only failure capture complete; immutable
failure sealing and execution-v3 materialization are pending. This document is
an additive amendment to `steel-module-production-phase2b-recovery-v1.md`; it
does not alter the frozen Phase-2A plan, the original Phase-2B incident, or any
physics input.

## 1. Observed formal failure

The first formal R3 compute-node probe used:

- execution ID `sm-v1-production-bc-s1-execution-v2-50aefd35ac58`;
- execution hash
  `8352b7949657fb3161ec2e31ddf384997634ae6092f1f3e884ef95f7f22427de`;
- Slurm job `50544247`;
- job name `g4sm-r3-8352b7949657`.

The held scheduler identity was reviewed before release and matched the fixed
account, non-array shape, command, working directory, output path, resource
request, `Requeue=0`, and `JobHeldUser` state. After release, the parent and
batch step terminated `FAILED / 1:0`; the extern step completed. The Slurm log
contains:

```text
ModuleNotFoundError: No module named 'steel_module_production_r3_probe_lib'
```

Slurm copied the submitted Python file into its private spool as
`.../slurm_script`. Python consequently used the spool directory, rather than
the frozen control archive, as the script import directory. The failure
occurred at the top-level import before the probe loader, Apptainer, or Geant4
could run.

The accepted classification is `pre-apptainer-python-import-failure`:

```text
R3 probe workspace created       false
Apptainer invoked                false
Geant4 invoked                   false
production events consumed       0
production seeds consumed        0
production intent created        false
```

The four externally captured inputs are fixed by SHA-256:

```text
held scontrol  4cc7a063988818176cb5fe9c37a07956785183a10076ac5d11a93e0e6822d84a
sacct          0229887554c425327f4ac02d2290291cc209619980f8ffca4e5133a56c94aef5
squeue         e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
Slurm output   c825ed709aff6ab0fc9b41b7ada09a5262606d4217c59446e5218680e7b1d6b0
```

## 2. Immutable-history rule

Execution-v2 and job `50544247` are historical evidence. They must not be
deleted, overwritten, edited, or relabeled as a successful preflight. In
particular:

- do not replace the v2 frozen control archive;
- do not delete the held `scontrol` snapshot or Slurm log;
- do not feed the failure into the successful R3 accounting sealer;
- do not create an R4 readiness lock for v2;
- do not submit another probe or production intent from v2.

A dedicated content-addressed failed-preflight bundle must first bind the held
scheduler row, terminal accounting, empty terminal `squeue`, traceback, v2
static snapshot, empty mutable roots, absent raw probe workspace, and the zero
consumption classification.

## 3. Why an external wrapper is rejected

An untracked wrapper, `PYTHONPATH` injection, or `sbatch --wrap` command would
not be an acceptable repair. The v2 frozen verifier requires the original
direct-Python held command, and its production worker requires a lock-only R4
child of the v2 control commit. A new launcher or verifier in the live checkout
would therefore split scheduler evidence from the code that later authorizes
production.

The repair uses a new immutable execution generation instead.

## 4. Execution-v3 contract

The canonical recovery successor is:

```text
$WORK/campaigns/steel-module-production-bc-s1-execution-v3
```

It binds both predecessor histories, including the complete v2 recovery
authority rather than only its digest:

1. the original pre-simulation production incident for job `50532143`;
2. execution-v2 and the content-addressed failed R3 preflight for job
   `50544247`.

The following must be byte-for-byte or identity-equivalent to Phase-2A and
execution-v2:

- all 32 logical tasks and scan arguments;
- 8,000 requested events;
- the exact 64 production seeds;
- the simulation source archive;
- the executable, Apptainer image, Geant4 data manifest, and environment;
- the sealed-pilot and production-program bindings.

Only the control-plane source and execution-generation metadata change. V3
starts with empty `intents/`, `attempts/`, and `finalized/`, and remains
unsubmittable until a new compute preflight and a v3-specific lock-only R4
commit are accepted.

## 5. Copy-safe R3 launcher

V3 freezes a shell batch entrypoint. It deliberately does not infer its source
root from `$0` or `BASH_SOURCE`, because Slurm also copies shell scripts into
the spool. Instead it:

1. validates a non-array Slurm job identity;
2. requires the scheduler-established current working directory (`--chdir`) to
   be the canonical execution-v3 directory and deliberately distrusts
   `SLURM_SUBMIT_DIR`;
3. derives the frozen control archive and canonical R3 workspace from that
   working directory and the top-level execution hash;
4. validates that the scheduler job name is the hash-derived R3 name;
5. executes the original frozen Python runner by absolute path.

The accepted held scheduler command is the frozen shell entrypoint. Its
command, working directory, output path, job name, account, resources, held
state, and non-array shape are all checksum- and schema-bound.

The launcher invokes Apptainer only for the container-isolation probe and still
fixes `Geant4 invoked: false`.

## 6. R4 and later production

The future v3 R4 readiness record must distinguish the complete compute
preflight history:

```text
failed compute preflight jobs     [50544247]
accepted compute preflight job    <new single R3 job>
all compute preflight jobs        [50544247, <new job>]
```

It binds the failed-preflight bundle and the accepted R3 bundle separately.
The implementation commit that materializes v3 is frozen first; the R4 commit
must then be its single non-merge child and add only the readiness lock.

Only after that lock passes both the live manager and the frozen v3 worker may
the one-time `predecessor-retry` production intent be prepared. The whole-child
finalizer and checkpoint retain both failed predecessor histories but select
physics results only from a valid v3 production attempt.

## 7. Authorization boundary

Implementation, synthetic tests, failed-evidence sealing, and v3
materialization do not authorize another Slurm job. The replacement R3 held
submission, its release, the R4 lock, and the later production intent remain
separate human-reviewed actions.
