# OSC Apptainer/Slurm Runs

`.sif` means Singularity Image Format. It is the single-file container image used by Apptainer/Singularity on clusters, roughly serving the role that a Docker image serves locally.

Build the Geant4 image on OSC:

```bash
apptainer build geant4.sif docker://carlomt/geant4:11.4.2-almalinux9
```

Run one smoke scan from the repository root:

```bash
hpc/osc/run_scan_apptainer.sh
```

Run an explicit scan:

```bash
hpc/osc/run_scan_apptainer.sh full custom --events 1 --source-mode gps \
  --tank-size "100 100 5 mm" \
  --x-min -5 --x-max 5 --y-min -5 --y-max 5 --step 5 --grid-unit mm
```

Submit the Week 5-8 smoke matrix as a Slurm array:

```bash
SCAN_ARGS_FILE=hpc/osc/scan_args_week5_8_smoke.txt \
  sbatch -A YOUR_ACCOUNT --array=1-7 hpc/osc/submit_scan.sbatch
```

Explicit scan arguments passed after `submit_scan.sbatch` take precedence over `SCAN_ARGS_FILE`.

## Realistic-Neutron Campaigns

The realistic detector study uses a dedicated immutable campaign workflow. A
formal campaign pins Geant4 `11.4.2`, the Apptainer image, a Geant4 dataset
manifest, a prebuilt `OpNovice2` executable, the clean Git commit, every task
configuration, and every random seed pair.

The event audit and bootstrap analysis require Python 3, NumPy, and uproot on
the login node where finalization is performed. Check that environment before
submitting an expensive stage:

```bash
python3 -c 'import numpy, uproot; print(numpy.__version__, uproot.__version__)'
python3 hpc/osc/check_realistic_neutron_infrastructure.py
```

Any change to the event schema requires rebuilding the frozen executable and
rerunning the six-task `geometry-smoke` stage. Old smoke results remain useful
historical artifacts but do not validate a newer executable/schema pair.

### Optional one-time geometry view

Because the steel volume is new, a one-time interactive view of the `4 mm` and
`16 mm` production-size geometries is recommended. It is intentionally not an
acceptance gate; `--prepare-only` can still preserve the exact macro and run
configuration on a machine without a Geant4 GUI build.

```bash
cd test/OpNovice2
cmake -S . -B build-gui -DWITH_GEANT4_UIVIS=ON
cmake --build build-gui -j4
python3 visualize_realistic_neutron_geometry.py --tile-thickness-mm 4
python3 visualize_realistic_neutron_geometry.py --tile-thickness-mm 16
```

For the independent `steel-module-scan-v1` study, use its separate
visualizer. Omitting `--sipm-layout` prepares and opens all four accepted
layouts in sequence:

```bash
cd test/OpNovice2
python3 visualize_steel_module_scan_geometry.py --tile-thickness-mm 16
```

The visual macro leaves `/run/beamOn 0` and provides both a close SiPM
detail and a wider steel overview. Use `--prepare-only` on a non-GUI host.

### Steel-module campaign workflow

`steel-module-scan-v1` has an independent campaign schema and must not be
generated or finalized with the historical `realistic-neutron-v1` tools. Run
the deterministic preset and campaign checks before freezing a build:

```bash
python3 hpc/osc/check_steel_module_scan_preset.py
python3 hpc/osc/check_steel_module_campaign_infrastructure.py
```

After the candidate build and the electron differential regression below pass,
the first formal stage is a fixed 18-task geometry smoke: six thicknesses times
three SiPM layouts, one full-optical event each, with the `500 mm` absorber.
Generate it only from the clean candidate commit and the frozen OSC Geant4
`11.4.2` artifacts:

```bash
python3 hpc/osc/generate_steel_module_campaign.py \
  --out-dir /path/to/campaigns/steel-module-geometry-smoke \
  --stage geometry-smoke \
  --campaign-seed 20260715 \
  --environment-mode osc-production \
  --geant4-version 11.4.2 \
  --image /path/to/geant4-11.4.2.sif \
  --g4-data-manifest /path/to/g4-data-manifest.json \
  --build-artifact /path/to/frozen/OpNovice2

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-geometry-smoke \
  --check-only
```

Submit through the steel-module wrapper so the immutable attempt journal and
the scan-specific result recorder are selected automatically:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-geometry-smoke \
  --account PAS2524 \
  --g4-data-root /path/to/geant4-data/11.4.2 \
  --frozen-root /path/to/frozen-campaign-sources
```

After all tasks complete, one finalizer command verifies artifact identities,
the v4 run-config contract, all ROOT event-level causal invariants, the four
per-SiPM fields, and the Geant aggregate summaries. It writes both the
configuration summary and `event_audit.json` atomically:

```bash
python3 hpc/osc/finalize_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-geometry-smoke
```

Finalization requires Python 3, NumPy, and uproot on the login node. The
accepted frozen commit `88f15eace17137475310913d585a96908a493c72` passed the
electron differential regression and the formal 18-task geometry smoke
(`sm-v1-geometry-smoke-d77d801b947c`). The four-configuration cost-envelope
benchmark is fixed at 100 events per task:

```bash
python3 hpc/osc/generate_steel_module_campaign.py \
  --out-dir /path/to/campaigns/steel-module-benchmark \
  --stage benchmark \
  --events 100 \
  --campaign-seed 20260715 \
  --environment-mode osc-production \
  --geant4-version 11.4.2 \
  --image /path/to/geant4-11.4.2.sif \
  --g4-data-manifest /path/to/g4-data-manifest.json \
  --build-artifact /path/to/frozen/OpNovice2
```

The benchmark envelope is exactly `4 mm back-center`, `24 mm back-center`,
`24 mm edge-center`, and `24 mm back-four`. Campaign
`sm-v1-benchmark-378e088c618d` passed finalization. Its four task times were
`23`, `217`, `222`, and `279 s`; the slowest task was `24 mm back-four`, whose
linear 250-event projection is `697.5 s` (`11.625 min`). This supports the
accepted convergence-pilot execution block of 250 events.

The checked-in pilot selection is
`hpc/osc/configurations/steel-module-convergence-pilot-v1.tsv`. It contains the
18 nominal `500 mm` configurations plus `200/300 mm` absorber checks at the
`4/24 mm` thickness endpoints for every layout: 30 configurations total. Four
independent 250-event seed blocks produce 1,000 events per configuration, 120
logical tasks, and 30,000 events. The TSV has this exact header:

```text
tile_thickness_mm\tsipm_layout\tabsorber_transverse_mm
```

The normalized selection is copied into the immutable campaign and checksummed.
Generate and validate the accepted pilot from the repository root:

```bash
python3 hpc/osc/generate_steel_module_campaign.py \
  --out-dir /path/to/campaigns/steel-module-convergence-pilot \
  --stage convergence-pilot \
  --events 250 \
  --blocks 4 \
  --configurations-tsv hpc/osc/configurations/steel-module-convergence-pilot-v1.tsv \
  --campaign-seed 20260715 \
  --environment-mode osc-production \
  --geant4-version 11.4.2 \
  --image /path/to/geant4-11.4.2.sif \
  --g4-data-manifest /path/to/g4-data-manifest.json \
  --build-artifact /path/to/frozen/OpNovice2

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-convergence-pilot \
  --check-only
```

After the check-only report shows exactly 120 tasks, submit through the wrapper:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-convergence-pilot \
  --account PAS2524 \
  --g4-data-root /path/to/geant4-data/11.4.2 \
  --frozen-root /path/to/frozen-campaign-sources
```

When every task has one valid result, finalize and run the steel-module-specific
event-level analysis:

```bash
python3 hpc/osc/finalize_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-convergence-pilot

python3 hpc/osc/analyze_steel_module_campaign.py \
  --campaign-dir /path/to/campaigns/steel-module-convergence-pilot \
  --production-block-events 250
```

The analyzer writes `configuration_intervals.csv`, `per_sensor_intervals.csv`,
`thickness_ratios.csv`, `layout_ratios.csv`, `absorber_convergence.csv`, and
`production_statistics.json` under `finalized/analysis`, together with pinned
provenance and checksums. Its 5% and 10% relative 95% CI half-width projections
use a fixed `1.25` safety factor and round upward to complete 250-event blocks.
They are review aids only: no precision target, production `N`, or absorber
size is automatically accepted.

The accepted 120-task pilot subsequently completed and passed finalization,
the integrated event audit, and finalized/analysis checksum verification. Its
historical v1 interpretation and the required analysis-v2 gate are recorded in
`docs/decisions/steel-module-convergence-pilot-review-v1.md`. The completed v2
review and current production boundary are recorded in
`docs/decisions/steel-module-analysis-v2-review-v1.md`.

The independent analysis-v2 implementation reuses the sealed ROOT events and
does not run Geant4. Run it only from a clean analysis checkout with NumPy and
uproot available:

```bash
python3 hpc/osc/check_steel_module_campaign_infrastructure.py

python3 hpc/osc/analyze_steel_module_campaign_v2.py \
  --campaign-dir /path/to/campaigns/steel-module-convergence-pilot \
  --production-block-events 250
```

The analyzer refuses to overwrite `finalized/analysis-v2`, verifies the
campaign/finalized/event-audit/ROOT identity chain, reconciles the v1 point
estimates, and writes immutable configuration, distribution, response-pathway,
seed-block, pooled-production, standardized-response, primary/secondary
contrast, absorber-review, sizing, provenance, summary, and checksum outputs.
Only four primary contrasts control the eight 5%/10% sizing projections:
pooled scintillation production `24/4` and observed net `24/4` for each of the
three layouts. The projections and 5%/10% absorber bands are review aids; they
never authorize production automatically.

Plotting is optional and separately checksum-bound. It requires matplotlib but
does not change the core evidence:

```bash
python3 hpc/osc/plot_steel_module_analysis_v2.py \
  --analysis-dir /path/to/campaigns/steel-module-convergence-pilot/finalized/analysis-v2
```

This writes the non-overwriting sibling directory
`finalized/analysis-v2-figures` with five figures in both PNG and PDF formats,
plot provenance, and an independent `SHA256SUMS`. Do not create a production
campaign until the v2 summary, tail/block diagnostics, absorber statuses, and
contrast-specific sizing have been reviewed explicitly.

The accepted v2 policy fixes `500 x 500 x 40 mm` as the v1 production model
reference without claiming transverse convergence or equivalence to the full
official ePIC absorber geometry. The `200/300 mm` configurations remain
pilot-only diagnostics. No further absorber-convergence run is required for
v1, but every interpretation must be scoped to the fixed `500 mm` slab proxy.

The accepted production precision target is a 10% event-bootstrap 95%
relative half-width for four and only four `24/4` primary contrasts. The
six-thickness allocation is `4,000` events for the four intermediate
`back-center` configurations, up to `40,250` cumulative events for each
`4/24 mm back-center` endpoint, `10,000` for each `edge-center`
configuration, and `12,000` for each `back-four` configuration. The maximum
new sample is
`4 x 4,000 + 2 x 40,250 + 6 x 10,000 + 6 x 12,000 = 228,500` events, or
`914` complete 250-event blocks. The 30,000-event pilot is not counted.

The `back-center` endpoints must be reviewed cumulatively at
`4,000/10,000/20,000/40,250` events per configuration. Each stage requires
fresh seeds, an immutable manifest, finalization/audit, cumulative analysis,
and an explicit continue decision; `40,250` is a hard ceiling. The complete
sizing formula, rounding arithmetic, stage totals, and stopping rules are in
`docs/decisions/steel-module-analysis-v2-review-v1.md` and SMS-016 of
`docs/decisions/steel-module-scan-v1.md`.

### Current direct BC-S1 production runbook

The first production increment now uses the same ordinary campaign and Slurm
array path that completed the 120-task convergence pilot. It deliberately has
no held preflight, execution successor, readiness lock, intent journal, or
separate control-plane authority. The only admitted production shape is exact:
`4/24 mm back-center`, 16 independent 250-event blocks per endpoint, 32 tasks,
8,000 events, and the 64 seeds already frozen in the accepted BC-S1 child.

From a clean OSC checkout with the usual environment activated:

