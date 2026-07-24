#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLAN_DIR="hpc/osc/generated/lab_v2_realsetup/surface_comparison_5x5_55mrad_5000events"
OUTPUT_DIR="test/OpNovice2/scan_runs/lab_v2_realsetup/surface_comparison_5x5_55mrad_5000events"
BASELINE_ROOT="test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_refinement_5x5_polishedfrontpainted_5000events"
BASELINE_16="${BASELINE_ROOT}/lab_v2_5x5x16_polishedfrontpainted_sigma5mm_55mrad_5000events"
BASELINE_4="${BASELINE_ROOT}/lab_v2_5x5x4_polishedfrontpainted_sigma5mm_55mrad_5000events"
ARCHIVE_DIR="test/OpNovice2/lab_run_v2"
MANIFEST="${PLAN_DIR}/submission-manifest.tsv"

cd "${PROJECT_ROOT}"

for baseline in "${BASELINE_16}" "${BASELINE_4}"; do
  if [[ ! -f "${baseline}/efficiency_map.csv" ]]; then
    echo "Missing audited polishedfrontpainted baseline: ${baseline}/efficiency_map.csv" >&2
    exit 1
  fi
done

python3 "${SCRIPT_DIR}/finalize_lab_v2_calibration.py" \
  --manifest "${MANIFEST}" \
  --output-dir "${OUTPUT_DIR}" \
  --expected-plans 6 \
  --expected-tasks 90

FIRST_JOB_ID="$(tail -n 6 "${MANIFEST}" | awk -F '\t' 'NR == 1 { print $1 }')"
LAST_JOB_ID="$(tail -n 1 "${MANIFEST}" | cut -f1)"
ARCHIVE_NAME="lab-v2-5x5-surface-55mrad-${FIRST_JOB_ID}-${LAST_JOB_ID}.tar.gz"

mkdir -p "${ARCHIVE_DIR}"
tar -czf "${ARCHIVE_DIR}/${ARCHIVE_NAME}" \
  "${OUTPUT_DIR}" \
  "${BASELINE_16}" \
  "${BASELINE_4}" \
  "${PLAN_DIR}"

echo "Archive: ${ARCHIVE_DIR}/${ARCHIVE_NAME}"
