# Lab v2 5x5 surface comparison at 55 mrad

This stage fixes the current divergence-calibration setup and adds only the
three missing painted-surface hypotheses for the two 5x5 tiles.

## Fixed setup

- Samples: `50x50x16 mm` and `50x50x4 mm`
- Divergence: `55 mrad`
- Beam: circular Gaussian, `sigma=5 mm`
- Events: `5000` per scan point
- Source: bare Sr-90/Y-90 GPS spectrum
- SiPM: `2.4x2.4x0.5 mm`, centered on the `-Z` face
- EJ-510: repository empirical reflectivity curve
- Coupling: undimpled zero-gap EJ-550 proxy (`optical_coupling=none`)
- Backpainted air-gap sensitivity proxy: `RINDEX=1.0003`

The polished-front baseline is not resubmitted. It comes from the current
Stage A jobs `50392947` and `50392952` under commit `8ad79fba`, in
`divergence_refinement_5x5_polishedfrontpainted_5000events`. Do not substitute
the earlier 500-event 55 mrad maps.

## New work

- Surfaces: `groundfrontpainted`, `polishedbackpainted`, `groundbackpainted`
- Plans: 6
- Tasks per plan: 15
- Total tasks: 90
- Slurm wall time: 2 hours per task through `submit_scan.sbatch`

Regenerate this exact set with:

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

Validate, submit, and finalize with:

```bash
hpc/osc/submit-lab-v2-5x5-surface-55mrad.sh PAS2524 /path/to/geant4-data --check-only
hpc/osc/submit-lab-v2-5x5-surface-55mrad.sh PAS2524 /path/to/geant4-data
hpc/osc/finalize-lab-v2-5x5-surface-55mrad.sh
```

After downloading the archive, analyze all four surfaces with the two current
polished-front baseline maps plus the six new maps:

```bash
python3 test/OpNovice2/analyze_lab_v2_surface.py \
  --sample-set 5x5 \
  --baseline-scan-dir test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_refinement_5x5_polishedfrontpainted_5000events \
  --surface-scan-dir test/OpNovice2/scan_runs/lab_v2_realsetup/surface_comparison_5x5_55mrad_5000events \
  --out-dir test/OpNovice2/lab_run_v2/surface_analysis_5x5_55mrad_5000events \
  --divergence-mrad 55 \
  --events-per-point 5000 \
  --lab-relative-uncertainty 0.10

root -l -b -q \
  'test/OpNovice2/plot_lab_v2_surface.C("test/OpNovice2/lab_run_v2/surface_analysis_5x5_55mrad_5000events",55)'
```

The primary ranking is the equal-weight mean of each tile's all-point scaled
normalized RMSE. Chi-square, shared-scale, inside-only, and outside-only values
remain diagnostics.