```bash
MANAGED="$WORK/campaigns/steel-module-production-bc-s1"
DIRECT="$WORK/campaigns/steel-module-production-bc-s1-direct"
FROZEN="$WORK/frozen-campaign-sources"

python3 hpc/osc/generate_steel_module_direct_bc_s1_campaign.py \
  --managed-child-dir "$MANAGED" \
  --out-dir "$DIRECT"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

The generator validates the historical BC-S1 binding and runtime artifacts,
copies its exact tasks and seeds into a standard `campaign.json`, and refuses
to overwrite an existing target. Check-only must report exactly `32 total, 0
submitted, 0 complete`. It does not contact Slurm.

After that output is reviewed, submit the array directly through the familiar
wrapper (there is no separate preflight command):

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --project-root "$REPO" \
  --account PAS2524 \
  --g4-data-root "$DATA_ROOT" \
  --frozen-root "$FROZEN"
```

The wrapper still creates the standard immutable attempt record and frozen Git
source used by the successful pilot. Production shapes other than the exact
reviewed direct children remain fail-closed. Once all 32 tasks finish, use the
ordinary steel-module finalizer:

```bash
python3 hpc/osc/finalize_steel_module_campaign.py \
  --campaign-dir "$DIRECT"
```

Run the generic campaign analysis for the production/collection/net
decomposition, then the thin direct adapter for the predeclared 32-block LOO,
heavy-tail, and pilot-to-production precision review:

```bash
python3 hpc/osc/analyze_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --production-block-events 250

python3 hpc/osc/analyze_steel_module_direct_bc_s1.py \
  --campaign-dir "$DIRECT" \
  --pilot-campaign-dir "$CAMPAIGN"
```

The second command writes `finalized/direct-analysis` atomically and refuses
overwrite. It performs no scheduler call and creates no Geant4 events,
checkpoint, intent, readiness object, or automatic progression decision. The
complete execution and interpretation record is
`docs/decisions/steel-module-direct-bc-s1-execution-v1.md`.

The completed BC-S1 review found `no-material-worsening` and selected
`continue`: observed net `24/4 = 1.64567 [1.27719, 2.12547]`, relative
half-width `25.77%`, maximum LOO shift `5.03%`, and narrowing versus the sealed
pilot. This explicitly makes the separate direct BC-S2 increment eligible; it
does not submit it and does not authorize `FIXED`, `BC-S3`, or `BC-S4`.

### Direct BC-S2 campaign generation

BC-S2 uses the already frozen production-program blocks `16-39`: 24 new
250-event blocks at each of 4 and 24 mm, or 48 tasks and 12,000 events. From a
clean OSC checkout after pulling the implementation commit:

```bash
PROGRAM="$WORK/campaigns/steel-module-production-program-cbc03814"
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"

python3 hpc/osc/generate_steel_module_direct_bc_s2_campaign.py \
  --program-dir "$PROGRAM" \
  --bc-s1-campaign-dir "$BCS1" \
  --out-dir "$BCS2"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$BCS2" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

Generation verifies the frozen production program, accepted BC-S1
finalization and direct-analysis checksums, numeric `continue` eligibility,
the recorded `no-material-worsening` human decision, runtime identity, exact
block range, and all 96 new seeds. It contacts no scheduler and refuses an
existing output directory. Stop unless check-only reports exactly `48 total,
0 submitted, 0 complete` and 12,000 events. The later ordinary submission is a
separate explicit action; there is no preflight.

After the ordinary BC-S2 array and finalizer complete, analyze BC-S1 and BC-S2
together with the cumulative direct adapter:

```bash
python3 hpc/osc/analyze_steel_module_direct_bc_s2.py \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2"
```

The adapter writes `BCS2/finalized/direct-cumulative-analysis` atomically. It
requires exactly blocks `0-39`, 40 independent 250-event blocks and 10,000
events at each endpoint, 80 tasks and 160 unique production seeds across the
two campaigns. It reports cumulative generated/scintillation production,
collection, observed net response, a 10,000-resample event-bootstrap interval,
80 leave-one-block-out evaluations, and tail diagnostics for BC-S1, BC-S2 and
their cumulative sample. The independent BC-S1-versus-BC-S2 comparison is a
diagnostic, not an equivalence test. No scheduler action, Geant4 event,
progression decision, or later child submission is created.

The completed cumulative review recorded `no-material-worsening` and selected
`continue`: observed net `24/4 = 1.47459 [1.24635, 1.74560]`, relative
half-width `16.93%`, maximum LOO shift `3.94%`, and continued narrowing. This
makes only the separate direct BC-S3 increment eligible.

### Direct BC-S3 campaign generation

BC-S3 uses frozen production-program blocks `40-79`: 40 new 250-event blocks
at each endpoint, or 80 tasks and 20,000 events. From a clean OSC checkout:

```bash
PROGRAM="$WORK/campaigns/steel-module-production-program-cbc03814"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"

python3 hpc/osc/generate_steel_module_direct_bc_s3_campaign.py \
  --program-dir "$PROGRAM" \
  --bc-s2-campaign-dir "$BCS2" \
  --out-dir "$BCS3"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$BCS3" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

Generation verifies the frozen BC-S3 task/seed allocation, accepted BC-S2
finalization and cumulative-analysis checksums, the exact reviewed numeric
result, the `no-material-worsening / continue` decision, and unchanged runtime
identity. It performs no scheduler action and refuses an existing target. Stop
unless check-only reports exactly `80 total, 0 submitted, 0 complete` and
20,000 events. BC-S4 remains unauthorized.

The accepted BC-S3 campaign ran as ordinary Slurm array `50618229` and was
finalized and event-audited as 80 tasks with no invalid attempt results. Its
finalized checksum verification returned zero. Analyze the exact contiguous
BC-S1 through BC-S3 production prefix with:

```bash
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"

python3 hpc/osc/analyze_steel_module_direct_bc_s3.py \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2" \
  --bc-s3-campaign-dir "$BCS3"
```

The adapter writes `BCS3/finalized/direct-cumulative-analysis` atomically and
refuses overwrite. It requires blocks `0-79`, 80 independent 250-event blocks
and 20,000 events at each endpoint, 160 tasks, and 320 unique production seeds.
It reports the cumulative production/collection/net decomposition, a pinned
10,000-resample event-bootstrap interval, 160 leave-one-block-out evaluations,
tail diagnostics for each increment and the cumulative sample, and the three
pairwise independent-increment comparisons. Those comparisons are diagnostic,
not equivalence tests. The adapter neither contacts Slurm nor runs Geant4, and
it does not authorize BC-S4; review its summary and tails first.

The completed human review recorded `no-material-worsening` and selected
`continue`: observed net `24/4 = 1.44020 [1.29124, 1.60717]`, relative
half-width `10.97%`, maximum LOO shift `1.90%`, and continued narrowing. This
makes only the separate direct BC-S4 increment eligible.

### Direct BC-S4 campaign generation

BC-S4 is the final frozen back-center increment. It uses blocks `80-160`: 81
new 250-event blocks at each endpoint, or 162 tasks and 40,500 events. After
completion, each endpoint will contain 161 production blocks and 40,250 events.
From a clean OSC checkout:

```bash
PROGRAM="$WORK/campaigns/steel-module-production-program-cbc03814"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"
BCS4="$WORK/campaigns/steel-module-production-bc-s4-direct"

python3 hpc/osc/generate_steel_module_direct_bc_s4_campaign.py \
  --program-dir "$PROGRAM" \
  --bc-s3-campaign-dir "$BCS3" \
  --out-dir "$BCS4"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$BCS4" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

Generation binds the checksum-valid BC-S3 cumulative analysis, its exact
unrounded primary values, the accepted `no-material-worsening / continue`
review, the frozen BC-S4 task/seed allocation, and unchanged runtime identity.
It performs no scheduler action and refuses an existing target. Stop unless
check-only reports exactly `162 total, 0 submitted, 0 complete` and 40,500
events. Formal submission remains a separate ordinary command with no
preflight. BC-S4 is the ceiling: after it is finalized, the last cumulative
review cannot select another `continue` stage.

The accepted BC-S4 campaign ran as ordinary Slurm array `50618568`. All 162
tasks completed with exit code `0:0`; the ordinary finalizer selected and
event-audited all 162 tasks, ignored zero invalid attempt results, and its
`finalized/SHA256SUMS` verification returned zero. Analyze the exact final
BC-S1 through BC-S4 prefix with:

```bash
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"
BCS4="$WORK/campaigns/steel-module-production-bc-s4-direct"

python3 hpc/osc/analyze_steel_module_direct_bc_s4.py \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2" \
  --bc-s3-campaign-dir "$BCS3" \
  --bc-s4-campaign-dir "$BCS4"
```

The adapter writes `BCS4/finalized/direct-cumulative-analysis` atomically and
refuses overwrite. It requires the exact contiguous block range `0-160`, 161
independent 250-event blocks and 40,250 events at each endpoint, 322 tasks,
and 644 unique production seeds. It reports the final
production/collection/net decomposition, pinned 10,000-resample event
bootstrap interval, 322 leave-one-block-out evaluations, per-increment and
cumulative tail diagnostics, and all six pairwise increment comparisons.
Those comparisons remain diagnostic rather than equivalence tests. At the
BC-S4 hard ceiling, `continue` is suppressed: numeric review can expose only
`stop-success / pause-review` when the precision and LOO success rules pass,
or only `pause-review` otherwise.

The accepted final analysis used all 322 tasks and 80,500 events. It found
generated/scintillation production `24/4 = 5.67082`, optical collection
`24/4 = 0.245724`, and observed net SiPM response `24/4 = 1.39346` with a
95% event-bootstrap interval `[1.29104, 1.50771]`. Relative half-width was
`7.77%` and maximum leave-one-block-out shift was `0.89%`, satisfying both
precommitted 10% success rules. The BC-S4 and cumulative tail review recorded
`no-material-worsening`; the final progression decision is `stop-success`,
with no further BC child. This closes only the adaptive back-center endpoint
chain and authorizes, but does not submit, the separately frozen `FIXED`
allocation.

### Direct FIXED campaign generation and submission

The remaining production allocation is non-adaptive and runs as one ordinary
592-task Slurm array. It contains four intermediate back-center thicknesses,
all six edge-center thicknesses, and all six back-four thicknesses, for 148,000
new events. The confirmed OSC array concurrency limit is 1,000. From a clean
OSC checkout with the analysis environment active:

```bash
PROGRAM="$WORK/campaigns/steel-module-production-program-cbc03814"
BCS4="$WORK/campaigns/steel-module-production-bc-s4-direct"
FIXED="$WORK/campaigns/steel-module-production-fixed-direct"
FROZEN="$WORK/frozen-campaign-sources"

python3 hpc/osc/generate_steel_module_direct_fixed_campaign.py \
  --program-dir "$PROGRAM" \
  --bc-s4-campaign-dir "$BCS4" \
  --out-dir "$FIXED"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$FIXED" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

Generation requires the checksum-valid final BC-S4 cumulative analysis and
the accepted `no-material-worsening / stop-success` decision, then reproduces
the exact frozen FIXED task and seed mapping. Stop unless check-only reports
exactly `592 total, 0 submitted, 0 complete`. After human review, submit the
single array with the complete required runtime arguments:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$FIXED" \
  --project-root "$REPO" \
  --account PAS2524 \
  --g4-data-root "$DATA_ROOT" \
  --frozen-root "$FROZEN"
```

No preflight or planned subdivision is used. If isolated elements fail, use
the ordinary retry-failed route for those elements rather than regenerating or
splitting the campaign.

The accepted FIXED array subsequently completed as job `50619224`: all 592
array elements reported `COMPLETED / 0:0`, the ordinary finalizer selected and
event-audited all 592 tasks, ignored zero invalid results, and the finalized
checksum check returned zero. Combine it with the four finalized endpoint
campaigns using the read-only final analyzer:

```bash
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"
BCS4="$WORK/campaigns/steel-module-production-bc-s4-direct"

python3 hpc/osc/analyze_steel_module_direct_final.py \
  --fixed-campaign-dir "$FIXED" \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2" \
  --bc-s3-campaign-dir "$BCS3" \
  --bc-s4-campaign-dir "$BCS4"

FINAL_ANALYSIS="$FIXED/finalized/direct-final-analysis"
python3 hpc/osc/plot_steel_module_direct_final.py \
  --analysis-dir "$FINAL_ANALYSIS"
