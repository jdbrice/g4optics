# Steel Module Convergence Pilot — v1 Review and Analysis-v2 Gate

Status: historical v1 review; analysis-v2 gate subsequently completed and
superseded for current decisions by
`docs/decisions/steel-module-analysis-v2-review-v1.md`

Study preset: `steel-module-scan-v1`

Reviewed: 2026-07-16

> This record preserves the v1 evidence and the gate as it stood on the review
> date. Statements below describing analysis-v2 as future work are historical;
> the completed v2 review and accepted fixed-reference absorber and 10% staged
> production policies are in
> `docs/decisions/steel-module-analysis-v2-review-v1.md`.

## Technical summary

The 30-configuration convergence pilot completed all 120 logical tasks and
passed campaign finalization, the integrated ROOT event audit, and both
finalized and analysis checksum verification. The accepted sample contains
1,000 incident 1 GeV-kinetic-energy neutrons per configuration, split across
four independent 250-event seed blocks.

The preliminary physics answer is layout-dependent. At the `500 mm` absorber,
increasing tile thickness from `4` to `24 mm` increased scintillation
production by approximately `4.6–5.1x`, while optical collection decreased.
The resulting aggregate SiPM response ratio was:

| Layout | 24/4 production | 24/4 collection | 24/4 net SiPM response | Preliminary interpretation |
| --- | ---: | ---: | ---: | --- |
| `back-center` | `4.641` `[3.294, 6.480]` | `0.160` `[0.117, 0.234]` | `0.742` `[0.461, 1.303]` | no resolved net gain |
| `edge-center` | `5.052` `[3.813, 6.718]` | `0.425` `[0.415, 0.434]` | `2.148` `[1.621, 2.860]` | resolved net gain |
| `back-four` | `4.814` `[3.583, 6.567]` | `0.711` `[0.687, 0.748]` | `3.424` `[2.553, 4.666]` | resolved aggregate net gain |

Values in brackets are event-bootstrap 95% intervals. `back-four` has four
times the active SiPM area of either single-SiPM layout; its aggregate response
must not be presented as a sensor-area-normalized efficiency improvement.

The pilot does **not** establish absorber equivalence. None of the production
or net-response intervals for `200/500` or `300/500` lay wholly inside the
predefined `[0.95, 1.05]` review window. At the same time, every production and
net interval included unity, so the pilot also found no resolved absorber-size
difference. These are different statements: absence of a detected difference
is not evidence of equivalence. Use `500 mm` as the conservative reference for
the next analysis, but do not label it “converged.”

The v1 automatic sizing output (`58,000` events per configuration for a 10%
relative half-width or `231,750` for 5%) is not accepted. It is driven by the
noisy `back-center 8/4` net ratio and requires every enumerated comparison to
meet one common precision target. That is not yet the agreed scientific
estimand. Analysis-v2 must first diagnose the zero-inflated heavy tail, exploit
the readout-invariance of scintillation production, define primary contrasts,
and then issue contrast-specific sizing projections.

## Short preliminary update for the professor

> We completed a 30-configuration pilot with 1,000 neutrons per configuration.
> For a 500 mm transverse steel absorber, the 24 mm tile produced about five
> times as many scintillation photons as the 4 mm tile. Whether that becomes a
> larger measured signal depends strongly on the SiPM layout: the 24/4 SiPM
> response ratio was `0.74 [0.46, 1.30]` for one SiPM at the back center,
> `2.15 [1.62, 2.86]` for one SiPM on the edge, and
> `3.42 [2.55, 4.67]` for four back SiPMs. The four-SiPM result is an aggregate
> signal with four times the active area. These are preliminary bootstrap 95%
> intervals. We are doing a second analysis pass before choosing final event
> statistics; the current pilot does not yet prove that the transverse steel
> size is converged.

## Evidence identity and scope

This review interprets, but does not replace, the sealed campaign artifacts:

| Item | Accepted value |
| --- | --- |
| Campaign directory | `/users/PAS2524/anolddriver66/g4optics-rn/campaigns/steel-module-convergence-pilot-cfd7d974` |
| Slurm array | `50477293` |
| Source commit | `cfd7d974af2c5f40f7d76948172fc87ee70bfa3c` |
| Frozen executable SHA-256 | `adbcbc2facf4bb39b4671c7d47a8f9caf9e4f691b9a28a6d5e0d79d354b4cf41` |
| Environment | OSC production, Geant4 `11.4.2` |
| Campaign shape | 30 configurations × 4 blocks × 250 events = 30,000 events |
| Finalization | 120 expected and selected tasks; integrated event audit; accepted statistical evidence |
| Analysis | 10,000 event-bootstrap resamples; seed `20260715` |
| Integrity | finalized checksums and analysis checksums passed |

The campaign manifest, `tasks.tsv`, finalized validation report, event audit,
ROOT checksums, and `finalized/analysis` directory remain authoritative for
identity and numerical reproduction. This document is a review record; values
were transcribed from the checksum-verified v1 analysis outputs.

The nominal `500 mm` matrix contains six tile thicknesses (`4–24 mm` in `4 mm`
steps) and three SiPM layouts. Absorber checks use `200`, `300`, and `500 mm`
transverse sizes only at the `4` and `24 mm` endpoints for each layout.

## Metric definitions and inferential boundary

For one configuration containing `n` incident neutrons:

- **Production (`P`)**: total scintillation photons divided by `n`.
- **Collection (`C`)**: total photons entering the SiPM proxy volume divided
  by total generated optical photons.
- **Net response (`N`)**: total photons entering the SiPM proxy volume divided
  by `n`.
- **Interaction fraction**: fraction of events with at least one recorded
  primary-neutron elastic, inelastic, or capture interaction.
- **SiPM-zero fraction**: fraction of incident-neutron events with zero photons
  entering every SiPM proxy volume.

`N` is a geometrical optical-entry proxy, not a prediction of photoelectrons or
electronics response. The current setup produces essentially scintillation
light, so `P × C ≈ N`; an independent transcription check over all 18 nominal
configuration rows found a maximum relative mismatch of about `0.011%`,
consistent with printed-value rounding.

The analyzer pools the four seed blocks within a configuration, then performs
10,000 non-parametric resamples of individual events with replacement. Ratio
intervals are percentile intervals obtained by pairing the corresponding
bootstrap draws. Binary fractions use Wilson 95% intervals. These intervals
quantify Monte Carlo sampling uncertainty under this event model; they do not
cover detector-model systematics, material uncertainty, or the approximation
of the full ePIC steel geometry by a local rectangular slab.

## Thicker tiles robustly increase production, but readout controls the net result

At `500 mm`, the full configuration curves are:

| Thickness (mm) | Layout | Production P | Collection C | Net N | Interaction | SiPM zero |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 4 | `back-center` | 6,959.46 | 0.030642 | 213.25 | 0.336 | 0.796 |
| 8 | `back-center` | 9,613.35 | 0.013545 | 130.21 | 0.335 | 0.819 |
| 12 | `back-center` | 17,241.90 | 0.008383 | 144.53 | 0.374 | 0.763 |
| 16 | `back-center` | 26,303.22 | 0.006618 | 174.06 | 0.347 | 0.781 |
| 20 | `back-center` | 27,827.78 | 0.007682 | 213.77 | 0.347 | 0.758 |
| 24 | `back-center` | 32,300.25 | 0.004900 | 158.28 | 0.339 | 0.765 |
| 4 | `edge-center` | 6,923.09 | 0.010225 | 70.79 | 0.366 | 0.801 |
| 8 | `edge-center` | 14,401.53 | 0.007714 | 111.10 | 0.348 | 0.790 |
| 12 | `edge-center` | 18,298.03 | 0.006369 | 116.55 | 0.322 | 0.793 |
| 16 | `edge-center` | 19,041.43 | 0.005509 | 104.91 | 0.329 | 0.778 |
| 20 | `edge-center` | 29,557.18 | 0.004839 | 143.02 | 0.352 | 0.779 |
| 24 | `edge-center` | 34,975.37 | 0.004348 | 152.06 | 0.360 | 0.751 |
| 4 | `back-four` | 6,748.42 | 0.018937 | 127.80 | 0.350 | 0.805 |
| 8 | `back-four` | 10,447.96 | 0.017861 | 186.61 | 0.363 | 0.784 |
| 12 | `back-four` | 16,529.97 | 0.016195 | 267.70 | 0.367 | 0.795 |
| 16 | `back-four` | 20,849.62 | 0.015296 | 318.92 | 0.340 | 0.764 |
| 20 | `back-four` | 22,693.22 | 0.014072 | 319.35 | 0.348 | 0.775 |
| 24 | `back-four` | 32,487.14 | 0.013468 | 437.54 | 0.361 | 0.764 |

