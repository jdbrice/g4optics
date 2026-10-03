#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --array=0-239
set -euo pipefail
: "${LAYOUT_REPO:?exact PR checkout required}"
: "${LAYOUT_PYTHON:?absolute analysis Python required}"
: "${LAYOUT_CAMPAIGN:?frozen campaign directory required}"
: "${LAYOUT_MANIFEST_SHA:?frozen manifest SHA256 required}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$LAYOUT_PYTHON" "$LAYOUT_REPO/tools/layout_study/scan.py" worker \
  --manifest "$LAYOUT_CAMPAIGN/manifest.json" \
  --manifest-sha256 "$LAYOUT_MANIFEST_SHA" \
  --task-index "$SLURM_ARRAY_TASK_ID"