```

The core output is `direct-final-analysis`; the optional Matplotlib output is
the sibling `direct-final-analysis-figures`. The analyzer verifies all five
campaign/finalization/ROOT identities and the exact 914-task, 228,500-event,
18-configuration matrix. In accordance with SMS-016, pooled production gives
each layout stratum weight `1/3`; it never event-weights the unequal endpoint
samples. It reports configuration, pathway, tail, per-sensor, pooled,
standardized, thickness-ratio, layout-ratio, and four-primary-contrast tables.
Neither entry point contacts Slurm or runs Geant4.

### Direct edge-two follow-up campaign

The accepted single-layer follow-up adds two SiPMs on the same `+X` side and
repeats all six tile thicknesses. It uses `40` independent `250`-event blocks
per thickness: `240` tasks and `60,000` events in one ordinary `1-240` array,
with no preflight. Because the original frozen executable predates the
`edge-two` geometry, first build and freeze a new executable from the clean
campaign-tooling checkout:

```bash
HEAD8="$(git rev-parse --short=8 HEAD)"
BUILD_DIR="build-osc-${HEAD8}"
ARTIFACT_DIR="$WORK/builds/candidate-${HEAD8}"
CANDIDATE="$ARTIFACT_DIR/OpNovice2"

test ! -e "$ARTIFACT_DIR"
mkdir "$ARTIFACT_DIR"

G4_APPTAINER_IMAGE="$REPO/geant4.sif" \
G4_DATA_ROOT="$DATA_ROOT" \
G4_BUILD_DIR="$BUILD_DIR" \
G4_BUILD_JOBS=4 \
G4_FORCE_REBUILD=1 \
  hpc/osc/run_scan_apptainer.sh

install -m 0555 \
  "$REPO/test/OpNovice2/$BUILD_DIR/OpNovice2" \
  "$CANDIDATE"
sha256sum "$CANDIDATE"
```

Then construct and validate the direct campaign without contacting Slurm:

```bash
PROGRAM="$WORK/campaigns/steel-module-production-program-cbc03814"
EDGE_TWO="$WORK/campaigns/steel-module-production-edge-two-direct"
DATA_MANIFEST="$WORK/environment/71e5979a/g4-data-manifest.sha256"

python3 hpc/osc/generate_steel_module_direct_edge_two_campaign.py \
  --program-dir "$PROGRAM" \
  --out-dir "$EDGE_TWO" \
  --image "$REPO/geant4.sif" \
  --g4-data-manifest "$DATA_MANIFEST" \
  --build-artifact "$CANDIDATE"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$EDGE_TWO" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

The generator derives `480` new seeds and verifies that they do not overlap
the sealed pilot or any of the original `914` production tasks. Stop unless
check-only reports exactly `240 total, 0 submitted, 0 complete`. A later
explicit submission uses the same ordinary wrapper, `PAS2524`, and the shared
`$WORK/frozen-campaign-sources` directory.

After all 240 array elements complete, finalize normally and build the
read-only four-layout evidence from the five original direct campaigns plus
the new edge-two campaign:

```bash
FIXED="$WORK/campaigns/steel-module-production-fixed-direct"
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"
BCS4="$WORK/campaigns/steel-module-production-bc-s4-direct"
EDGE_TWO="$WORK/campaigns/steel-module-production-edge-two-direct"

python3 hpc/osc/finalize_steel_module_campaign.py \
  --campaign-dir "$EDGE_TWO"

python3 hpc/osc/analyze_steel_module_four_layout.py \
  --fixed-campaign-dir "$FIXED" \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2" \
  --bc-s3-campaign-dir "$BCS3" \
  --bc-s4-campaign-dir "$BCS4" \
  --edge-two-campaign-dir "$EDGE_TWO" \
  --project-root "$REPO"
```

The default core output is
`$EDGE_TWO/finalized/four-layout-analysis`. It reports the four aggregate
curves plus per-sensor and per-active-area diagnostics for both `edge-two`
and `back-four`, as well as individual installed sensor-copy collection
efficiencies. Rendering remains an independent optional step:

```bash
python3 hpc/osc/plot_steel_module_four_layout.py \
  --analysis-dir "$EDGE_TWO/finalized/four-layout-analysis"
```

The sibling `four-layout-analysis-figures` contains seven PNG/PDF figure
pairs, figure provenance, and its own checksum manifest. Neither command
contacts Slurm or runs Geant4.

### Ten-layer stack follow-up

`steel-module-stack-v1` is independent of the frozen single-layer evidence.
It places ten contiguous `[40 mm steel][tile]` modules along `-Z`, with two
edge SiPMs per layer and causal optical-origin bookkeeping.  It intentionally
uses the same ordinary direct Slurm route as the successful single-layer
production campaigns; there is no preflight or managed execution layer.

Run the focused checker and prepare all six local visualization records first:

```bash
python3 hpc/osc/check_steel_module_stack_infrastructure.py

cd test/OpNovice2
python3 visualize_steel_module_stack_geometry.py --prepare-only
# Omit --prepare-only to inspect interactively; review at least 4 and 24 mm.
```

Every formal stage is generated from a clean checkout and the same frozen OSC
artifact triplet.  The geometry smoke is six one-event tasks:

```bash
python3 hpc/osc/generate_steel_module_stack_campaign.py \
  --out-dir "$WORK/campaigns/steel-module-stack-geometry-smoke" \
  --stage geometry-smoke --campaign-seed 20260722 \
  --environment-mode osc-production --geant4-version 11.4.2 \
  --image "$REPO/geant4.sif" --g4-data-manifest "$DATA_MANIFEST" \
  --build-artifact "$STACK_EXECUTABLE"

python3 hpc/osc/submit_steel_module_stack_campaign.py \
  --campaign-dir "$WORK/campaigns/steel-module-stack-geometry-smoke" --check-only
python3 hpc/osc/submit_steel_module_stack_campaign.py \
  --campaign-dir "$WORK/campaigns/steel-module-stack-geometry-smoke" \
  --project-root "$REPO" --account PAS2524 --g4-data-root "$DATA_ROOT" \
  --frozen-root "$WORK/frozen-sources"

python3 hpc/osc/finalize_steel_module_stack_campaign.py \
  --campaign-dir "$WORK/campaigns/steel-module-stack-geometry-smoke"
```

Repeat with `--stage benchmark` for the fixed `4/24 mm x 25 event` benchmark.
After finalization, capture `sacct` and freeze the selected block size and
memory request:

```bash
python3 hpc/osc/summarize_steel_module_stack_benchmark.py \
  --campaign-dir "$WORK/campaigns/steel-module-stack-benchmark"
```

The report selects the largest block in `250,100,50,25,10` satisfying the
predeclared guarded 45-minute rule.  If it selects `B`, generate the fixed
four-block-per-thickness pilot.  Repeat `--excluded-seeds` for every frozen
pilot/FIXED/BC-S1...S4/edge-two registry or task index; this is how the formal
campaign proves stack seeds do not reuse earlier production seeds.

```bash
python3 hpc/osc/generate_steel_module_stack_campaign.py \
  --out-dir "$WORK/campaigns/steel-module-stack-pilot" \
  --stage pilot --campaign-seed 20260723 --events "$B" \
  --benchmark-report "$WORK/campaigns/steel-module-stack-benchmark/finalized/benchmark/benchmark_report.json" \
  --excluded-seeds /path/to/frozen/task_index.tsv \
  --environment-mode osc-production --geant4-version 11.4.2 \
  --image "$REPO/geant4.sif" --g4-data-manifest "$DATA_MANIFEST" \
  --build-artifact "$STACK_EXECUTABLE"
```

Submit and finalize with the same ordinary wrapper, then analyze:

```bash
python3 hpc/osc/analyze_steel_module_stack_campaign.py \
  --pilot-campaign-dir "$WORK/campaigns/steel-module-stack-pilot"
```

Human review of `finalized/stack-analysis/production_sizing.json` is required.
Only when `production_plan_permitted_for_human_review` is true may it generate
the single additional production array:

```bash
python3 hpc/osc/generate_steel_module_stack_campaign.py \
  --out-dir "$WORK/campaigns/steel-module-stack-production" \
  --stage production --campaign-seed 20260724 \
  --sizing-plan "$WORK/campaigns/steel-module-stack-pilot/finalized/stack-analysis/production_sizing.json" \
  --excluded-seeds "$WORK/campaigns/steel-module-stack-pilot/tasks.tsv" \
  --excluded-seeds "$WORK/campaigns/steel-module-stack-pilot/excluded_seed_registry.json" \
  --environment-mode osc-production --geant4-version 11.4.2 \
  --image "$REPO/geant4.sif" --g4-data-manifest "$DATA_MANIFEST" \
  --build-artifact "$STACK_EXECUTABLE"
```

After the one production array is finalized, combine it with the pilot exactly
once and render checksum-bound figures:

```bash
python3 hpc/osc/analyze_steel_module_stack_campaign.py \
  --pilot-campaign-dir "$WORK/campaigns/steel-module-stack-pilot" \
  --production-campaign-dir "$WORK/campaigns/steel-module-stack-production"
python3 hpc/osc/plot_steel_module_stack_analysis.py \
  --analysis-dir "$WORK/campaigns/steel-module-stack-production/finalized/stack-analysis"
```

The final analyzer reports achieved precision only.  It never generates a
second production increment or selects a detector automatically.  The frozen
scientific and statistical contract is in
`docs/decisions/steel-module-stack-v1.md`.

### Historical managed control-plane record

The sections below preserve the earlier managed/preflight implementation for
provenance and incident interpretation. They are not the current BC-S1
submission runbook.

The accepted execution topology is one non-submittable 914-task parent
production program plus five upfront-frozen incremental child campaigns:
`FIXED` (`592` tasks / `148,000` events), `BC-S1` (`32` / `8,000`),
`BC-S2` (`48` / `12,000`), `BC-S3` (`80` / `20,000`), and `BC-S4`
(`162` / `40,500`). The back-center children use configuration-scoped,
stage-continuous block ranges `0-15`, `16-39`, `40-79`, and `80-160` for each
of the `4/24 mm` endpoint configurations. `FIXED + BC-S1` is the first
complete four-contrast checkpoint (`624` tasks / `156,000` events); `BC-S1`
alone is only the 32-task back-center diagnostic.

The parent program allocates and audits all seeds across the maximum plan,
binds all five child hashes and the pilot-exclusion identity, and is never sent
to Slurm. Generic campaign submission must reject managed children. Submit one
child through the production-stage gate and finalize that whole child with the
existing child-local attempt journal. Program-level cumulative evidence may
use a checksum-valid contiguous `BC-S1...BC-Sn` prefix for back-center-only
diagnosis, or that prefix plus `FIXED` for complete four-contrast evidence.

The accepted initial gate submits `BC-S1` alone (`32` tasks / `8,000` events).
`FIXED` and all later BC children remain locked until BC-S1 is finalized,
audited, checksum-verified, analyzed, and explicitly reviewed. `stop-success`
unlocks only `FIXED`; `continue` unlocks `FIXED` and makes `BC-S2` eligible for
a later scheduling decision; `pause-review` unlocks nothing. If `continue` is
recorded, parallel versus sequential execution of `FIXED` and `BC-S2` remains
open. No analysis output authorizes or submits a child automatically.

LOO in this policy means leave-one-250-event-block-out: recompute the endpoint
`24/4` ratio after omitting each contributing block and report the largest
relative shift. It is a block-sensitivity diagnostic, not a confidence
interval or a request to remove data. The cumulative analyzer outputs only a
provisional numeric eligibility subset of `stop-success`, `continue`, and
`pause-review`. Invalid evidence fails closed, a material adverse-tail human
finding forces `pause-review`, and the `BC-S4` hard ceiling always suppresses
`continue`. A separate append-only human decision record binds the
finalized/audit/analysis checksums, numeric eligibility, and tail disposition,
and is validated by the managed submitter. Neither component submits a child
automatically.

