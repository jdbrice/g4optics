# First independent ten-layer SiPM layout scan

This is a fixed first scientific sample of 24,000 incident neutrons: 24 configurations, 1,000 independent events per configuration, divided into ten independently seeded 100-event tasks per configuration (240 tasks total). The sample size is fixed before observing the scientific results. Any follow-up sample, replacement run or model revision is recorded separately; there is no automatic precision-driven extension. Publish this protocol in the draft PR and freeze its explicit commit before submission. This document and the analysis program do not submit jobs.

## Frozen model and execution

- Freeze the final explicit commit on the draft PR branch as `approved_source_commit` in the campaign manifest. Export that commit and independently rebuild its C++ on OSC; bind the new executable checksum to that source commit. Verify that the rebuilt model retains the validated C++/physics definitions and passes the agreed qualification before launch. Do not execute the main-branch checkout or reuse the historical executable. Commit `5ef9d3b695583e1daef980c07ec71af47d37938f` is historical model-validation provenance only, not this campaign's runtime source identity.
- Use the version-pinned x86_64 image documented in the runtime guide (or an existing SIF with matching provenance), and record the actual SIF checksum. Validate all data files against an independent checksum manifest produced by the official-data installation. Keep Geant4 11.4.2, x86_64, and Serial. Qualify the exact PR build before its first scientific events; any physical-model difference must be resolved first.
- Ten 40 mm steel layers; 100 × 100 mm scintillator tiles of thickness 4, 8, 12, 16, 20, or 24 mm; the same nine 0.5 mm intermodule readout gaps in every layout. Each SiPM has a nominal 2.4 × 2.4 mm collection face and 0.5 mm thickness.
- Four layouts: back-four, back-two (diagonal pair), back-center, and edge-two. Keep materials, optical surfaces, counting rules, geometry/source definitions, and physics unchanged from the validated model.
- Use the existing 1 GeV neutron source and its thickness-dependent position. For tile thickness t in mm, core length is `10*(40+t)+4.5`, and source z is `core_length/2+1.5` mm. Preserve the already validated direction and source distribution.
- Each task is a separate process and has a preregistered seed pair. All 240 pairs and all 480 seed values must be distinct and must not overlap the recorded qualification/benchmark seed values. Analysis RNG is separate from simulation RNG.
- Submit without a `%` concurrency cap. Actual execution remains subject to OSC/project limits. Resource requests follow the measured benchmark and are recorded in the frozen submission plan; this document makes no wall-time guarantee.
- Keep every original task/attempt, log, exit status, failure, and output. A failed task is not silently accepted or replaced. All 240 original registered tasks must be accepted before the complete-scan analysis starts. Incomplete data may be described operationally, but are not used to publish the planned comparisons.

## Endpoints and estimands

The primary endpoint is total collected photons D per incident neutron, summed over all active SiPMs in all ten layers. Its estimator is the arithmetic event mean, including zero-response events. Each incident neutron is the statistical observation; its photons, layers, and sensors are not independent observations.

The 18 primary contrasts are the absolute difference in mean D between each of the three back layouts and edge-two at the same tile thickness. Direction is `back layout − edge-two`; positive values favor the back layout on this endpoint. The point estimate of relative change, `mean(D_back)/mean(D_edge-two) − 1`, is useful context and is reported with an explicitly descriptive interval. No same-seed pairing is used.

Secondary/descriptive outputs for every configuration are:

- Mean legacy generated count G per neutron and the legacy collection ratio `sum(D)/sum(G)`. The latter is a ratio of totals, not the mean of per-event D/G, and is not a full photon-birth/terminal accounting efficiency.
- Mean all-origin and same-layer-origin collected counts per layer and per active sensor. Report inactive sensor slots as absent, not as observed zeros. Keep the global copy mapping `4*layer+local_sensor`.
- Mean generated photons and tile/steel energy deposits per layer; sparse cross-layer collection and its share of D.
- Mean D divided by the nominal total active collection-face area. Areas are 230.4, 115.2, 57.6, and 115.2 mm² for back-four, back-two, back-center, and edge-two respectively across the ten layers. This is a descriptive area normalization, not an equal-area geometry experiment or a causal separation of placement and sensor count.
- Zero-response fraction, median and 90th/99th percentiles, sample maximum, coefficient of variation, maximum-event contribution to total D, and top 1% event contribution. Also retain the ten task-block means to reveal seed-block variability. No winsorization, tail clipping, removal of zero events, or truncation by observed response is allowed.

