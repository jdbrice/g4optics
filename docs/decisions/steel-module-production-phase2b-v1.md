# Steel Module Production Phase-2B Managed Execution Closure

Historical status at the original implementation-C checkpoint: implementation
complete and locally regression-tested; readiness evidence, formal intent, and
real submission were then still absent.

Postscript: implementation C and readiness R were subsequently frozen, and
the first real attempt exposed a non-portable cross-node inode assumption
before Geant4 started. This historical v1 contract is not retroactively
rewritten with the recovery behavior. The additive incident and successor
authority is documented in
`docs/decisions/steel-module-production-phase2b-recovery-v1.md`.

Study preset: `steel-module-scan-v1`

Planned implementation checkpoint: 2026-07-18

## Implemented artifact set

The implementation is intentionally split into four independently checked
parts:

- execution/control: canonical materializer, central validation library,
  two-step attempt manager, frozen worker/recorder, held-array batch entry,
  readiness proposal collector, and post-R readiness validator;
- evidence: managed whole-child finalizer, integrated event/seed audit, exact
  content-addressed `BC-ONLY-S1` checkpoint, and canonical-source loader;
- analysis/review: 10,000-resample checkpoint analyzer, sealed-pilot
  reconciliation and tail baseline, offline HTML/PDF renderer, and append-only
  progression recorder;
- gates: focused control, finalization/checkpoint, and analysis/review tests,
  all invoked by the existing top-level steel-module infrastructure checker.

The test scheduler is an in-process fake. A PATH sentinel demonstrates that
the complete integrated readiness suite never invokes `sbatch`, `scontrol`,
`squeue`, or `sacct`; no test fallback may convert fixture evidence into
accepted evidence.

## Purpose and authority boundary

Phase-2B closes the engineering path between the immutable Phase-2A BC-S1
plan and a later, explicitly confirmed Slurm submission. It does not change
the detector, physics list, executable, task registry, seed allocation,
statistical estimand, or accepted progression rules.

The Phase-2A authority remains the exact, read-only directory:

```text
/users/PAS2524/anolddriver66/g4optics-rn/campaigns/
  steel-module-production-bc-s1
```

It must never acquire a `campaign.json`, attempt directory, result, lock, or
finalized output. Phase-2B uses the separate canonical sibling:

```text
/users/PAS2524/anolddriver66/g4optics-rn/campaigns/
  steel-module-production-bc-s1-execution
```

The companion is not an ordinary campaign. Generic campaign submission must
continue to reject both the parent program and every program-managed child.

## Accepted execution contract

The formal execution companion binds the frozen production program,
authorization graph, Phase-2A lock and three Phase-2A artifacts, exact BC-S1
task and seed sets, runtime identities, and two distinct source trees:

- simulation source `cbc03814e80e8d0742a3bfb17921c118c6ff1585`, which
  remains the recorded simulation Git identity;
- a later Phase-2B control-plane implementation commit, which supplies the
  managed worker, recorder, state machine, and evidence tools.

Both sources are frozen into separate read-only archives. The existing
prebuilt `OpNovice2` executable remains unchanged. No Phase-2B tool modifies
`run_sipm_cavity_scan.sh` or the event schema merely to add control-plane
provenance; that provenance belongs in the managed intent and immutable task
result marker.

Formal execution remains hard-locked until a tracked
`steel-module-production-phase2b-v1.lock.json` exists and validates. The lock
must bind the code-release commit and protected blob hashes rather than rely on
a self-referential lock-carrier commit. A later clean descendant checkout is
permitted only when every protected blob remains bit-identical; the actual
invocation commit and tree are still recorded in each intent.

## Two-step attempt protocol

Preparing an intent and contacting Slurm are separate operations:

1. `prepare-intent --check-only` validates the exact selection without
   writing.
2. `prepare-intent --write-intent` writes one immutable, checksum-covered
   intent and prints its ID and semantic SHA-256. It never invokes Slurm.
3. `submit-intent` requires the exact intent ID and full hash plus an explicit
   submit flag. It revalidates every lock, source, runtime, task, prior event,
   and scheduler precondition before contact.

Formal scheduler settings are fixed: OSC account `PAS2524`, no inherited
environment, no requeue, an exact array shape, a unique identity-bearing job
name, and attempt-local output paths. Submission uses a held array. After a
single numeric job ID is returned, the control plane verifies job name,
account, array, command, work directory, and output path with Slurm before it
releases the job.

Attempt state is reconstructed exclusively from immutable, hash-chained event
files. There is no mutable status file, `HEAD`, or TSV authority. The event
immediately before `sbatch` is flushed and synced. Any crash, timeout,
non-zero result, unparseable output, or lost receipt after that event is an
ambiguous submission and blocks prepare, submit, resume, and retry.

Ambiguous reconciliation is fail-closed:

- one exact scheduler match may be adopted with immutable query evidence;
- multiple matches are a duplicate-submission incident and are never selected
  automatically;
- zero matches do not prove that no job was accepted. A no-job disposition
  requires two successful `squeue` and `sacct` snapshots at least ten minutes
  apart plus reviewer, rationale, and typed intent-hash confirmation;
- release uncertainty is resolved only against the already known held job.
  It never causes a second `sbatch` call.