Each cumulative checkpoint must also have a non-overwriting, self-contained
offline review report generated from checksum-valid analysis. It includes
`index.html`, a print-equivalent PDF, PNG/PDF plots, machine-readable review
data, provenance, and independent checksums. At minimum it visualizes precision
versus event count and the 10% target, all LOO shifts and the 10%/20% bands,
heavy-tail/zero diagnostics, and the endpoint ratio interval. The report may
provide a copyable decision-recorder command, but cannot write a decision,
unlock work, or invoke Slurm. See SMS-019 in
`docs/decisions/steel-module-scan-v1.md` for the full contract.

Phase 1 materializes only the immutable, non-submittable production program.
It writes `production_program.json`, the canonical 914-task registry, global
seed and sealed-pilot exclusion registries, and five child task-set plans. It
intentionally writes no `campaign.json`; the existing campaign submitter must
therefore reject both the parent and every child directory. Generate the clean
OSC evidence object with:

```bash
cd ~/projects/g4optics
source hpc/osc/activate_steel_module_osc.sh
```

The sourced helper validates the OSC repository, analysis virtual environment,
sealed pilot, finalized checksums, and Geant4 data directory. It exports
`REPO`, `WORK`, `CAMPAIGN`, `DATA_ROOT`, and `G4_DATA_ROOT`, activates
`$WORK/analysis-venv`, and returns to the source repository. Running it as a
normal executable is intentionally rejected because an executed child process
cannot activate the parent shell.

```bash
python3 hpc/osc/generate_steel_module_production_program.py \
  --out-dir /path/to/campaigns/steel-module-production-program \
  --program-seed 20260717 \
  --sealed-pilot-dir /path/to/campaigns/steel-module-convergence-pilot-cfd7d974 \
  --executable-build-source-commit 88f15eace17137475310913d585a96908a493c72 \
  --environment-mode osc-production \
  --image /path/to/geant4.sif \
  --g4-data-manifest /path/to/g4-data-manifest.sha256 \
  --build-artifact /path/to/candidate-88f15eac/OpNovice2 \
  --build-provenance /path/to/build-environment.txt
```

The generator requires the accepted, checksum-valid 120-task pilot and
cross-checks its `tasks.tsv` against finalized `task_index.tsv`. It initializes
the production seed allocator with all 240 pilot seed integers, derives all
1,828 production seed integers in one global registry, and refuses any overlap.
It writes through a sibling temporary tree, validates the complete program,
and performs one atomic rename; an existing target is never overwritten.

Validate an archived program, including the currently recorded runtime files,
with:

```bash
python3 hpc/osc/validate_steel_module_production_program.py \
  --program-dir /path/to/campaigns/steel-module-production-program \
  --verify-runtime-artifacts
```

For a local development dry run, use `--environment-mode local-dev
--allow-dirty` and omit the four runtime artifact paths. Such output is always
marked `accepted_statistical_evidence=false`. Phase 1 is complete only after
`check_steel_module_production_program.py` passes; it still does not authorize
a `BC-S1` Slurm submission. Managed child materialization and submission belong
to Phase 2.

Phase 2A materializes only the exact `BC-S1` child named by the initial
authorization graph from the tracked formal-program lock. The CLI intentionally
has no program, child, seed, or identity override: the execution source remains
the Phase-1 program source while the clean Phase-2A checkout is recorded
separately as the control plane. From a clean OSC checkout, create the one
canonical target fixed beside the formal parent program with:

```bash
python3 hpc/osc/materialize_steel_module_production_child.py
```

The fixed output is
`$WORK/campaigns/steel-module-production-bc-s1`. It is a managed child plan,
not a campaign: it intentionally contains `managed_child.json` and no
`campaign.json`, attempt journal, or Slurm entry point. This also makes every
historical generic submitter fail before submission. The plan contains exactly
32 tasks and 8,000 events: `4/24 mm back-center`, 250 events in each of blocks
`0-15`. It is checksum-bound to the parent program, its frozen
seeds/configuration hashes, the initial authorization graph, the execution
source, and the recorded control-plane source. Its dedicated Phase-2A wrapper
provides read-only validation only:

```bash
python3 hpc/osc/submit_steel_module_production_child.py \
  --managed-child-dir "$WORK/campaigns/steel-module-production-bc-s1" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

The recorded Phase-2A commit/tree and artifact blobs remain verifiable from
Git history after HEAD advances. This immediate check-only wrapper additionally
requires the current clean checkout to equal that recorded commit. A future
actual-attempt implementation must bind its newer submission control plane
separately; it must not rewrite or rematerialize this plan.

Omitting `--check-only` is rejected before any plan mutation. The generic
steel-module submitter rejects every production-stage campaign, including this
managed child. Phase 2A creates no attempt journal, frozen-source archive, or
Slurm command; real submission remains blocked until the downstream closure is
implemented and reviewed. Validate the deterministic no-Slurm contract with
`python3 hpc/osc/check_steel_module_managed_production.py`.

Phase 2B adds a separate execution companion and the complete managed evidence
closure without changing the frozen Phase-2A directory. Its exact contract and
stopping boundary are in
`docs/decisions/steel-module-production-phase2b-v1.md`. At implementation
commit C, first activate the existing OSC analysis environment and run:

```bash
python3 hpc/osc/check_steel_module_campaign_infrastructure.py
python3 hpc/osc/materialize_steel_module_production_execution.py
python3 hpc/osc/manage_steel_module_production_attempt.py status
```

The materializer has no path, program, child, account, or source override. It
creates only the canonical sibling
`$WORK/campaigns/steel-module-production-bc-s1-execution`, two read-only source
archives, the historical inode-bound v1 control lock, and immutable static
identity files. It creates no `campaign.json`, intent, job, ROOT output, or
readiness authority. The real OSC failure of this cross-node inode assumption
and its additive recovery are recorded in
`docs/decisions/steel-module-production-phase2b-recovery-v1.md`.

Still at commit C, collect the no-Slurm OSC readiness evidence into an untracked
file outside the repository:

```bash
python3 hpc/osc/generate_steel_module_production_phase2b_readiness.py \
  --write-proposal "$WORK/evidence/steel-module-phase2b-readiness-proposal.json"
```

This reruns the integrated checker, requires NumPy/uproot/matplotlib, exercises
cross-process locking and atomic publication on the campaign filesystem, checks
that all scheduler commands exist while a sentinel blocks every scheduler
executable during the checker, and requires `intents/`, `attempts/`, and
`finalized/` to be exactly empty. The proposal is deliberately
non-authoritative: it records `managed_submission_ready=false` and cannot write
inside the repository. After human review, a separate readiness-lock commit R
may add only
`hpc/osc/configurations/steel-module-production-phase2b-v1.lock.json`, changing
the reviewed status to `accepted-formal-phase-2b-ready` and readiness to true
while retaining `automatic_submission=false`, zero intents, and zero job IDs.
After pulling R on OSC, validate it with:

```bash
python3 hpc/osc/validate_steel_module_production_phase2b_readiness.py
```

The validator requires the fixed complete critical-artifact set and compares
every digest across the reviewed lock, the clean R checkout, and the read-only
Implementation-C control archive. It also rechecks the exact Phase-2A lock
digest and the empty mutable roots; the readiness lock cannot choose a smaller
protected set.

Readiness also starts an isolated Python process directly from the read-only
control archive (which intentionally has no `.git`) and loads the formal
execution/runtime through the relocated-lock path. This probe runs neither the
worker simulation nor Geant4 and remains inside the scheduler sentinel.

The first real intent is outside the Phase-2B readiness rollout. When that
separate gate is authorized, the two-step interface is:

```bash
ATTEMPT_ID=20260718T120000Z-initial

python3 hpc/osc/manage_steel_module_production_attempt.py prepare-intent \
  --attempt-id "$ATTEMPT_ID" --mode initial --check-only
python3 hpc/osc/manage_steel_module_production_attempt.py prepare-intent \
  --attempt-id "$ATTEMPT_ID" --mode initial --write-intent

# Copy the full printed intent SHA-256 into INTENT_SHA256 and review it first.
python3 hpc/osc/manage_steel_module_production_attempt.py submit-intent \
  --attempt-id "$ATTEMPT_ID" --intent-sha256 "$INTENT_SHA256" --check-only

# This is the only form that may contact Slurm; do not run it during readiness.
python3 hpc/osc/manage_steel_module_production_attempt.py submit-intent \
  --attempt-id "$ATTEMPT_ID" --intent-sha256 "$INTENT_SHA256" --submit
```

Submission uses one held array and verifies its account, immutable wrapper,
working directory, output pattern, and exact array shape before release. Any
uncertain submission or release is quarantined and must be reconciled against
that same intent/job; it never triggers another `sbatch`. Freeze terminal
accounting while OSC still retains it:

```bash
python3 hpc/osc/manage_steel_module_production_attempt.py freeze-accounting \
  --attempt-id "$ATTEMPT_ID" --intent-sha256 "$INTENT_SHA256"
```

The managed freezer derives array indices from logical `sacct JobID` values
and records `JobIDRaw` separately as scheduler provenance. It accepts OSC's
normal terminal shape of one complete array-task set with no separate parent
row; the final task's raw ID may equal the parent number. The active-job check
uses the intent's unique Slurm job name, so a terminal job disappearing from
`squeue -j` is not mistaken for missing accounting.

The historical first attempt `20260718T175107Z-initial`, job `50532143`,
failed before Apptainer or Geant4 because the login-node inode identity was not
portable to compute nodes. Do not create another intent under that execution.
OSC later demonstrated that the same numeric inode identity is not even stable
across login nodes. The historical status/sealing path therefore relaxes only
that cross-node number for this exact closed execution; normal workers and all
submission mutations remain strict and closed.
After pulling the recovery implementation, preview and explicitly seal only
that incident with:

```bash
python3 hpc/osc/seal_steel_module_production_phase2b_incident.py \
  --check-only --actor "$USER"
python3 hpc/osc/seal_steel_module_production_phase2b_incident.py \
  --seal --actor "$USER"
```

The preview performs scheduler reads but no writes. The seal freezes the exact
32 `FAILED/1:0` rows, appends the terminal event, and publishes a
content-addressed incident bundle. Neither form can invoke `sbatch` or
`scontrol`. Before the two allowlisted read-only scheduler calls, the tool
validates the readiness-bound companion bytes, unique historical lineage, all
32 failure logs, and the absence of simulation output. Successor execution
materialization and retry remain separate, later gates.

Execution-v2 was subsequently materialized, but its first R3 job `50544247`
failed before Apptainer because Slurm copied the directly submitted Python
file into its spool and the sibling import was no longer resolvable. Preserve
the four read-only inputs under `$R3ROOT` and seal this distinct failed
preflight before creating the next execution generation:

```bash
FAILED_EXECUTION="$WORK/campaigns/steel-module-production-bc-s1-execution-v2"
R3ROOT="$WORK/evidence/steel-module-production-r3"

python3 hpc/osc/seal_steel_module_production_r3_failure.py \
  --execution-dir "$FAILED_EXECUTION" \
  --held-scontrol-input "$R3ROOT/held-scontrol-50544247.txt" \
  --sacct-input "$R3ROOT/sacct-50544247.psv" \
  --squeue-input "$R3ROOT/squeue-50544247.psv" \
  --slurm-output-input "$R3ROOT/slurm-50544247.out" \
  --check-only

python3 hpc/osc/seal_steel_module_production_r3_failure.py \
  --execution-dir "$FAILED_EXECUTION" \
  --held-scontrol-input "$R3ROOT/held-scontrol-50544247.txt" \
  --sacct-input "$R3ROOT/sacct-50544247.psv" \
  --squeue-input "$R3ROOT/squeue-50544247.psv" \
  --slurm-output-input "$R3ROOT/slurm-50544247.out" \
  --seal
```

The formal sealer re-requires the four fixed OSC input SHA-256 values, the
complete frozen v2 execution, exact traceback/accounting, absent v2 raw
workspace, and zero event/seed consumption. It performs no scheduler call.
Execution-v2 is permanently closed regardless of whether the bundle is being
previewed or has been sealed.

After the failed-preflight bundle exists, preview and materialize v3 without
scheduler contact:

```bash
python3 hpc/osc/materialize_steel_module_production_successor_v3_execution.py \
  --check-only
python3 hpc/osc/materialize_steel_module_production_successor_v3_execution.py \
  --materialize
python3 hpc/osc/validate_steel_module_production_successor_v3_execution.py \
  --check-only