## Uncertainty and multiplicity

Use 10,000 bootstrap replicates and the fixed analysis seed `2026100209`. Independently for each configuration, resample its 1,000 whole incident-neutron events with replacement. Apply the same event weights to D, G, all layer/sensor quantities, and energy deposits. Preserve their within-event covariance. Task boundaries organize provenance; they are not treated as independent physical clusters. Reuse the same edge-two bootstrap replicates for its three contrasts at a given thickness.

For each of the 18 absolute primary contrasts, report percentile endpoints at `0.05/(2*18)` and `1−0.05/(2*18)` (approximately 0.00138889 and 0.99861111). These are individual 99.7222% bootstrap intervals with a nominal Bonferroni family-wise 95% target. Also report ordinary 95% descriptive intervals. The bootstrap approximation does not provide exact finite-sample coverage; with 10,000 replicates the adjusted tails use only about 14 replicates each, so their Monte Carlo stability is limited.

Configuration means, relative differences, legacy D/G, zero fractions, layers, sensors, energy and area-normalized outputs use descriptive 95% percentile intervals without a simultaneous-coverage claim. Do not treat any one of these many secondary intervals as a multiplicity-adjusted discovery. Where a denominator is zero, report the ratio and interval as undefined and give the number of invalid bootstrap replicates; do not drop invalid replicates and recalculate a more favorable interval.

One thousand events per configuration is the prespecified first sample size, not a promised precision. Broad intervals or unstable tails are reported as results. An interval containing zero does not establish equivalence, and a mean ranking alone does not establish a winner. No automated extension is triggered by the interval widths or outcomes.

## Completion and analysis checks

Before numerical comparisons, verify the frozen manifest checksum; exactly 24 allowed configurations and ten unique 100-event blocks each; 240 unique task IDs; unique registered seed values; exclusion of prior engineering seeds; and unchanged simulation/program/image/data identities. Require every accepted receipt to bind its task, actual seeds, original output checksums, normal process exit, and terminal scheduler completion. Recheck ROOT, summary CSV, log, and task configuration against the frozen identities and rerun the current layout audit. Within each task, event IDs must be exactly 0–99; global analysis identity is `(task_id,event_id)`.

The current audit checks ROOT/summary consistency, active-sensor and transfer sums, known origin labels, legacy layer G, finite energies, the registered source, and log failures. Its NoRINDEX check is limited to what the preserved program logs: the inherited boundary counter observes the first geometric boundary after each tracking entry. Therefore describe this as “no NoRINDEX report found in the logs,” not as proof that every optical trajectory was free of NoRINDEX. The new study does not introduce a full photon ledger or a physics correction.

## Delivery

Save a machine-readable result with all 24 summaries, 18 contrasts, the uncertainty specification, block diagnostics, manifest/source hashes and input inventory. Save bootstrap replicate arrays separately so interval calculations can be inspected without another simulation. Produce: D and G versus thickness; the 18 relative/absolute contrasts; layer response; active sensor contributions; zero response; and distribution/tail diagnostics. Clearly label all plots with the fixed 0.5 mm gap, model/environment identity, sample size, and interval scope. Archive the exact analysis program and dependency versions alongside the result. This package is a first-sample comparison under this frozen model, not an experimental validation of the materials or a guaranteed precision result.

## Companion program interface

`analysis.py` consumes the frozen formal campaign manifest and its accepted-task receipts directly. No additional adapter or control service is required. The analyzer requires a caller-provided manifest SHA-256, the frozen layout audit module and its SHA-256, and checks all accepted tasks before calculating a bootstrap. Runtime `simulation_identity.source_commit` must equal the manifest's explicit `approved_source_commit`, which is the final draft-PR commit. No simulation, submission, or automatic retry is performed by the analyzer. The manifest/receipt example is available through `python tools/layout_study/analysis.py --print-schema`. Only results from the approved first formal scan belong in this manifest; engineering/benchmark samples are excluded.


See [FORMAL_RUN.md](FORMAL_RUN.md) for the version-freezing, submission and collection commands.
