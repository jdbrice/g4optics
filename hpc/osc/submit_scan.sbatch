#!/usr/bin/env bash
#SBATCH --job-name=g4optics-scan
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=4G
#SBATCH --array=1-1
#SBATCH --output=slurm-%x-%A_%a.out

# Add OSC-specific account or partition lines when needed, for example:
##SBATCH --account=YOUR_ACCOUNT
##SBATCH --partition=YOUR_PARTITION

set -euo pipefail

if [[ -n "${G4_PROJECT_ROOT:-}" ]]; then
  PROJECT_ROOT="$(cd "${G4_PROJECT_ROOT}" && pwd)"
elif [[ -n "${SLURM_SUBMIT_DIR:-}" && -f "${SLURM_SUBMIT_DIR}/hpc/osc/run_scan_apptainer.sh" ]]; then
  PROJECT_ROOT="$(cd "${SLURM_SUBMIT_DIR}" && pwd)"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
fi
SCRIPT_DIR="${PROJECT_ROOT}/hpc/osc"

cd "${PROJECT_ROOT}"

if [[ -z "${SCAN_RUN_ID_SUFFIX:-}" && -n "${SLURM_ARRAY_JOB_ID:-}" && -n "${SLURM_ARRAY_TASK_ID:-}" ]]; then
  export SCAN_RUN_ID_SUFFIX="job${SLURM_ARRAY_JOB_ID}_task${SLURM_ARRAY_TASK_ID}"
elif [[ -z "${SCAN_RUN_ID_SUFFIX:-}" && -n "${SLURM_JOB_ID:-}" ]]; then
  export SCAN_RUN_ID_SUFFIX="job${SLURM_JOB_ID}"
fi

if [[ $# -gt 0 ]]; then
  :
elif [[ -n "${SCAN_ARGS_FILE:-}" ]]; then
  args_file="${SCAN_ARGS_FILE}"
  if [[ "${args_file}" != /* ]]; then
    args_file="${PROJECT_ROOT}/${args_file}"
  fi
  if [[ ! -f "${args_file}" ]]; then
    echo "Missing SCAN_ARGS_FILE: ${args_file}" >&2
    exit 1
  fi

  task_id="${SLURM_ARRAY_TASK_ID:-1}"
  scan_line="$(
    awk -v n="${task_id}" '
      /^[[:space:]]*($|#)/ { next }
      { ++i; if (i == n) { print; exit } }
    ' "${args_file}"
  )"
  if [[ -z "${scan_line}" ]]; then
    echo "No scan args for array task ${task_id} in ${args_file}" >&2
    exit 1
  fi

  # SCAN_ARGS_FILE is a trusted local plan file so quoted arguments can be kept.
  eval "set -- ${scan_line}"
elif [[ $# -eq 0 ]]; then
  set -- full custom --events "${N_EVENTS:-1}" --source-mode gps \
    --tank-size "100 100 5 mm" \
    --x-min 0 --x-max 0 --y-min 0 --y-max 0 --step 5 --grid-unit mm
fi

echo "Running g4optics scan args: $*"
"${SCRIPT_DIR}/run_scan_apptainer.sh" "$@"