```

The successor is the fixed sibling
`$WORK/campaigns/steel-module-production-bc-s1-execution-v3`. It binds the
original production incident, complete closed-v2 authority, and exact failed
R3 bundle. Its 32 task rows, scan arguments, 64 seeds, simulation source,
executable, image, and Geant4 data identity remain byte-for-byte equivalent to
Phase-2A/v2. V3 leaves `intents/`, `attempts/`, and `finalized/` empty and
reports `submission_ready=false`; neither earlier readiness record can
authorize it.

The first correctly admitted execution-v3 compute probe, Slurm job `50548308`
(`g4sm-r3-30d01a2a03d9`), did reach Apptainer but rejected its own successful
read-only result because of a writer-probe instrumentation bug. It did not
invoke Geant4 or consume an event or production seed. Execution-v3 and that
job are immutable rejected-preflight history. Preview and then seal their
exact raw output and externally frozen scheduler evidence before creating the
next execution generation:

```bash
V3="$WORK/campaigns/steel-module-production-bc-s1-execution-v3"
R3ROOT="$WORK/evidence/steel-module-production-r3"
REJECT_JOB=50548308
REJECT_NAME=g4sm-r3-30d01a2a03d9
RAW_REJECT="$R3ROOT/raw/$REJECT_NAME"

python3 hpc/osc/seal_steel_module_production_r3_probe_rejection.py \
  --execution-dir "$V3" \
  --raw-workspace "$RAW_REJECT" \
  --held-scontrol-input "$R3ROOT/held-scontrol-$REJECT_JOB.txt" \
  --sacct-input "$R3ROOT/sacct-$REJECT_JOB.psv" \
  --squeue-input "$R3ROOT/squeue-$REJECT_JOB.psv" \
  --slurm-output-input "$R3ROOT/slurm-$REJECT_JOB.out" \
  --check-only

python3 hpc/osc/seal_steel_module_production_r3_probe_rejection.py \
  --execution-dir "$V3" \
  --raw-workspace "$RAW_REJECT" \
  --held-scontrol-input "$R3ROOT/held-scontrol-$REJECT_JOB.txt" \
  --sacct-input "$R3ROOT/sacct-$REJECT_JOB.psv" \
  --squeue-input "$R3ROOT/squeue-$REJECT_JOB.psv" \
  --slurm-output-input "$R3ROOT/slurm-$REJECT_JOB.out" \
  --seal
```

Both forms perform no scheduler call. The sealer fixes the execution-v3
identity, job ID/name, all externally recorded SHA-256 values, the exact two
false writer-report keys, unchanged execution snapshot, and zero event/seed
consumption. The sealed bundle remains rejected evidence and can never satisfy
the accepted-preflight gate.

After that bundle exists, preview, materialize, and validate the canonical
execution-v4 without scheduler contact:

```bash
python3 hpc/osc/materialize_steel_module_production_successor_v4_execution.py \
  --check-only
python3 hpc/osc/materialize_steel_module_production_successor_v4_execution.py \
  --materialize
python3 hpc/osc/validate_steel_module_production_successor_v4_execution.py \
  --check-only
```

Execution-v4 is the fixed sibling
`$WORK/campaigns/steel-module-production-bc-s1-execution-v4`. It binds all
preceding history, including the sealed `50548308` rejection, while preserving
the 32 tasks, 8,000 events, 64 production seeds, simulation source, runtime,
and physics inputs. It starts with empty `intents/`, `attempts/`, and
`finalized/`, and still reports `submission_ready=false`. Job `50544247` and
job `50548308` are failed compute-preflight history; job `50547698` remains a
separate administrative launcher rejection and must not be counted as a
compute preflight.

Execution-v4's released R3 job `50558158` failed before creating its raw
workspace or entering the protected probe body. OSC GPFS reproduced the exact
root cause: an exclusive `flock` on an `O_RDONLY` descriptor returns `EBADF`,
while `O_RDWR + LOCK_EX` succeeds. Execution-v4 had frozen its portable lock
at mode `0400`, so it cannot be repaired or retried in place. Freeze the exact
three scheduler rows and empty terminal queue externally, then preview and seal
the content-addressed pre-workspace failure:

```bash
V4="$WORK/campaigns/steel-module-production-bc-s1-execution-v4"
R3ROOT="$WORK/evidence/steel-module-production-r3"
FAILED_JOB=50558158

python3 hpc/osc/seal_steel_module_production_r3_preworkspace_failure.py \
  --execution-dir "$V4" \
  --held-scontrol-input "$R3ROOT/held-scontrol-$FAILED_JOB.txt" \
  --sacct-input "$R3ROOT/sacct-$FAILED_JOB.psv" \
  --squeue-input "$R3ROOT/squeue-$FAILED_JOB.psv" \
  --slurm-output-input "$R3ROOT/slurm-$FAILED_JOB.out" \
  --check-only

python3 hpc/osc/seal_steel_module_production_r3_preworkspace_failure.py \
  --execution-dir "$V4" \
  --held-scontrol-input "$R3ROOT/held-scontrol-$FAILED_JOB.txt" \
  --sacct-input "$R3ROOT/sacct-$FAILED_JOB.psv" \
  --squeue-input "$R3ROOT/squeue-$FAILED_JOB.psv" \
  --slurm-output-input "$R3ROOT/slurm-$FAILED_JOB.out" \
  --seal
```

The formal sealer fixes the full execution-v4 identity, job ID/name, exact
input paths and SHA-256 values, `FAILED/1:0` parent and batch plus
`COMPLETED/0:0` extern, exact one-line `EBADF` log, absent raw workspace,
empty `intents/attempts/finalized`, and zero Apptainer/Geant4/event/seed use.
It performs no scheduler call. Execution-v4 is permanently closed after the
released job even though its mutable roots stayed empty.

After that bundle exists, preview, materialize, and validate the clean
execution-v5 without scheduler contact:

```bash
python3 hpc/osc/materialize_steel_module_production_successor_v5_execution.py \
  --check-only
python3 hpc/osc/materialize_steel_module_production_successor_v5_execution.py \
  --materialize
python3 hpc/osc/validate_steel_module_production_successor_v5_execution.py \
  --check-only
```

Execution-v5 binds the immutable v4 failure while preserving all 32 tasks,
8,000 events, 64 production seeds, simulation/runtime/physics identities, and
empty mutable roots. Its portable lock and manifest are both mode `0600`.
Shared validation continues to open the lock read-only; an exclusive probe
lease uses `O_RDWR`. Historical `0400` locks fail closed for exclusive use and
are never chmodded.

The replacement R3 is one non-array compute job. It invokes the pinned
Apptainer image to test the exact production mount boundary but never invokes
Geant4. The frozen v5 control archive and v5-specific copied-safe launcher must
be used. `sbatch` and `scontrol release` remain separate explicit actions and
are never called by an R3 Python tool:

```bash
EXECUTION="$WORK/campaigns/steel-module-production-bc-s1-execution-v5"
CONTROL="$EXECUTION/sources/control"
R3ROOT="$WORK/evidence/steel-module-production-r3"
mkdir -p "$R3ROOT/raw" "$R3ROOT/accounting" "$R3ROOT/evidence"

EXECUTION_HASH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["execution_hash"])' \
  "$EXECUTION/managed_execution.json")
JOB_NAME="g4sm-r3-${EXECUTION_HASH:0:12}"
RAW="$R3ROOT/raw/$JOB_NAME"

# Review this exact command before running it. It is the only R3 submission.
SUBMISSION=$(sbatch --hold --parsable --no-requeue --export=NONE \
  --time=00:10:00 --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=1G \
  --account PAS2524 --job-name "$JOB_NAME" --chdir "$EXECUTION" \
  --output "$R3ROOT/slurm-%j.out" \
  "$CONTROL/hpc/osc/run_steel_module_production_r3_probe_v5.sbatch")
JOB_ID=${SUBMISSION%%;*}
[[ "$JOB_ID" =~ ^[1-9][0-9]*$ ]] || { echo "invalid R3 job id" >&2; exit 1; }

# Capture into a private temporary file. A failed/truncated query cannot reach
# release, and hard-link publication refuses to replace an existing snapshot.
HELD="$R3ROOT/held-scontrol-$JOB_ID.txt"
HELD_TMP=$(mktemp "$R3ROOT/.held-scontrol-$JOB_ID.XXXXXX") || exit 1
if ! scontrol show job -o "$JOB_ID" > "$HELD_TMP"; then
  rm -f "$HELD_TMP"
  exit 1
fi
if ! python3 hpc/osc/validate_steel_module_production_r3_held_job.py \
  --job-id "$JOB_ID" --job-name "$JOB_NAME" \
  --execution-dir "$EXECUTION" \
  --held-scontrol-input "$HELD_TMP" --check-only; then
  rm -f "$HELD_TMP"
  exit 1
fi
ln "$HELD_TMP" "$HELD" || { rm -f "$HELD_TMP"; exit 1; }
rm -f "$HELD_TMP"
python3 hpc/osc/validate_steel_module_production_r3_held_job.py \
  --job-id "$JOB_ID" --job-name "$JOB_NAME" \
  --execution-dir "$EXECUTION" \
  --held-scontrol-input "$HELD" --check-only
```

Stop here. The validator must print `PASS`, and a human must independently
review the captured row for the exact job name, case-insensitive `PAS2524`
account, `PENDING / JobHeldUser`, `Requeue=0`, non-array shape, execution-v5
`WorkDir`, frozen launcher `Command`, and requested `StdOut`. Also confirm the
ten-minute, one-node, one-CPU, 1-GiB resource shape. If any field
differs, preserve the held job and stop for review.

Only after that distinct review may the one release write be issued as a
separate command:

```bash
scontrol release "$JOB_ID"
```

After release, require exactly one parent, one `.batch`, and one `.extern` row;
all three must be `COMPLETED / 0:0` under account `PAS2524`
case-insensitively, and terminal `squeue` must be empty. This exact-three-row
review is an explicit human gate in addition to the freezer. Then freeze the
externally collected read-only accounting and seal the successful bundle:

```bash
sacct -n -P -j "$JOB_ID" \
  --format=JobID,JobName,Account,State,ExitCode,ElapsedRaw \
  > "$R3ROOT/sacct-$JOB_ID.psv"
squeue -h --name "$JOB_NAME" -o '%A|%j|%T' \
  > "$R3ROOT/squeue-$JOB_ID.psv"

python3 hpc/osc/freeze_steel_module_production_r3_probe_accounting.py \
  --job-id "$JOB_ID" --job-name "$JOB_NAME" \
  --execution-dir "$EXECUTION" \
  --sacct-input "$R3ROOT/sacct-$JOB_ID.psv" \
  --squeue-input "$R3ROOT/squeue-$JOB_ID.psv" \
  --held-scontrol-input "$R3ROOT/held-scontrol-$JOB_ID.txt" \
  --output-dir "$R3ROOT/accounting/$JOB_ID"

python3 hpc/osc/seal_steel_module_production_r3_probe_evidence.py \
  --raw-workspace "$RAW" \
  --terminal-accounting-dir "$R3ROOT/accounting/$JOB_ID" \
  --output-root "$R3ROOT/evidence" \
  --execution-dir "$EXECUTION"
```

Successful execution-v5 probe evidence is the end of this recovery phase.
The exact leased probe mountpoint remains under `attempts/` with a read-only,
content-bound marker; it is neither deleted nor renamed. The raw workspace
contains a byte-identical marker copy, and the accepted bundle records
`execution_closed=true` and `future_successor_required=true`.

Therefore do not generate readiness or create an intent under execution-v4 or
execution-v5, and do not use either for production, finalization, or checkpoint
construction. Any later BC-S1 production requires a separately reviewed and
authorized clean successor (expected execution-v6) that binds the accepted v5
evidence while starting with new, empty mutable roots. That future step is
outside this phase and does not authorize a scheduler submission.

#### Phase 2C: sacrificial preflight twin and clean execution-v6

Phase 2C implements that clean successor without running the production scan.
Its complete decision contract is in
`docs/decisions/steel-module-production-phase2c-v1.md`. The implementation
commit C6 must not contain
`hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json`. C6 also
must not create a production intent, submit the 32-task BC-S1 array, or invoke
Geant4. Each sacrificial twin may submit at most one separately authorized,
held, non-array, no-Geant4 preflight. A checksum-valid failed twin may lead to
a new content-addressed retry twin and a new authorization, but the same twin
must never call `sbatch` twice.

On OSC, begin from the canonical source and work roots. The activation helper
checks and exports the same values, activates the analysis environment, and
returns to the source repository:

```bash
REPO=/users/PAS2524/anolddriver66/projects/g4optics
WORK=/users/PAS2524/anolddriver66/g4optics-rn