The interaction fraction is stable (`0.322–0.374`) across these nominal
configurations, while `75.1–81.9%` of incident neutrons produce no SiPM entry.
Only 181–249 events per nominal configuration therefore contribute nonzero
SiPM response. The unconditional means remain the correct calorimeter-response
estimands per incident neutron, but their uncertainty is dominated by a small
and potentially heavy-tailed responding subset.

Within `edge-center`, every thickness above `4 mm` has a net-response ratio
whose 95% interval is above unity. The same is true for `back-four`. None of the
`back-center` net-response intervals relative to `4 mm` excludes unity. This is
the direct answer to the original competition between more shower light and
poorer collection: the balance changes with optical readout geometry.

Production is physically upstream of the SiPM layout, yet its three estimates
at a fixed thickness are not identical. Their cross-layout coefficient of
variation ranges from about `1.3%` to `18.2%` across the six thicknesses. This
is evidence of finite-sample shower fluctuations, not a plausible readout
effect on scintillation creation, and motivates pooled production estimates in
analysis-v2.

## Four back SiPMs increase aggregate response, not normalized efficiency

At `24 mm`, the `back-four/back-center` aggregate net ratio is
`2.764 [2.070, 3.722]`, while its collection ratio is
`2.748 [2.424, 3.068]`. The four-SiPM layout therefore collects more total
light, as intended, but it uses four sensors and four times the nominal active
area. Its average response per installed sensor is approximately
`437.54 / 4 = 109.39` photons per neutron, versus `158.28` for the single back
sensor. This per-sensor arithmetic is descriptive only because the four
sensors occupy different positions from the center sensor.

The layout comparison also varies with thickness. `back-four/back-center` net
intervals exclude unity at `12`, `16`, and `24 mm`, but not at `4`, `8`, or
`20 mm`. `edge-center/back-center` is below unity at `16 mm`, while most other
thickness intervals include unity. These layout ratios inherit independent
shower samples and should be revisited with a common production standard before
assigning optical causality to their point-to-point variation.

## The absorber study is inconclusive for equivalence

The v1 analysis compares `200/500` and `300/500` at `4` and `24 mm`. For all 12
candidate/layout/thickness comparisons:

- every production and net-response 95% interval contains `1`;
- no production or net-response interval lies wholly inside `[0.95, 1.05]`;
- a few collection intervals lie inside the 5% window, but not consistently
  across layouts, thicknesses, and candidates.

Consequently, the evidence supports neither a resolved absorber effect nor a
5% equivalence claim. The correct operational decision is to retain `500 mm`
as the conservative reference while analysis-v2 pools the layout-invariant
production evidence and reports explicit 5% and 10% equivalence windows. A new
simulation should be considered only if that pooled analysis remains too broad
for the engineering decision.

## Why the v1 production-size projection is not a decision

The v1 projection extrapolates the observed relative bootstrap half-width as
`1/sqrt(N)`, applies a `1.25` safety factor, and rounds upward to 250-event
blocks. It then chooses the largest requirement across all thickness ratios,
all eligible layout ratios, and production, collection, and net metrics.

The limiting comparison is `back-center 8/4` net response:

```text
ratio = 0.6106
95% interval = [0.3329, 1.164]
```

That contrast is close to an unresolved cancellation between production gain
and collection loss. Requiring it—and every secondary comparison—to have the
same relative precision answers a much broader question than the professor's
primary thick-versus-thin study. The resulting `58,000` and `231,750` values
are mechanically correct outputs of the v1 rule, but they are not a justified
production design.

Even endpoint-only projections remain review aids. Independent calculations
from the v1 intervals give approximate 10% requirements of `40,250`, `10,500`,
and `12,000` events per configuration for the `24/4` net contrast in
`back-center`, `edge-center`, and `back-four`, respectively; 5% requirements
are approximately four times larger. The wide spread shows why a universal
event count should not be selected before primary contrasts and stopping rules
are explicit.

