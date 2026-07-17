#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export PLAN_DIR="${PROJECT_ROOT}/hpc/osc/generated/lab_v2_realsetup/divergence_validation"
export EXPECTED_PLANS=8
export EXPECTED_TASKS=172

exec "${SCRIPT_DIR}/submit-lab-v2-calibration.sh" "$@"
