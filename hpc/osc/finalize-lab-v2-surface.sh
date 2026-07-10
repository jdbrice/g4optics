#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLAN_DIR="hpc/osc/generated/lab_v2_realsetup/surface_comparison_75mrad_5000events"
OUTPUT_DIR="test/OpNovice2/scan_runs/lab_v2_realsetup/surface_comparison_75mrad_5000events"
BASELINE_DIR="test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_validation"
ARCHIVE_DIR="test/OpNovice2/lab_run_v2"

cd "${PROJECT_ROOT}"

if [[ ! -d "${BASELINE_DIR}" ]]; then
  echo "Missing audited polishedfrontpainted baseline: ${BASELINE_DIR}" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/finalize_lab_v2_calibration.py" \
  --manifest "${PLAN_DIR}/submission-manifest.tsv" \
  --output-dir "${OUTPUT_DIR}" \
  --expected-plans 12 \
  --expected-tasks 258

FIRST_JOB_ID="$(tail -n 12 "${PLAN_DIR}/submission-manifest.tsv" | awk -F '\t' 'NR == 1 { print $1 }')"
LAST_JOB_ID="$(tail -n 1 "${PLAN_DIR}/submission-manifest.tsv" | cut -f1)"
ARCHIVE_NAME="lab-v2-surface-comparison-${FIRST_JOB_ID}-${LAST_JOB_ID}.tar.gz"

mkdir -p "${ARCHIVE_DIR}"
tar -czf "${ARCHIVE_DIR}/${ARCHIVE_NAME}" \
  "${OUTPUT_DIR}" \
  "${BASELINE_DIR}" \
  "${PLAN_DIR}"

echo "Archive: ${ARCHIVE_DIR}/${ARCHIVE_NAME}"
