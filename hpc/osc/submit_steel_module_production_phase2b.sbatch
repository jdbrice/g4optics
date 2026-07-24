#!/usr/bin/env bash
#SBATCH --time=01:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --array=1-1

set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "Expected execution root, attempt ID, and intent SHA-256." >&2
  exit 2
fi
execution_root="$1"
attempt_id="$2"
intent_sha256="$3"
if [[ ! "${execution_root}" = /* ||
      ! "${attempt_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$ ||
      ! "${intent_sha256}" =~ ^[0-9a-f]{64}$ ||
      ! "${SLURM_ARRAY_JOB_ID:-}" =~ ^[1-9][0-9]*$ ||
      ! "${SLURM_JOB_ID:-}" =~ ^[1-9][0-9]*$ ||
      ! "${SLURM_ARRAY_TASK_ID:-}" =~ ^[1-9][0-9]*$ ||
      ! "${SLURM_ARRAY_TASK_COUNT:-}" =~ ^[1-9][0-9]*$ ||
      ! "${SLURM_ARRAY_TASK_MIN:-}" =~ ^[1-9][0-9]*$ ||
      ! "${SLURM_ARRAY_TASK_MAX:-}" =~ ^[1-9][0-9]*$ ||
      ! "${SLURM_ARRAY_TASK_STEP:-}" =~ ^[1-9][0-9]*$ ||
      -z "${SLURM_JOB_NAME:-}" || -z "${SLURM_SUBMIT_DIR:-}" ]]; then
  echo "Invalid managed worker identity." >&2
  exit 2
fi

control_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
if [[ "${control_root}" != "${execution_root}/sources/control" ]]; then
  echo "Batch script is not running from the frozen control-plane source." >&2
  exit 2
fi

exec python3 "${control_root}/hpc/osc/run_steel_module_production_phase2b_task.py" \
  --execution-dir "${execution_root}" \
  --attempt-id "${attempt_id}" \
  --intent-sha256 "${intent_sha256}" \
  --array-index "${SLURM_ARRAY_TASK_ID}"