cd "$REPO"
source hpc/osc/activate_steel_module_osc.sh
test "$REPO" = /users/PAS2524/anolddriver66/projects/g4optics
test "$WORK" = /users/PAS2524/anolddriver66/g4optics-rn
test ! -e hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json
git status --short
```

At clean C6, record `git rev-parse HEAD`, require the complete checker and the
focused Phase-2C checker to pass, and only then preview and materialize the
canonical sacrificial twin. None of these commands contacts Slurm:

```bash
C6_COMMIT=$(git rev-parse HEAD)
python3 hpc/osc/check_steel_module_campaign_infrastructure.py
python3 hpc/osc/check_steel_module_production_phase2c.py

python3 hpc/osc/materialize_steel_module_production_preflight_v6.py \
  --check-only
python3 hpc/osc/materialize_steel_module_production_preflight_v6.py \
  --materialize
python3 hpc/osc/validate_steel_module_production_preflight_v6.py \
  --check-only
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py status
```

The initial fixed twin is
`$WORK/campaigns/steel-module-production-bc-s1-preflight-v6`. It has no
production authority. Its static production inputs and the later execution-v6
share one `production_equivalence_hash`; its preflight journal instead lives
under `$WORK/evidence/steel-module-production-phase2c/preflight-control`.
If reviewed failure evidence later closes it, the retry materializer publishes
a sibling named
`steel-module-production-bc-s1-preflight-v6-retry-<NN>-<hash12>` from the
checksum-valid failed twin. The manager always selects the latest validated
twin; every retry requires a new authorization.

Creating the preflight authorization is a separate two-step operation. First
preview it, then explicitly write it. The check-only payload has no authority
even though its stable semantic hash is designed to match the subsequently
written payload. Copy the complete `authorization_sha256` printed by the
`--write-authorization` command into `AUTH_SHA256`.

```bash
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  prepare --check-only --actor "$USER"

# Separate human authorization is required before this write.
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  prepare --write-authorization --actor "$USER"

AUTH_SHA256=<full-written-authorization-sha256>
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py status
```

Next review the exact held-submission command without contacting Slurm. Stop
again for explicit human authorization before using `--submit`. This is the
only Phase-2C command that invokes `sbatch`; it submits exactly one non-array
job with `--hold`, `--no-requeue`, `--export=NONE`, account `PAS2524`, one CPU,
1 GiB, and a ten-minute limit. A successful return leaves the job held.

```bash
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  submit --authorization-sha256 "$AUTH_SHA256" --check-only

# STOP: run only after explicit authorization of the printed command and hash.
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  submit --authorization-sha256 "$AUTH_SHA256" --submit --actor "$USER"

python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  verify-held --authorization-sha256 "$AUTH_SHA256" --actor "$USER"
```

`verify-held` performs scheduler reads and appends one immutable identity event
to the external preflight journal. It must report the exact job name,
case-insensitive `PAS2524/pas2524` account, non-array shape, frozen launcher and
work directory, and `PENDING / JobHeldUser`. Preview release separately. Stop
for a second explicit human authorization before `--release`; release mutates
only that already verified job and never calls `sbatch`.

If `verify-held` rejects the scheduler identity while the exact recorded job
remains held, do not release, adopt, or resubmit it. The manager intentionally
does not auto-cancel this exceptional case. Obtain separate authorization for
`scancel <exact-job-id-from-status-and-journal>`, wait until `squeue` is empty
and `sacct` is terminal, then freeze accounting and include that intervention
in the reviewed failure rationale before sealing the twin.

```bash
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  release --authorization-sha256 "$AUTH_SHA256" --check-only

# STOP: run only after separate authorization to release this exact held job.
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  release --authorization-sha256 "$AUTH_SHA256" --release --actor "$USER"
```

If submission or release is ambiguous, do not submit again. Preserve the
existing job and use only the reconciliation route for the same authorization:

```bash
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  reconcile --authorization-sha256 "$AUTH_SHA256" --actor "$USER"
```

For an ambiguous zero-match submission, two `reconcile` observations using
both `squeue` and `sacct`, separated by at least ten minutes, are required
before reviewed failure sealing can declare it retry-eligible. Multiple
matching jobs enter permanent quarantine; never adopt one arbitrarily and
never submit another job for that twin.

After a submitted preflight becomes terminal, freeze accounting before Slurm
retention expires. If the job failed, do not run the success-evidence sealer.
Preview and then seal reviewed failure evidence. The preview prints
`retry_eligible`; only `true` permits creation of a new twin. Failure sealing
performs no scheduler command and permanently closes the failed twin:

```bash
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  freeze-accounting --authorization-sha256 "$AUTH_SHA256" --actor "$USER"

FAILURE_RATIONALE='concise reviewed reason for this preflight failure'
python3 hpc/osc/seal_steel_module_production_phase2c_preflight_failure.py \
  --authorization-sha256 "$AUTH_SHA256" \
  --reviewer "$USER" --rationale "$FAILURE_RATIONALE" --check-only
python3 hpc/osc/seal_steel_module_production_phase2c_preflight_failure.py \
  --authorization-sha256 "$AUTH_SHA256" \
  --reviewer "$USER" --rationale "$FAILURE_RATIONALE" --seal
python3 hpc/osc/validate_steel_module_production_preflight_v6.py \
  --check-only
```

For a safely reviewed zero-match ambiguity there is no terminal job accounting;
run the same failure-sealing commands only after the required two observations.
For `retry_eligible: true`, preview and publish the next content-addressed twin.
The materializer detects the sealed predecessor and switches to retry mode:

```bash
python3 hpc/osc/materialize_steel_module_production_preflight_v6.py \
  --check-only
python3 hpc/osc/materialize_steel_module_production_preflight_v6.py \
  --materialize
python3 hpc/osc/validate_steel_module_production_preflight_v6.py \
  --check-only

unset AUTH_SHA256
# Return to prepare --check-only and obtain a new explicit authorization.
```

If failure sealing reports `retry_eligible: false`, stop: execution-v6 cannot
be materialized from that twin and this recovery path has no automatic retry.

For a successful terminal preflight, freeze accounting, preview the success
evidence seal, and then publish it. Copy the printed content-addressed directory
basename into `EVIDENCE_ID` and validate it. Accounting freeze performs
scheduler reads; success sealing and evidence validation perform no scheduler
command.

```bash
python3 hpc/osc/manage_steel_module_production_phase2c_preflight.py \
  freeze-accounting --authorization-sha256 "$AUTH_SHA256" --actor "$USER"

python3 hpc/osc/seal_steel_module_production_phase2c_preflight_evidence.py \
  --authorization-sha256 "$AUTH_SHA256" --check-only
python3 hpc/osc/seal_steel_module_production_phase2c_preflight_evidence.py \
  --authorization-sha256 "$AUTH_SHA256" --seal

EVIDENCE_ID=sm-v1-phase2c-preflight-<hash12>
python3 hpc/osc/validate_steel_module_production_phase2c_preflight_evidence.py \
  --evidence-id "$EVIDENCE_ID" --check-only
python3 hpc/osc/validate_steel_module_production_preflight_v6.py \
  --check-only
```

Accepted evidence must prove that Apptainer was invoked, Geant4 was not
invoked, zero events and production seeds were consumed, and the sacrificial
twin is permanently closed. Only then may the clean canonical execution-v6 be
previewed and materialized. These commands do not contact Slurm:

```bash
python3 hpc/osc/materialize_steel_module_production_execution_v6.py \
  --check-only
python3 hpc/osc/materialize_steel_module_production_execution_v6.py \
  --materialize
python3 hpc/osc/validate_steel_module_production_execution_v6.py \
  --check-only
```

The output is
`$WORK/campaigns/steel-module-production-bc-s1-execution-v6`. It is copied
from the checksum-valid twin static tree, has the same
`production_equivalence_hash`, and starts with empty `intents/`, `attempts/`,
and `finalized/`. It remains non-submittable until the separate R6 lock-only
commit is accepted.

Still at clean C6, first freeze the content-addressed C6 OSC acceptance bundle.
This command reruns the top-level and focused checkers, records both complete
outputs and hashes, requires the focused forbidden-scheduler sentinel, and
proves zero real Slurm calls and no Geant4 invocation. It writes no tracked
lock. Copy the printed `output` path into `C6_ACCEPTANCE` and verify its
checksum manifest:

```bash
test "$(git rev-parse HEAD)" = "$C6_COMMIT"
git status --short
python3 hpc/osc/generate_steel_module_production_phase2c_acceptance.py --write

C6_ACCEPTANCE=/absolute/path/printed/by-the-command
(
  cd "$C6_ACCEPTANCE"
  sha256sum --quiet -c SHA256SUMS
)
```

There must be exactly one valid acceptance bundle for this C6. Historical
acceptance bundles may remain, but a second bundle for the same C6, a symlink,
or a current-C6 bundle outside the canonical evidence root is rejected. Now generate
the non-authoritative readiness proposal in the external Phase-2C evidence
root. This writes no tracked lock and contacts no scheduler:

```bash
test "$(git rev-parse HEAD)" = "$C6_COMMIT"
git status --short
python3 hpc/osc/generate_steel_module_production_phase2c_readiness.py \
  --write-proposal

PROPOSAL="$WORK/evidence/steel-module-production-phase2c/steel-module-production-phase2c-readiness-proposal.json"
sha256sum "$PROPOSAL"
```

Download and review that exact proposal before creating R6 in the source
repository. The lock writer is intentionally a different command and requires
both the reviewed proposal path and reviewer identity:

```bash
# Run in a clean checkout whose HEAD is still exact C6.
cd "$REPO"
test "$(git rev-parse HEAD)" = "$C6_COMMIT"
git status --short
python3 hpc/osc/generate_steel_module_production_phase2c_readiness.py \
  --write-lock --proposal /absolute/path/to/reviewed-proposal.json \
  --reviewer "$USER"

git diff --name-status --no-renames "$C6_COMMIT" HEAD
```

The last command must list exactly one add-only row,
`A<TAB>hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json`.
Commit that single ordinary non-executable tracked file as R6; R6 must be the
direct non-merge child of C6.
After pushing R6 and pulling it on OSC, validate the clean lock-only checkout:

```bash
python3 hpc/osc/validate_steel_module_production_phase2c_readiness.py \
  --check-only
python3 hpc/osc/manage_steel_module_production_attempt.py status
```

The terminal state must report readiness true, exactly one accepted preflight
job, zero production intents, and zero production Slurm jobs. **Stop here.** Do
not run `prepare-intent`, do not submit BC-S1, and do not invoke Geant4 during
Phase 2C. The first future production intent is the separately reviewed
`predecessor-retry` for all 32 tasks, 8,000 events, and the original 64 seeds;
it belongs to the next phase. That future array remains held after submit
verification; `reconcile` can recover the same identity but cannot release it.
Only a separate checksum-bound `release-intent` command may call
`scontrol release`. A cancelled never-submitted intent, or a submission proven
absent by the two-snapshot rule, may be replaced by another exact
`predecessor-retry`; no job or seeds were consumed. Immediately before release,
the journal records `release-invoked`, which is the only ambiguous release
window admitted by workers. Multiple exact scheduler matches instead create a
permanent quarantine and block release.

The downstream finalizer, checkpoint, analyzer, renderer, and progression
recorder remain applicable only after such a future production successor has
actually run all 32 tasks and produced valid selected results. The production
analyzer must then keep pooled scintillation production equally weighted
across the three layout strata even when their final event counts differ:
bootstrap within each stratum at its available sample size and average the
three means with `1/3` weights.

### Electron differential regression

Before statistical interpretation, build one `OpNovice2` executable from the
`practice` baseline commit `50ec06d4` and one from the candidate study commit,
using the same pinned Geant4 `11.4.2` image. The dedicated wrapper supplies the
same Geant4 dataset environment as the scan runner:

```bash
G4_APPTAINER_IMAGE=/path/to/geant4-11.4.2.sif \
G4_DATA_ROOT=/path/to/geant4-data/11.4.2 \
  hpc/osc/run_realistic_neutron_electron_regression_apptainer.sh \
  --baseline-executable /path/to/practice-50ec06d4/OpNovice2 \
  --candidate-executable /path/to/candidate/OpNovice2 \
  --baseline-label practice-50ec06d4 \
  --candidate-label "$(git rev-parse HEAD)" \
  --output-dir /path/to/evidence/electron-regression
