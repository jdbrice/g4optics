#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLAN_DIR="hpc/osc/generated/lab_v2_realsetup/surface_comparison_75mrad_5000events"
OUTPUT_DIR="test/OpNovice2/scan_runs/lab_v2_realsetup/surface_comparison_75mrad_5000events"
BASELINE_DIR="test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_validation"
ARCHIVE_DIR="test/OpNovice2/lab_run_v2"
PRIMARY_MANIFEST="${PLAN_DIR}/submission-manifest.tsv"
RECOVERY_MANIFEST="${PLAN_DIR}/groundback-recovery-manifest.tsv"
SUCCESS_MANIFEST="${PLAN_DIR}/successful-submission-manifest.tsv"

cd "${PROJECT_ROOT}"

if [[ ! -d "${BASELINE_DIR}" ]]; then
  echo "Missing audited polishedfrontpainted baseline: ${BASELINE_DIR}" >&2
  exit 1
fi

MANIFEST="${PRIMARY_MANIFEST}"
if [[ -s "${RECOVERY_MANIFEST}" ]]; then
  python3 "${SCRIPT_DIR}/compose_lab_v2_surface_manifest.py" \
    --primary "${PRIMARY_MANIFEST}" \
    --recovery "${RECOVERY_MANIFEST}" \
    --output "${SUCCESS_MANIFEST}"
  MANIFEST="${SUCCESS_MANIFEST}"
fi

python3 "${SCRIPT_DIR}/finalize_lab_v2_calibration.py" \
  --manifest "${MANIFEST}" \
  --output-dir "${OUTPUT_DIR}" \
  --expected-plans 12 \
  --expected-tasks 258

FIRST_JOB_ID="$(tail -n 12 "${MANIFEST}" | awk -F '\t' 'NR == 1 { print $1 }')"
LAST_JOB_ID="$(tail -n 1 "${MANIFEST}" | cut -f1)"
ARCHIVE_NAME="lab-v2-surface-comparison-${FIRST_JOB_ID}-${LAST_JOB_ID}.tar.gz"

mkdir -p "${ARCHIVE_DIR}"
tar -czf "${ARCHIVE_DIR}/${ARCHIVE_NAME}" \
  "${OUTPUT_DIR}" \
  "${BASELINE_DIR}" \
  "${PLAN_DIR}"

echo "Archive: ${ARCHIVE_DIR}/${ARCHIVE_NAME}"
