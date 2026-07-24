# Steel-module R3 writer-probe recovery v1

Status: the writer-probe root cause is fixed and the subsequent GPFS
capability audit requires successful execution-v4 to be retained and closed
in place. Formal OSC rejection sealing, v4 materialization, and the one-job R3
preflight remain pending. No production intent, future-successor
materialization, or Geant4 production task is authorized by this document.

This is an additive amendment to
`steel-module-production-r3-bootstrap-recovery-v1.md`. It does not modify any
physics input, Phase-2A task, event count, or production seed.

## 1. Observed execution-v3 preflight

The first correctly admitted execution-v3 R3 probe used:

- execution ID `sm-v1-production-bc-s1-execution-v3-e3bd626fe694`;
- execution hash
  `30d01a2a03d97f3cc5bb54cf1364d5259ccabc0fbfe2aeefa04d59e441b9781a`;
- Slurm job `50548308`;
- job name `g4sm-r3-30d01a2a03d9`.

The held scheduler identity was reviewed before release. The job ran
Apptainer on an OSC compute node without invoking Geant4. The parent and batch
step terminated `FAILED / 1:0`; the extern step completed. The raw probe
recorded `probe_passed=false` with exactly these two false report keys:

```text
static_writer_open_rejected
control_lock_writer_open_rejected
```

All mount-topology, create-rejection, original-path, adjacent-path, challenge,
round-trip, runtime, portable-lock, and execution-snapshot checks passed. The
execution snapshot was byte-identical before and after the probe. Therefore
the failure is classified as a probe-instrumentation failure, not evidence
that either frozen file was modified or that the execution root was writable.

The externally frozen inputs are:

```text
held scontrol  4cd17d0100d51b11b17b556d3173206702065f6e0ea241b130fd3052100a3f55
Slurm output   4d2f0fc4e62e05c9e2a5bde98a61ca3f2fe41e2452f5e7d5cff7ead15f25ef00
sacct          4c44b4cd8b487b82686b9d5fb19b2ad371d8391b69a3e6fb351de812e0e28e7a
squeue         e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
probe result   d7317c8bea60f2e988c79daaba5f4e70dcb35a4e2ed13719c8c002ea451b3b5a
```

## 2. Root cause

The container helper used this condition:

```bash
if (exec 9>>"${target}"; exec 9>&-) 2>/dev/null; then
```

Commands used as an `if` test are exempt from Bash `errexit`. When the writer
open was correctly denied, Bash continued to the descriptor-close command.
The close succeeded and replaced the failed open status with zero, so the
helper falsely reported the file as openable.

The fixed helper tests only the writer open inside one subshell:

```bash
if (exec 9>>"${target}") 2>/dev/null; then
```

The regression suite exercises an absent-parent target, a mode-0400 file, a
writable file, inherited descriptor 9, and copied-launcher nonzero exit-code
propagation. The focused R3 checker passes with the fix.

## 3. Immutable-history rule

Execution-v3 and job `50548308` remain immutable rejected-preflight history.
In particular, do not:

- delete, move, rename, or overwrite its raw workspace;
- edit its frozen control source, manifest, lock, launcher, or log;
- feed its failed raw result into the accepted-preflight sealer;
- omit it from later compute-preflight history;
- create a production intent under execution-v3.

A content-addressed rejection bundle must bind the raw workspace, held
identity, terminal accounting, empty terminal queue, Slurm log, current
execution snapshot, exact false-key set, and zero event/seed consumption.

## 4. Why execution-v4 is required

The bug is frozen inside execution-v3. An external repair wrapper could run a
green diagnostic, but execution-v3's frozen worker and R4 validator would
reject its command identity, updated code, and expanded preflight history.
Making that external path authoritative would create a split control plane.

The recovery therefore uses a new canonical sibling:

```text
$WORK/campaigns/steel-module-production-bc-s1-execution-v4
```

Execution-v4 must bind execution-v3 and the sealed `50548308` rejection. It
must preserve, byte-for-byte or by exact identity:

- all 32 logical tasks and scan arguments;
- 8,000 requested events and all 64 production seeds;
- the simulation source;
- the executable, Apptainer image, Geant4 data manifest, and environment;
- the sealed-pilot, production-program, original production incident, and
  earlier R3 failure lineage.

The earlier job `50544247` and rejected probe `50548308` are compute-preflight
history. Job `50547698` is retained separately as a pre-probe administrative
launcher rejection; it must not enter compute-preflight, physics-result, or
terminal-accounting lists.

Only the control-plane source and execution-generation metadata may change.
Execution-v4 begins with empty `intents/`, `attempts/`, and `finalized/`.

## 5. Next accepted-preflight boundary

After the rejection bundle and execution-v4 pass local and OSC validation, one
separately reviewed, held, non-array R3 job may be submitted. It remains a
no-Geant4 container-isolation preflight. A successful bundle must record:

```text
failed compute preflights   [50544247, 50548308]
accepted compute preflight  [<new execution-v4 job>]
all compute preflights      [50544247, 50548308, <new execution-v4 job>]
production intents/jobs     0 / []
```

The successful probe writes a content-bound read-only marker through the exact
leased mountpoint descriptor and leaves that mountpoint in place. A
byte-identical copy is stored with the raw probe evidence. The resulting
execution-v4 boundary records:

```text
execution_closed          true
future_successor_required true
rename_performed          false
directory_entry_removed   false
deletion_performed        false
```

This is the terminal state of execution-v4. It must not receive a recovery
readiness lock, production intent, or production task. Any later BC-S1
production requires a separately authorized clean successor (expected to be
execution-v5) that binds the accepted v4 evidence and begins with empty mutable
roots. Implementing or authorizing that successor is outside this document.

## 6. GPFS publication and mountpoint policy

OSC capability probes showed that GPFS returned `EINVAL` for both
`renameat2(RENAME_NOREPLACE)` and `renameat2(RENAME_EXCHANGE)`, including the
same-parent directory-publication shape. Plain rename could replace an empty
placeholder, but an open descriptor for the replaced placeholder became
`ESTALE`. These results rule out rename-based strict mountpoint retirement.

The probe mountpoint therefore uses the deletion-free in-place policy above.
Generic content-addressed evidence publication keeps its no-replace fast path;
only Linux `EINVAL`, `ENOSYS`, or `EOPNOTSUPP` may enter the GPFS fallback.
Under the existing exclusive sibling lock, that fallback exclusively creates
and authenticates an empty target placeholder, atomically moves the
authenticated source over that owned placeholder, and verifies that the final
target is the original source inode. Pre-existing targets, source changes,
non-empty placeholders, unrelated errors, and detectable races all fail closed
without rollback. This publication fallback is a cooperative-writer protocol;
GPFS provides no directory compare-and-swap guarantee against a same-UID
process that deliberately ignores the sibling lock.