```

The script generates one canonical absorber-disabled `100-event`, fixed-seed,
centered `1 MeV` electron macro and runs both binaries against it. It records
both executable hashes, summaries, ROOT files, logs, comparison tolerances, and
checksums. A failed comparison is preserved as evidence and blocks statistical
interpretation.

The current runner's explicit `/opnovice2/sipm/layout single` command is omitted
only from the baseline macro because `practice@50ec06d4` predates that messenger;
`single` is already the candidate default, so the two detector configurations
remain equivalent. Execution failures also preserve their macros and logs in a
timestamped `.failed-*` evidence directory instead of deleting the diagnosis.

### Generate, submit, and finalize one stage

Generate one stage only after its preceding gate has passed. For example:

```bash
python3 hpc/osc/generate_realistic_neutron_campaign.py \
  --out-dir /path/to/campaigns/convergence-pilot \
  --stage convergence-pilot \
  --campaign-seed 20260714 \
  --environment-mode osc-production \
  --geant4-version 11.4.2 \
  --image /path/to/geant4-11.4.2.sif \
  --g4-data-manifest /path/to/g4-data-manifest.json \
  --build-artifact /path/to/frozen/OpNovice2
```

Validate without contacting Slurm:

```bash
python3 hpc/osc/submit_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/convergence-pilot \
  --check-only
```

Submit through the campaign wrapper, not through raw `sbatch`. The wrapper
creates a read-only Git archive and an append-only attempt record, then gives
every array task an isolated output directory:

```bash
python3 hpc/osc/submit_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/convergence-pilot \
  --account PAS2524 \
  --g4-data-root /path/to/geant4-data/11.4.2 \
  --frozen-root /path/to/frozen-campaign-sources
```

`--resume` submits only tasks never recorded in a successful Slurm submission.
`--retry-failed` consults `sacct` and submits only terminal or completed tasks
that lack a valid, checksum-verified task result. Retries keep the original
logical task ID, configuration hash, and seeds, while writing a new attempt
directory; prior evidence is never overwritten.

After every expected task has one unambiguous valid result, finalize, preserve
the complete D-012 ROOT audit, and run the pinned event-level bootstrap
analysis:

```bash
python3 hpc/osc/finalize_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/convergence-pilot

python3 hpc/osc/audit_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/convergence-pilot

python3 hpc/osc/analyze_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/convergence-pilot \
  --finalized-dir /path/to/campaigns/convergence-pilot/finalized
```

The finalizer rejects missing or changed artifacts, mismatched configuration or
environment identities, and ambiguous duplicate successes. The event audit
checks every row and every field of every original ROOT `scan` tree against the
complete D-012 causal invariants and aggregate summaries. The analyzer reads
those same audited trees, applies the pinned 10,000-resample independent event
bootstrap, writes thickness-ratio and absorber-convergence intervals plus
Wilson intervals for interaction and zero-light fractions, and emits a
review-only production-`N` recommendation for a convergence pilot.
`local-dev` campaigns and the analyzer's explicitly marked fixture mode are
never accepted as statistical evidence.

### Two-task benchmark

The benchmark is exactly two centered tasks: `4 mm` and `16 mm`, each with
`100` full-optical events and the `500 x 500 x 40 mm` absorber. With the same
immutable image, data manifest, and candidate executable that passed the new
geometry smoke, generate it as follows:

```bash
python3 hpc/osc/generate_realistic_neutron_campaign.py \
  --out-dir /path/to/campaigns/benchmark \
  --stage benchmark \
  --campaign-seed 20260715 \
  --environment-mode osc-production \
  --geant4-version 11.4.2 \
  --image /path/to/geant4-11.4.2.sif \
  --g4-data-manifest /path/to/g4-data-manifest.json \
  --build-artifact /path/to/frozen/OpNovice2

python3 hpc/osc/submit_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/benchmark \
  --check-only

python3 hpc/osc/submit_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/benchmark \
  --account YOUR_ACCOUNT \
  --g4-data-root /path/to/geant4-data/11.4.2 \
  --frozen-root /path/to/frozen-campaign-sources
```

After both array tasks finish, run finalization and the ROOT audit first, then
capture `sacct` while the accounting rows are readily available:

```bash
python3 hpc/osc/finalize_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/benchmark

python3 hpc/osc/audit_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/benchmark

python3 hpc/osc/summarize_realistic_neutron_benchmark.py \
  --campaign-dir /path/to/campaigns/benchmark

python3 hpc/osc/analyze_realistic_neutron_campaign.py \
  --campaign-dir /path/to/campaigns/benchmark \
  --finalized-dir /path/to/campaigns/benchmark/finalized
```

The benchmark report preserves raw `sacct` rows, elapsed time, MaxRSS,
MaxVMSize when reported, ROOT and total artifact bytes per event, optical
multiplicities, interaction/zero-light fractions, and a reviewable
events-per-task recommendation for the `1000-event` convergence pilot. The
report measures which thickness is limiting rather than assuming it is the
`16 mm` tile.

## Point-Level Array Scans

For longer scans, split the grid so each Slurm array task runs one `(x, y)` point in serial Geant4. This keeps the stable `G4RUN_MANAGER_TYPE=Serial` path while letting Slurm run points concurrently.
`submit_scan.sbatch` automatically tags array outputs with `SCAN_RUN_ID_SUFFIX=job<jobid>_task<taskid>` so tasks that start in the same second do not overwrite each other's run directories.

Run the checked-in 3x3, 100-event pilot plan:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/scan_args_pilot_3x3_100events.txt \
  sbatch -A YOUR_ACCOUNT --array=1-9 hpc/osc/submit_scan.sbatch
```

After the array finishes, merge the per-point `efficiency_map.csv` files:

```bash
sacct -j JOBID --format=JobID,JobName,State,ExitCode,Elapsed
python3 hpc/osc/merge_array_efficiency_maps.py --job-id JOBID
```

For the 24-array lab v2 divergence calibration, finalize only the most recent
submission attempt after `squeue` is empty:

```bash
python3 hpc/osc/finalize_lab_v2_calibration.py
```

The finalizer verifies all 516 numbered task logs and their `Scan complete`
markers before merging. It writes one uniquely named map per plan plus
`finalization_summary.tsv` and `all_efficiency_maps.csv` under
`test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_calibration/`.

To generate the focused 45/75 mrad high-statistics validation without also
regenerating the surface study:

```bash
EVENTS=5000 \
DIVERGENCES_MRAD="45 75" \
DIVERGENCE_STAGE=divergence_validation \
GENERATE_SURFACE_COMPARISON=0 \
  hpc/osc/generate_lab_v2_realsetup_plans.sh
```

This produces eight arrays (four tile geometries at each divergence) with 172
tasks total. Submit that bounded set with the same resumable helper by
overriding its plan-set expectations:

```bash
hpc/osc/submit-lab-v2-validation.sh PAS2524 /path/to/geant4-data
```

Use the same command with a final `--check-only` before submission to validate
the plan count, task count, and Geant4 dataset path without calling `sbatch`.

After completion, finalize, audit, merge, and package the validation set with:

```bash
hpc/osc/finalize-lab-v2-validation.sh
```

The wrapper checks all 172 task logs, writes the combined maps, and packages
the merged outputs, plans, and submission manifest under
`test/OpNovice2/lab_run_v2/`. The equivalent generic finalizer invocation is:

```bash
python3 hpc/osc/finalize_lab_v2_calibration.py \
  --manifest hpc/osc/generated/lab_v2_realsetup/divergence_validation/submission-manifest.tsv \
  --output-dir test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_validation \
  --expected-plans 8 --expected-tasks 172
```

By default the merge writes a ROOT-compatible run directory under `test/OpNovice2/scan_runs`.
For beam-size scans, the directory name is inferred from `run_config.json`, for example `test/OpNovice2/scan_runs/week9_2mm_thickness_1mm_beam_sigma/efficiency_map.csv`.
If auto-naming is not specific enough, pass `--label NAME`; if you need the old flat-file behavior, pass an explicit CSV path with `--out path/to/file.csv`.

If some tasks are still running or failed, the merge tool reports which Slurm output lacks `Scan complete`. To inspect only completed tasks, add `--allow-missing`.

Generate a new point-level plan:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week5_grid_1000events.txt \
  --description "Week 5 19x19 position scan, 1000 events per point" \
  --events 1000 \
  --tank-size "100 100 5 mm" \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm
```

The same generator can sweep Week 6 thicknesses or Week 7 surface presets by repeating options:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week6_thickness_1000events.txt \
  --events 1000 \
  --tank-size "100 100 4 mm" \
  --tank-size "100 100 8 mm" \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week7_surface_1000events.txt \
  --events 1000 \
  --tank-size "100 100 5 mm" \
  --surface-preset polished \
  --surface-preset ground \
  --surface-preset wrapped \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm
```

Additional painted presets are supported for lab matching:
`polishedfrontpainted`, `groundfrontpainted`, `polishedbackpainted`, and
`groundbackpainted`.

The lab v2 real-setup scaffold uses the repository's digitized EJ-510
reflectivity curve, the official EJ-550 constant refractive index 1.46, and the
confirmed circular Gaussian beam sigma of 5 mm. Grease thickness remains a
required experimental input:

```bash
GREASE_THICKNESS="... mm" \
hpc/osc/generate_lab_v2_realsetup_plans.sh
```

Divergence calibration starts from `polishedfrontpainted`, matching the
observed lab setup where EJ-510 is applied directly to the EJ-200 tile. The
later four-surface comparison retains both backpainted variants as sensitivity
models. Those variants explicitly use an air-gap surface `RINDEX=1.0003`; this
is recorded as a caveat in every plan and in `run_config.json`, not presented as
the refractive index of EJ-510. Override it only for a deliberate model study
with `BACKPAINTED_AIR_RINDEX=...` or the runner's `--surface-rindex`/
`--surface-rindex-csv` options.

The focused 75 mrad, 5000-event surface study can reuse the completed
`polishedfrontpainted` validation baseline and generate only the other three
surface hypotheses:

```bash
EVENTS=5000 \
BEST_DIVERGENCE_MRAD=75 \
SURFACE_COMPARISON_STAGE=surface_comparison_75mrad_5000events \
SURFACE_PRESETS="groundfrontpainted polishedbackpainted groundbackpainted" \
GENERATE_DIVERGENCE_CALIBRATION=0 \
GENERATE_SURFACE_COMPARISON=1 \
  hpc/osc/generate_lab_v2_realsetup_plans.sh
```

This produces 12 arrays and 258 point tasks. Validate or submit exactly that
set with `hpc/osc/submit-lab-v2-surface.sh`; after completion,
`hpc/osc/finalize-lab-v2-surface.sh` audits the task logs, merges the maps, and
packages both the new surface outputs and the existing polished-front baseline.

After downloading and extracting the archive, score and plot the four surfaces:

```bash
python3 test/OpNovice2/analyze_lab_v2_surface.py
root -l -b -q 'test/OpNovice2/plot_lab_v2_surface.C()'
```

The analyzer and ROOT macro tag every output filename with the selected
divergence (for example, `_75mrad`). For a different divergence, pass the same
value to `--divergence-mrad` and as the macro's second argument so separate
studies can share one analysis directory without overwriting each other.

The Stage A 5x5-only divergence refinement keeps every physical setting fixed
and adds high-statistics intermediate points between the existing 45 and
75 mrad runs:

