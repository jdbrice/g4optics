#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export PLAN_DIR="${PROJECT_ROOT}/hpc/osc/generated/lab_v2_realsetup/surface_comparison_75mrad_5000events"
export EXPECTED_PLANS=12
export EXPECTED_TASKS=258

exec "${SCRIPT_DIR}/submit-lab-v2-calibration.sh" "$@"
