# Lab v2 Stage A: 5x5 divergence refinement

## Purpose

Re-verify the beam-divergence preference using only the two established 5x5
lab scans. The 11.5x11.5 data are intentionally excluded while their lab
measurements are being repeated.

## Fixed configuration

- Surface: `polishedfrontpainted`
- Surface reflectivity: empirical EJ-510 CSV, represented as an optical-surface
  proxy rather than an explicit paint volume
- Tile material: EJ-200
- Source: bare Sr-90/Y-90 beta spectrum through GPS; source encapsulation and
  collimator transport are not modeled
- Beam profile: circular Gaussian, sigma = 5 mm
- New divergence candidates: 50, 55, 60, 65, and 70 mrad
- Reused high-statistics endpoints: 45 and 75 mrad
- Events: 5000 per scan point
- Samples: 50 x 50 x 16 mm and 50 x 50 x 4 mm
- SiPM: 2.4 x 2.4 x 0.5 mm, centered on the -Z face
- SiPM coupling: zero-gap proxy (`--optical-coupling none`)
- Beam-to-SiPM distance: 38 mm (`beam-z=30 mm` for 16 mm and `36 mm` for 4 mm)
- Slurm walltime: 2 hours per array task

## Scope and scoring

The directory contains 10 arrays with 15 point tasks each, for 150 new tasks.
The primary comparison will be the equal-weight mean normalized RMSE of the
two tiles with an independent scale fitted per tile. All-point chi2 and the
inside/outside regions remain diagnostics. This stage does not compare surface
presets and must not be interpreted as a four-surface study.