```bash
EVENTS=5000 \
DIVERGENCE_STAGE=divergence_refinement_5x5_polishedfrontpainted_5000events \
DIVERGENCES_MRAD="50 55 60 65 70" \
DIVERGENCE_SAMPLE_SET=5x5 \
CALIBRATION_SURFACE=polishedfrontpainted \
GENERATE_DIVERGENCE_CALIBRATION=1 \
GENERATE_SURFACE_COMPARISON=0 \
  hpc/osc/generate_lab_v2_realsetup_plans.sh
```

This produces 10 arrays and 150 tasks. Every plan filename and header records
`polishedfrontpainted`, the empirical EJ-510 model, Gaussian sigma 5 mm, the
Sr-90 spectrum, the 2.4 mm SiPM, and 5000 events per point. The 45 and 75 mrad
validation maps are reused rather than resubmitted. Submit with
`hpc/osc/submit-lab-v2-5x5-divergence-refinement.sh`; the shared Slurm template
allows two hours per task. After completion,
`hpc/osc/finalize-lab-v2-5x5-divergence-refinement.sh` audits and merges all 150
tasks into an explicitly labeled output directory and archive.

With 55 mrad selected as the provisional Stage A nominal value, the focused
5x5-only surface comparison reuses the new 5000-event polished-front maps and
generates only the other three surface hypotheses:

```bash
EVENTS=5000 \
BEST_DIVERGENCE_MRAD=55 \
SURFACE_COMPARISON_STAGE=surface_comparison_5x5_55mrad_5000events \
SURFACE_SAMPLE_SET=5x5 \
SURFACE_PRESETS="groundfrontpainted polishedbackpainted groundbackpainted" \
GENERATE_DIVERGENCE_CALIBRATION=0 \
GENERATE_SURFACE_COMPARISON=1 \
  hpc/osc/generate_lab_v2_realsetup_plans.sh
```

This produces 6 arrays and 90 tasks. The baseline is specifically the 55 mrad
maps from Stage A jobs `50392947` and `50392952`, not the earlier 500-event
calibration. Validate or submit with
`hpc/osc/submit-lab-v2-5x5-surface-55mrad.sh`; finalize with
`hpc/osc/finalize-lab-v2-5x5-surface-55mrad.sh`. The surface analyzer accepts
`--sample-set 5x5` and ranks the hypotheses with the same equal-weight,
all-point scaled normalized RMSE used for divergence calibration. The complete
commands and provenance are recorded in the generated stage `README.md`.

The default grease absorption model is `transparent` (`ABSLENGTH=1000 mm`).
For an explicitly derived sensitivity model based on Eljen's 0.1 mm
transmission plot, use:

```bash
GREASE_THICKNESS="... mm" \
GREASE_ABSORPTION_MODEL=ej550-transmission-derived \
hpc/osc/generate_lab_v2_realsetup_plans.sh
```

`BEAM_SIGMA`, `GREASE_RINDEX`, `GREASE_RINDEX_CSV`, and
`GREASE_TRANSMISSION_CSV` remain optional overrides. The transmission-derived
model converts the digitized transmission values to an effective bulk
absorption length with `L=-0.1 mm/ln(T)`; it is recorded as a derived model in
`run_config.json`, not as a directly tabulated manufacturer absorption length.

For Week 8 dimple scans, add dimple options:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week8_dimple_1000events.txt \
  --events 1000 \
  --tank-size "100 100 5 mm" \
  --dimple --dimple-radius 3 --dimple-sipm-mode surface \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm
```

For side-mounted SiPM scans, add the existing scan-runner geometry overrides:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/side_x_center/week6_side_x_center_thickness_5mm_21x21_100events.txt \
  --description "Week 6 side-mounted +X center SiPM, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --tank-size "100 100 5 mm" \
  --sipm-face +X \
  --sipm-local-position "0 0 0 cm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm
```

For beam-size scans, keep the same point-level workflow and add `--beam-sigma`.
`--beam-sigma` requires `--source-mode gps`; omitted or `0` keeps the point-source GPS position, while a positive value uses a circular 2D Gaussian source spot around each scan center. The value is interpreted in `--grid-unit` units.

Generate bottom-center beam-size plans for the first production pass:

```bash
mkdir -p hpc/osc/generated/beam_sigma_bottom_center

for t in 2 4 5 8 10; do
  for s in 1 2 3; do
    python3 hpc/osc/generate_scan_plan.py \
      --out "hpc/osc/generated/beam_sigma_bottom_center/week9_beam_sigma_${s}mm_thickness_${t}mm_21x21_100events.txt" \
      --description "Beam sigma ${s} mm, bottom center, ${t} mm thickness, 21x21, 100 events per point" \
      --events 100 \
      --source-mode gps \
      --tank-size "100 100 ${t} mm" \
      --beam-sigma "${s}" \
      --x-min -50 --x-max 50 \
      --y-min -50 --y-max 50 \
      --step 5 --grid-unit mm
  done
done
```

Submit one or two arrays at a time, using `-A` on the command line rather than storing the account in tracked files:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/beam_sigma_bottom_center/week9_beam_sigma_1mm_thickness_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch
```

Merge after completion:

```bash
sacct -j JOBID --format=JobID,JobName,State,ExitCode,Elapsed
python3 hpc/osc/merge_array_efficiency_maps.py --job-id JOBID
```

The beam-size merge output defaults to a directory such as:

```text
test/OpNovice2/scan_runs/week9_2mm_thickness_1mm_beam_sigma/efficiency_map.csv
```

For a quick physics QA of a Gaussian beam run, inspect the ROOT ntuple columns `shoot_x_mm` and `shoot_y_mm`; their mean should be near the scan center, and their RMS should be close to the requested `beam_sigma`.

For Week 10 Sr-90 source-model scans, keep the GPS point-level workflow and add
`--source-model sr90-spectrum`. This is the Week 10.1 production default: GPS
samples the checked-in `sr90_allowed_beta_v1` Sr-90/Y-90 beta spectrum table.
The older `--electron-energy-mode sr90Beta` path remains available as
`--source-model sr90-empirical` for historical comparison only.

```bash
mkdir -p hpc/osc/generated/week10_sr90

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_sr90/week10_sr90_thickness_5mm_21x21_100events.txt \
  --description "Week 10 Sr-90 spectrum source, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --source-mode gps \
  --source-model sr90-spectrum \
  --tank-size "100 100 5 mm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm

G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_sr90/week10_sr90_thickness_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch
```

The default merge label for this source mode is inferred from `run_config.json`,
for example `test/OpNovice2/scan_runs/week10_5mm_thickness_sr90_spectrum_source/efficiency_map.csv`.

For spectrum QA after merge, run:

```bash
cd test/OpNovice2
root -b -q 'plot_sr90_spectrum_qa.C("scan_runs/week10_5mm_thickness_sr90_spectrum_source")'
```

Week 10.2 adds angular divergence as a separate GPS beam property, not as part of the beam spot size. Internally, `--beam-divergence-mrad VALUE` is a GPS `beam2d` angular Gaussian with `/gps/ang/sigma_x VALUE mrad` and `/gps/ang/sigma_y VALUE mrad`; it is not a hard cone half-angle. The early `10 mrad` pass was only a small decision/smoke scan. After the lab estimate of roughly `5 deg` (about `100 mrad`), use sensitivity plans around `75, 100, 125 mrad`, while reusing or rerunning the corresponding `0 mrad` baselines.

```bash
mkdir -p hpc/osc/generated/week10_beam_divergence_sensitivity

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_beam_divergence_sensitivity/week10_1mev_100mrad_divergence_5mm_21x21_100events.txt \
  --description "Week 10.2 fixed 1 MeV, 100 mrad beam2d divergence, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --source-mode gps \
  --primary-energy "1 MeV" \
  --beam-divergence-mrad 100 \
  --tank-size "100 100 5 mm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_beam_divergence_sensitivity/week10_sr90_spectrum_100mrad_divergence_5mm_21x21_100events.txt \
  --description "Week 10.2 Sr-90 spectrum, 100 mrad beam2d divergence, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --source-mode gps \
  --source-model sr90-spectrum \
  --beam-divergence-mrad 100 \
  --tank-size "100 100 5 mm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm
```

Repeat the same two commands with `75` and `125` when generating the full sensitivity set. Submit each divergence plan with its matching args file:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_beam_divergence_sensitivity/week10_1mev_100mrad_divergence_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch

G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_beam_divergence_sensitivity/week10_sr90_spectrum_100mrad_divergence_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch
```

The auto-merge labels include the divergence token, for example `week10_5mm_thickness_1MeV_energy_100mrad_beam_divergence` and `week10_5mm_thickness_100mrad_beam_divergence_sr90_spectrum_source`. For a quick QA, inspect `run_config.json` for `beam.angular_model = "beam2d"` plus `beam.divergence_parameter = "sigma_x=sigma_y"`, and compare `hit_x/y/z` or `scint_centroid_x/y/z` against the corresponding no-divergence baseline.

Week 10.1b also provides a validation-first decay source model:
`--source-model sr90-decay`. It uses a GPS Sr-90 ion primary at rest and
`G4RadioactiveDecayPhysics`; this is not the Week 10 default plan until the
decay beta spectrum has been checked. Use a new build directory or force a
rebuild for the first OSC smoke so the executable includes the decay physics
registration:

```bash
mkdir -p hpc/osc/generated/week10_sr90_decay

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_sr90_decay/week10_sr90_decay_5mm_center_100events.txt \
  --description "Week 10.1b Sr-90 decay source smoke, 5 mm thickness, center point" \
  --events 100 \
  --source-mode gps \
  --source-model sr90-decay \
  --tank-size "100 100 5 mm" \
  --x-min 0 --x-max 0 \
  --y-min 0 --y-max 0 \
  --step 5 --grid-unit mm

G4_FORCE_REBUILD=1 G4_BUILD_DIR=build-osc-sr90-decay \
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_sr90_decay/week10_sr90_decay_5mm_center_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-1 hpc/osc/submit_scan.sbatch
```

After copying the ROOT outputs locally, validate with:

```bash
cd test/OpNovice2
root -b -q 'plot_sr90_decay_spectrum_qa.C("scan_runs/week10_5mm_thickness_sr90_decay_source","scan_runs/week10_5mm_thickness_sr90_spectrum_source")'
```

Acceptance is spectrum-first: the `decay_betas` ntuple should include both the
Sr-90 endpoint near `0.546 MeV` and the Y-90 high-energy tail near `2.28 MeV`.
If the decay run only reaches the Sr-90 endpoint, the chain is not yet suitable
for production scans; use the already validated `sr90-spectrum` plan instead.

Useful environment variables:

- `G4_APPTAINER_IMAGE=/path/to/geant4.sif`
- `G4_DATA_ROOT=/path/to/geant4-data`
- `G4_BUILD_DIR=build-osc`
- `G4_BUILD_JOBS=4`
- `G4_FORCE_REBUILD=1`
- `G4_PRINT_DATA_ENV=1`
- `G4RUN_MANAGER_TYPE=Serial`
- `PLOT_WITH_ROOT=0`

The wrapper binds this repository into the container at `/work/g4optics`, builds `test/OpNovice2` in `build-osc`, then calls the existing `run_sipm_cavity_scan.sh`. OSC runs default to `G4RUN_MANAGER_TYPE=Serial` because the scan macros use one Geant4 thread per point.

## Troubleshooting

If Geant4 aborts with messages such as `G4ENSDFSTATEDATA environment variable must be set`, `G4LEDATA data directory was not found`, or `G4LEVELGAMMADATA environment variable not set`, the Apptainer image does not expose the Geant4 runtime datasets. Keep the datasets in a persistent OSC directory and pass it to the wrapper:

```bash
hpc/osc/install_geant4_data.sh ~/geant4-data/11.4.2

G4_DATA_ROOT=~/geant4-data/11.4.2 G4_PRINT_DATA_ENV=1 hpc/osc/run_scan_apptainer.sh
```

The wrapper binds `G4_DATA_ROOT` into the container, auto-detects common Geant4 data paths, and exports Geant4 data environment variables. To inspect what the wrapper finds, run:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 G4_PRINT_DATA_ENV=1 hpc/osc/run_scan_apptainer.sh
```

If `G4ENSDFSTATEDATA` is still missing, inspect the image contents directly:

```bash
apptainer exec geant4.sif find /opt/geant4-data /opt/geant4 /usr/local/share /usr/share -type d -name 'G4ENSDFSTATE*' -print
```
