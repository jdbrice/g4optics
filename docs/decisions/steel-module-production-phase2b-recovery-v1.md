# Steel Module Production Phase-2B Recovery Amendment

Status: predecessor incident sealed on OSC and R2-R4 recovery infrastructure
implemented locally; successor execution has not yet been materialized on OSC;
R3 compute evidence, additive readiness lock, production intent, and retry
submission remain absent

Incident date: 2026-07-18

## Decision

The first managed `BC-S1` attempt is classified as a **pre-simulation
control-plane failure**.  It is not a Geant4 result and contributes zero events
to the production estimate.

The historical Phase-2B execution companion is closed to further production
attempts.  Recovery will use an additive, versioned successor execution; the
old implementation commit, readiness commit, execution manifest, intent,
event chain, Slurm job, logs, and terminal accounting remain immutable
predecessor evidence.

No seed is replaced.  The successor will reuse the exact Phase-2A mapping of
32 logical tasks, 8,000 events, and 64 production seeds only after the sealed
incident proves that the historical worker never invoked Geant4.

## Historical identities

```text
implementation C  20c9007f8cd3da27fe1b4fbd13cad8bdaefdc292
readiness R       5eaf22a23ee84d3bab3ac2539923d62cb8276bdb
execution ID      sm-v1-production-bc-s1-execution-193d261c3059
execution hash    d07a32a6afea7d2345b4f4ca45996fd88dde93a7cf8f9d875e0ba0e47e52cf7b
attempt ID        20260718T175107Z-initial
intent SHA-256    1232d0e69302634580b5e6d1ab4d1c725a7d6ede08e4181c717ba71adb494b9d
Slurm job         50532143
incident ID       sm-v1-bc-s1-pre-simulation-incident-0d8898f4e1ce
incident hash     0d8898f4e1ce5d6f4c6a9fede2e6e612f5c7723c7f59efa66abd481b1250ed9e
```

The account-case recovery that adopted and released job `50532143` remains
part of the immutable event chain.  It is not removed or rewritten by this
amendment.

## Observed OSC evidence

The login-node `.control.lock` still exactly matches the historical manifest:

```text
device=214 inode=958967 mode=600 size=0 links=1
sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

A later login session showed that even two OSC login nodes can report a
different numeric device/inode identity for this same shared path.  The first
recovery sealer therefore failed before scheduler access for the same reason
as the workers.  Historical sealing now has one narrowly scoped lock path:
it accepts only the exact execution, attempt, intent, job, readiness-bound
manifest/checksum identities, and original empty `0600` lock record above;
it ignores only the current node's numeric device/inode.  It still requires a
regular one-link file, `O_NOFOLLOW`, content/mode/size checks, `flock`, and
same-node path/descriptor identity checks before, during, and after the
critical section.  Ordinary v1 loaders and every production worker or
submission mutation retain the original strict behavior.

All 32 array logs contain exactly:

```text
Cannot run managed steel-module production task: managed execution
control-lock inode identity mismatch
```

The attempt has no `tasks/` directory, ROOT file, task-result marker, run
configuration, or summary.  The worker calls `load_execution_companion()`
before creating the task directory and before invoking Apptainer or Geant4.
The accepted incident disposition is therefore:

```text
Geant4 invocations          0
committed simulation events 0
production seeds consumed   0
```

OSC accounting exposes array identity as follows:

```text
logical JobID    JobIDRaw
50532143_1       50533282
...
50532143_31      50533312
50532143_32      50532143
```

There is no separate logical parent row.  The final task may reuse the parent
number as its numeric `JobIDRaw`; this does not make it a parent row.  A
terminal job may also disappear from `squeue`, causing `squeue -j 50532143` to
report `Invalid job id specified`.  Recovery therefore:

- derives array indexes only from logical `JobID`;
- preserves `JobIDRaw` as scheduler provenance;
- requires the exact logical set `1..32`;
- does not require a separate parent row;
- checks live activity by the unique job-name token rather than terminal job
  lookup by ID.

OSC also canonicalizes the submitted account `PAS2524` to `pas2524` in
`scontrol`.  Successor verification accepts only that exact ASCII-lowercase
representation (or the exact original spelling), records expected and observed
values in the immutable `job-verified` event, and rejects every other account.

## Root cause and lock protocol amendment

The v1 materializer recorded login-node `st_dev` and `st_ino` as persistent
lock authority, and every compute worker required those numeric values to be
identical.  OSC's shared filesystem does not promise that cross-node view.
The readiness suite exercised local subprocesses on the login node, so it did
not test this assumption on a compute node.

The successor uses portable lock protocol v2:

- manifest authority is the fixed path, regular-file type, mode, size,
  one-link requirement, and a random content token plus SHA-256;
- creation-time device/inode values are diagnostic only;
- each process performs `lstat`, `open(O_NOFOLLOW)`, `fstat`, and same-node
  path/file identity checks before and after `flock`;
- mode, link count, size, and token/hash are checked before, while holding,
  and after the critical section;
- symlink, hard-link, content, mode, and size changes are rejected; pathname
  replacement is detected at lock acquisition and again on critical-section
  exit.

The v2 protocol does not claim that an actor with arbitrary write access to
the whole execution root can never replace a same-content lock immediately
before acquisition, nor can an exit-time detection roll back an irreversible
action already performed inside the section. Therefore successor readiness
must additionally prove that compute workers cannot replace the static
execution root or `.control.lock`, and any scheduler call must occur only
after all lock and authority validation. Until those permissions and the
compute-node preflight pass, portable lock v2 is a local foundation rather
than accepted OSC execution authority.

The amendment removes only the false cross-node invariant.  It does not weaken
the content-addressed execution, intent, source, runtime, or event-chain
bindings.

## Staged recovery

### R1: seal the predecessor incident

The incident sealer has two explicit modes:

1. `--check-only` reads and validates the historical execution, intent, event
   chain, 32 logs, terminal `sacct`, and empty live `squeue` result.  It writes
   nothing.
2. `--seal` atomically freezes terminal accounting, appends the existing
   `terminal-accounting-frozen` event, and publishes one content-addressed
   incident bundle under:

   ```text
   $WORK/campaigns/steel-module-production-incidents/
     bc-s1-50532143-<incident-hash12>/
   ```

The bundle contains `incident.json`, the complete event chain, logical/raw
accounting, the 32-log index, and independent checksums.  It never calls
`sbatch` or `scontrol`.

Before either allowed `sacct`/`squeue` read, the formal sealer verifies the
readiness-bound canonical companion and byte hashes, the single historical
intent/attempt lineage, the exact six-step hash-valid release path (including
the account-case scheduler observation), all 32 exact failure logs, and the
absence of task, ROOT, or result output.  Its scheduler gateway accepts
only the two fixed read-only command lines recorded by readiness.  Accounting
publication and the terminal event share one exclusive historical recovery
lock; a crash after publication can only validate the v2 logical/raw Job-ID
snapshot and append the missing event, without another scheduler read.

### R2: materialize a successor execution

Only after the incident hash is frozen may a new sibling be created:

```text
$WORK/campaigns/steel-module-production-bc-s1-execution-v2
```

It must bind the predecessor execution, R lock, intent, job, event,
accounting, and incident hashes.  It must also prove that its task rows, scan
arguments, seed mapping, simulation source, executable, container image, and
Geant4 data identity exactly equal Phase-2A.

The first successor intent mode is `predecessor-retry`, not `initial`.  It may
select all 32 tasks only from the incident's zero-consumption disposition.

R2 uses a dedicated successor schema, loader, canonical path, and future
readiness namespace.  The historical Phase-2B readiness lock cannot authorize
the successor.  The R2 materializer has only explicit `--check-only` and
`--materialize` modes, exposes no path or scheduler override, and requires the
future additive readiness file to be absent.  It creates empty `intents/`,
`attempts/`, and `finalized/` roots and stops with `submission_ready=false`.

The frozen successor worker already understands the incident-bound schema and
the one-time `predecessor-retry` selection.  Its container view mounts the
static execution tree read-only and overlays only the exact logical task output
directory as writable.  R3 must test this permission boundary before any
successor readiness can be accepted.

Before creating a task directory, the worker also binds its host process to the
checksum-valid attempt event chain and the exact Slurm array parent, job name,
array index/count/min/max/step, and submit directory.  A direct/manual worker,
an unrelated job, or a differently shaped array cannot spend production seeds.

### R3: compute-node loader and container-isolation preflight

Before successor readiness can be accepted, one separately authorized,
single-task Slurm job must exercise the frozen successor control source and
portable lock on an OSC compute node.  It invokes the pinned Apptainer image
only as a no-Geant4 isolation probe.  The permission to use Geant4 does not
make a physics run useful at this gate, so R3 still fixes `geant4_invoked=false`.

The production worker and R3 share one frozen mount/environment builder.  It
uses `--cleanenv`, `--containall`, `--no-home`, disables
`hostfs,cwd,bind-paths`, and rejects every inherited `APPTAINER_*` or
`SINGULARITY_*` variable.  The execution root is mounted read-only.  One
external disposable workspace is overlaid as the only writable leaf under
`/work/g4optics-execution/attempts/.r3-probe-*`.

Before attempting any negative write, the probe verifies `/proc/self/mountinfo`
shows the execution alias read-only, that exact leaf read-write, and no other
read-write submount below the execution alias.  It then proves that the static
root, lock, `intents/`, non-probe `attempts/`, `finalized/`, the original OSC
absolute path, and an adjacent leaf are not writable, while the exact probe
leaf round-trips a checksum-valid challenge.  The production roots and the
complete execution snapshot must be byte-identical before and after.

The preflight job ID, externally captured held-job command/workdir/output/account
identity, login-creation and compute-node lock diagnostics, compute hostname,
result, terminal accounting, Apptainer binary path/hash/version, mount contract,
and checksums become a content-addressed evidence bundle.  Numeric device/inode
values are retained only as diagnostics; portable authority remains the lock
type/mode/link-count/content hash and same-node fd/path checks.  Production must
use the exact Apptainer identity accepted by R3.  A login-node subprocess or a
collection of unverified booleans is not a substitute.  R3 tooling does not
submit/query Slurm itself; the held `scontrol` row and terminal scheduler text
are supplied to a separate offline freezer and then sealed.

### R4: additive recovery readiness

A new tracked readiness lock binds implementation, incident, successor, and
compute-preflight evidence.  It distinguishes historical scheduler contact
from successor state:

```text
predecessor production jobs       [50532143]
compute preflight jobs             [<preflight job>]
successor production intent count  0
successor production jobs          []
automatic submission               false
```

The old R file is never edited.  The successor remains unsubmittable until the
new additive lock is reviewed, committed, pushed, pulled, and revalidated.
R4 recursively revalidates the complete R3 bundle and its held/terminal
scheduler evidence.  Before the first intent it requires the same pristine
execution snapshot observed by R3.  After a checksum-valid intent exists it
permits only explained intent/attempt journals and a checksum-valid finalized
bundle, rejecting orphan mutable roots without imposing the impossible
pre-intent snapshot on legitimate task output.  R4 accepts only one clean
non-merge Git commit whose sole R2-to-R4 delta is the new tracked readiness
lock.  A syntactically valid evidence hash alone cannot authorize submission.

Formal admission deliberately has two repository-root policies. Historical
failure sealing and live successor validation use the canonical live checkout,
because the Phase-2A binding records absolute lock paths and historical Git
objects. A frozen compute worker instead uses the checksum-bound
`sources/control` archive and reuses the predecessor already admitted in that
frozen context; it must not recursively reinterpret the archive as a live Git
checkout. Before any R3 submission, the v3 read-only validator performs both
the live/full admission and the exact frozen-worker admission on the login node
and requires their execution ID, hash, manifest, tasks, and scan arguments to
agree.

## Prohibited shortcuts

- Do not modify or replace the old `.control.lock`, manifest, source archive,
  readiness lock, intent, event files, or logs.
- Do not create `retry-failed` under the old execution; its frozen worker still
  contains the v1 bug. The current manager also rejects new prepare/submit
  operations for the exact historical execution ID and hash.
- Do not label the failed scheduler tasks as successful or cancelled.
- Do not generate replacement seeds.
- Do not create a production checkpoint or progression decision from the
  predecessor incident.
- Do not submit the successor before its compute preflight and additive
  readiness lock are complete.

## Current stopping point

The predecessor incident is sealed as accepted evidence with 32 terminal
`FAILED / 1:0` tasks, zero committed events, and zero consumed production
seeds. Execution-v2 was then materialized, but formal R3 job `50544247` failed
in Slurm's copied Python wrapper before Apptainer or Geant4. Its read-only
evidence is fixed by the additive
`steel-module-production-r3-bootstrap-recovery-v1.md` amendment.

Execution-v2 is therefore permanently closed. The current recovery target is
execution-v3, which must bind both failed histories and use the copy-safe shell
R3 launcher. No replacement R3 job, v3 R4 lock, production intent, or physics
retry is authorized merely by the implementation or evidence-sealing steps.