Terminal Slurm accounting is frozen separately while it is still retained.
The whole-child finalizer reads only that checksum-valid snapshot and never
queries live scheduler state.

## Downstream evidence chain

The managed finalizer requires all submitted or adopted attempts to have
terminal accounting, then audits a unique valid success for every one of the
32 tasks. Duplicate valid successes require an explicit selection file. It
revalidates task identity, original seeds, run configuration, result marker,
summary totals, ROOT contents, and source/runtime hashes. It writes an atomic,
non-overwriting finalization containing task and configuration indices,
event/seed audits, accounting, selection provenance, validation, and recursive
checksums.

The formal Phase-2B checkpoint is only:

```text
state              BC-ONLY-S1
evidence mode      back-center-only
children           BC-S1
tasks / events     32 / 8,000
4 mm blocks        0-15
24 mm blocks       0-15
```

The content-addressed checkpoint references ROOT files by canonical path and
hash; it does not copy them. The internal library may represent future
contiguous `BC-S1...BC-Sn` prefixes, but Phase-2B formal commands reject every
other state and any `FIXED` task.

The cumulative analyzer reports the observed back-center net-response `24/4`
ratio from the new 4,000-event endpoint samples. It uses 10,000 independent
event-bootstrap resamples, NumPy PCG64, seed `20260715`, and a percentile 95%
interval. It calculates

```text
h = (CI_high - CI_low) / (2 * abs(point_ratio))
```

and exactly 32 leave-one-250-event-block-out shifts. The sealed 1,000-event
pilot is recomputed and reconciled with formal analysis-v2, but its events are
never pooled into the production estimate. For BC-S1, `narrowing` means the
current unrounded `h` is smaller than the sealed-pilot `h`.

Numerical eligibility remains the accepted SMS-019 policy:

- `h <= 10%` and maximum LOO shift `<= 10%` permits `stop-success` or
  `pause-review`;
- otherwise, `h > 10%`, maximum LOO shift `<= 20%`, and narrowing permits
  `continue` or `pause-review`;
- every other valid state permits only `pause-review`;
- invalid identity, checksum, audit, ROOT, or required metric evidence emits
  no recordable decision.

The analyzer never chooses a decision. Ratio direction, unity crossing, and
unity exclusion remain display-only diagnostics.

## Human review and decision record

The offline review is generated only from checksum-valid core analysis. It
contains a self-contained `index.html`, print-equivalent PDF, four PNG/PDF
figure pairs, review data, provenance, and independent checksums. It spells out
**Maximum leave-one-block-out shift (LOO)**, displays the 10% and 20% bands,
tail/zero diagnostics, precision trajectory, and endpoint interval, and has no
network resource, form, write control, unlock action, or Slurm action.

The append-only progression recorder requires an explicit tail disposition:

- `no-material-worsening` preserves numerical eligibility;
- `material-worsening` permits only `pause-review`;
- `unable-to-determine` also permits only `pause-review`.

Every record binds finalization, checkpoint, analysis, review, numerical
metrics, tail disposition, reviewer, rationale, selected decision, derived
authorization, and the previous decision hash. It records authorization
evidence only; it does not mutate a child, write a mutable unlock flag, or
submit work.

## Release sequence and current stopping point

Phase-2B has two distinct version-control checkpoints:

1. **Implementation commit C.** Code, documentation, adversarial fixtures,
   fake-scheduler tests, and the full synthetic downstream path are completed.
   Without a readiness lock, formal submission remains impossible.
2. **Readiness-lock commit R.** After C is pushed and pulled on OSC, run the
   full checker, materialize the empty formal execution companion and both
   frozen source archives, validate shared-filesystem locking and atomic
   writes, inspect Slurm capabilities without submission, and run a synthetic
   end-to-end dry run. The resulting tracked lock binds those exact hashes and
   records zero formal intents and zero job IDs.

R must protect the complete fixed critical-artifact set. Its validator compares
each file across the lock, the clean R checkout, and the frozen Implementation-C
control archive, and rechecks the exact Phase-2A lock digest. Readiness also
requires all three mutable execution roots (`intents/`, `attempts/`, and
`finalized/`) to be exactly empty; orphan evidence is a hard failure.
The readiness evidence must also include a successful isolated loader probe
started directly from the read-only, no-`.git` control archive. That probe
validates the relocated Phase-1/Phase-2A locks and runtime identities without
running Geant4 or contacting the scheduler.

Phase-2B ends after R is pushed, pulled, and revalidated on OSC. It must still
report:

```text
managed_submission_ready=true
automatic_submission=false
real_slurm_submission_performed=false
formal_intent_count=0
slurm_job_ids=[]
```

Creating the first real intent, reviewing its hash, and explicitly submitting
BC-S1 are a separate operational gate. Until then `FIXED` and
`BC-S2...BC-S4` remain locked.

The implementation checkpoint stops before both repository and scheduler
authority: this document does not itself certify commit C or create R. OSC must
first materialize the empty canonical companion from clean C, run the integrated
checker and shared-filesystem probe, and produce the non-authoritative readiness
proposal. R may be authored only from that reviewed proposal and must preserve
zero intents and zero job IDs. The formal validator then rechecks clean checkout,
protected blob hashes, C ancestry, fixed control-lock inode, runtime artifacts,
checker evidence, and the zero-submit boundary.
