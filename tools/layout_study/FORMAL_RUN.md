# Run the fixed first scientific scan on OSC

Read [FIRST_SCAN.md](FIRST_SCAN.md) for the prespecified endpoints and intervals.
This workflow uses 24 configurations × 1,000 events, divided into 240 independently
seeded 100-event tasks. The PR remains a draft while these results are reviewed.
Start only after the small qualification in [README.md](README.md) passes.

## 1. Freeze the PR source and build its runtime

Use the exact 40-character commit recorded in the PR, not a moving branch name.
Keep this checkout unchanged until the campaign is finished. Use a separate
checkout for subsequent PR edits or plotting development.

```sh
export PR_COMMIT=replace-with-the-recorded-40-character-commit
git checkout --detach "$PR_COMMIT"
export LAYOUT_REPO="$PWD"
export LAYOUT_RUNTIME="$LAYOUT_REPO/outputs/runtime-$PR_COMMIT"
export LAYOUT_PYTHON="$LAYOUT_WORK/python/bin/python"
```

`LAYOUT_WORK`, `LAYOUT_SIF`, `LAYOUT_DATA` and `OSC_ACCOUNT` are the paths/account
set in the OSC quickstart. The SIF must come from the pinned public x86_64 image;
record its acquisition URI and actual file hash. The dataset installer creates
`$LAYOUT_DATA/layout-study-SHA256SUMS`, which is the trusted reference for the
full file comparison. If using an independently validated existing data directory,
pass that reference explicitly with `--reference-data-manifest /path/to/SHA256SUMS`.

Request **one CPU and 4 GiB** for this frozen runtime build:

```sh
salloc --account="$OSC_ACCOUNT" --partition=cpu --constraint=40core \
  --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=4G --time=01:00:00
srun --pty bash
cd "$LAYOUT_REPO"
"$LAYOUT_PYTHON" tools/layout_study/build_runtime.py \
  --source-commit "$PR_COMMIT" --sif "$LAYOUT_SIF" --data-dir "$LAYOUT_DATA" \
  --output-dir "$LAYOUT_RUNTIME"
"$LAYOUT_PYTHON" tools/layout_study/run_local.py \
  --apptainer-image "$LAYOUT_SIF" --data-dir "$LAYOUT_DATA" \
  --executable "$LAYOUT_RUNTIME/build/OpNovice2" \
  --output-dir outputs/qualify-frozen-pr --events 10 --tile-thickness-mm 4
```

The builder checks the actual source bytes against Git, builds headlessly in the
container, hashes every dataset file and compares it with the trusted reference.
Only success produces `runtime.json`. Outputs include the actual build command,
compiler/version records, source and binary hashes, and a compute-node data
preflight receipt. Existing runtime/output directories are not overwritten.

The four-layout qualification must complete normally and pass both audits.
Exit the interactive compute shell/allocation after qualification. The following
input generation and submission can then run on the login node.

## 2. Prepare and submit the fixed inputs

The reference exclusion list includes the documented smoke and historical
engineering/benchmark seeds. Add any other engineering seed values you used
before preparing the campaign. Once registered, neither seeds nor inputs change.

```sh
cd "$LAYOUT_REPO"
export LAYOUT_CAMPAIGN="$LAYOUT_REPO/outputs/first-scan-$PR_COMMIT"
"$LAYOUT_PYTHON" tools/layout_study/scan.py prepare \
  --runtime "$LAYOUT_RUNTIME/runtime.json" \
  --excluded-seeds tools/layout_study/reference/engineering-seeds.json \
  --source-commit "$PR_COMMIT" --seed-start 1800000001 \
  --output-dir "$LAYOUT_CAMPAIGN"
export LAYOUT_MANIFEST_SHA=$(sha256sum "$LAYOUT_CAMPAIGN/manifest.json" | cut -d ' ' -f 1)

sbatch --parsable --account="$OSC_ACCOUNT" --partition=cpu --constraint=40core \
  --chdir="$LAYOUT_REPO" \
  --output="$LAYOUT_CAMPAIGN/slurm-%A-%a.out" \
  --error="$LAYOUT_CAMPAIGN/slurm-%A-%a.err" \
  tools/layout_study/osc_scan.sbatch
```

Record the returned array ID, source commit, manifest hash and `runtime.json`
with the PR progress update. The script specifies `--array=0-239`, without a `%`
concurrency cap. OSC/account/resource limits still apply. Every task requests
one CPU, 4 GiB and one hour, with a fixed 3,300-second process timeout.

A worker checks the frozen source, input, executable, SIF and data-preflight
identities, then executes exactly one 100-event process. A successful process
and its geometry/ROOT audit produce a **process-verified** receipt, not final
statistical acceptance. Failures are preserved and are not automatically rerun.

## 3. Collect and analyze after all tasks finish

Use the real array ID from submission. Save full, untruncated accounting:

```sh
export LAYOUT_ARRAY_ID=replace-with-the-actual-array-id
sacct -X -P -j "$LAYOUT_ARRAY_ID" \
  --format=JobID%40,JobIDRaw%40,State,ExitCode,AllocCPUS,NodeList \
  > "$LAYOUT_CAMPAIGN/sacct.psv"
```

Run collection and the bootstrap analysis in a compute allocation, using the
same source and Python environment. This work adds no simulation events.

```sh
"$LAYOUT_PYTHON" tools/layout_study/scan.py collect \
  --manifest "$LAYOUT_CAMPAIGN/manifest.json" --manifest-sha256 "$LAYOUT_MANIFEST_SHA" \
  --sacct "$LAYOUT_CAMPAIGN/sacct.psv" --array-job-id "$LAYOUT_ARRAY_ID"
"$LAYOUT_PYTHON" tools/layout_study/analysis.py \
  --manifest "$LAYOUT_CAMPAIGN/manifest.json" --manifest-sha256 "$LAYOUT_MANIFEST_SHA" \
  --audit-module tools/layout_study/audit.py \
  --audit-sha256 "$(sha256sum tools/layout_study/audit.py | cut -d ' ' -f 1)" \
  --output "$LAYOUT_CAMPAIGN/analysis"
```

Collection requires exactly 240 normal `COMPLETED / 0:0 / 1 CPU` allocations
and rechecks all outputs before writing accepted receipts. Analysis refuses an
incomplete scan, preserves zero-response events, and produces JSON summaries
plus bootstrap arrays. Keep source/runtime/input/output and analysis identities
with later plots. Do not interpret timing samples as scientific data or replace
broad intervals with an unregistered extra scan.