## Analysis-v2 is the next gate

The analysis-v2 implementation is now available as
`hpc/osc/analyze_steel_module_campaign_v2.py`, with an independent plotter and
synthetic infrastructure checker. It has not yet produced or reviewed the
real pilot's `finalized/analysis-v2`; implementation readiness is not a
scientific result or production authorization.

Analysis-v2 consumes the existing checksum-verified ROOT events. It must
not launch or imply authorization for a new Geant4 campaign. It must write to a
new immutable output directory such as `finalized/analysis-v2` and leave the v1
analysis unchanged.

The required analysis-v2 work is:

1. **Zero-inflated and heavy-tail diagnostics.** For production and net
   response, report unconditional means, nonzero fractions, conditional means,
   quantiles through at least the 99th percentile, maxima, and the fractions of
   the total carried by the largest 1% and 5% of events. Separate conditioning
   on primary interaction, generated light, and SiPM response where meaningful.
2. **Seed-block stability.** Report each 250-event block and leave-one-block-out
   estimates so one rare shower or one seed block cannot silently determine a
   curve point.
3. **Pooled production.** At fixed thickness and absorber size, pool
   scintillation production across the three readout layouts, which are
   physically downstream of production. Preserve the three unpooled estimates
   as a heterogeneity diagnostic.
4. **Standardized response decomposition.** Combine the common pooled
   production curve with each layout's collection estimate to create a labeled
   model-based standardized response. Keep directly observed net response as
   the primary empirical quantity; never substitute the standardized value
   without labeling it.
5. **Primary contrasts.** Treat `24/4` production and per-layout net response as
   the primary endpoint contrasts. Report the full six-point curves and their
   adjacent/intermediate ratios as secondary evidence. Do not let one secondary
   `8/4` cancellation set the global production size by default.
6. **Absorber equivalence.** Pool the layout-invariant production evidence at
   each endpoint and compare `200/500` and `300/500` on a ratio scale. Report
   both 5% and 10% equivalence windows, with “difference not detected” and
   “equivalence established” as distinct outcomes. Retain per-layout net checks
   as secondary diagnostics.
7. **Contrast-specific sizing.** Produce 5% and 10% review projections for each
   primary contrast separately, including assumptions and limiting tails. No
   single production `N` is automatically accepted.
8. **Pinned outputs and QA.** Reverify the finalized campaign and ROOT
   checksums; record schema, source commit, campaign identity, bootstrap seed,
   estimator definitions, and checksums for every v2 output. Reconcile v2
   unconditional estimates exactly against finalized totals and v1 within
   printed precision.

The fixed output contract is `configuration_estimates.csv`,
`distribution_diagnostics.csv`, `response_pathway.csv`,
`seed_block_stability.csv`, `pooled_production.csv`,
`standardized_response.csv`, `primary_contrasts.csv`,
`secondary_contrasts.csv`, `absorber_equivalence.csv`,
`production_sizing_v2.json`, `summary.md`, `analysis_config.json`, and
`SHA256SUMS`. `secondary_contrasts.csv` is explicit so the six-point and
standardized ratios remain available without controlling production sizing.

The independent plotter reads only a checksum-valid completed analysis-v2 and
writes a non-overwriting `analysis-v2-figures` sibling with five PNG/PDF figure
pairs, provenance, and its own checksums. Matplotlib is optional for the core
evidence.

## Decision state after this review

Accepted now:

- the pilot is valid preliminary statistical evidence;
- thicker tiles produce substantially more scintillation light under the
  modeled 1 GeV neutron setup;
- the net thick-tile advantage is readout-dependent;
- `edge-center` and aggregate `back-four` show resolved 24/4 gains, while
  `back-center` does not;
- `500 mm` remains the conservative reference geometry for analysis-v2.

Not accepted now:

- a claim that `500 mm` absorber transverse size is converged;
- either v1 universal production-size projection;
- a final detector-layout ranking without active-area/cost qualification;
- a final production campaign.

The next decision checkpoint is review of the pinned analysis-v2 outputs. Only
then should the team decide whether existing events suffice, whether a focused
additional pilot is necessary, and which precision target and contrast define
production.
