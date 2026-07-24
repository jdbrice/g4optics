#!/usr/bin/env bash
# Frozen copy-safe entrypoint for the single Phase-2C no-Geant4 preflight.

set -euo pipefail

fail() {
  printf 'Cannot launch steel-module Phase-2C preflight: %s\n' "$1" >&2
  exit 2
}

[[ "$#" -eq 0 ]] || fail "positional arguments are forbidden"
[[ "${SLURM_JOB_ID:-}" =~ ^[1-9][0-9]*$ ]] || fail "invalid SLURM_JOB_ID"
[[ "${SLURM_JOB_NAME:-}" =~ ^g4sm-p2c-[0-9a-f]{12}$ ]] || \
  fail "invalid SLURM_JOB_NAME"
[[ -n "${SLURMD_NODENAME:-}" ]] || fail "missing compute-node identity"
[[ -z "${SLURM_ARRAY_JOB_ID:-}" && -z "${SLURM_ARRAY_TASK_ID:-}" &&
   -z "${SLURM_ARRAY_TASK_COUNT:-}" && -z "${SLURM_ARRAY_TASK_MIN:-}" &&
   -z "${SLURM_ARRAY_TASK_MAX:-}" && -z "${SLURM_ARRAY_TASK_STEP:-}" ]] || \
  fail "Phase-2C preflight must be a non-array job"

execution_root="$(pwd -P)"
execution_name="${execution_root##*/}"
[[ "${execution_name}" =~ ^steel-module-production-bc-s1-preflight-v6(-retry-(0[1-9]|[1-9][0-9])-[0-9a-f]{12})?$ ]] || \
  fail "working directory is not a canonical preflight-v6 path"
[[ "${execution_root}" == */campaigns/"${execution_name}" ]] || \
  fail "working directory is not under the canonical campaigns root"
[[ ! -L "${execution_root}" && -d "${execution_root}" ]] || \
  fail "working directory must be a regular directory"

suffix="/campaigns/${execution_name}"
work_root="${execution_root%"${suffix}"}"
[[ "${work_root}" == */g4optics-rn && "${work_root}" != "${execution_root}" ]] || \
  fail "cannot derive canonical work root"
home_root="${work_root%/g4optics-rn}"
repo_root="${home_root}/projects/g4optics"
[[ -d "${repo_root}" && ! -L "${repo_root}" ]] || \
  fail "canonical repository root is unavailable"

control_root="${execution_root}/sources/control"
manifest="${execution_root}/preflight_twin.json"
runner="${control_root}/hpc/osc/run_steel_module_production_phase2c_preflight.py"
[[ -d "${control_root}" && ! -L "${control_root}" ]] || \
  fail "frozen control source is unavailable"
[[ -f "${manifest}" && ! -L "${manifest}" ]] || \
  fail "preflight twin manifest is unavailable"
[[ -f "${runner}" && ! -L "${runner}" ]] || \
  fail "frozen Phase-2C Python runner is unavailable"

python_bin="$(type -P python3 || true)"
[[ "${python_bin}" = /* && -x "${python_bin}" ]] || fail "python3 is unavailable"
parser_path="${python_bin%/*}:/usr/bin:/bin"
twin_hash="$(
  /usr/bin/env -i \
    HOME="${home_root}" \
    LANG=C \
    PATH="${parser_path}" \
    "${python_bin}" -I -c \
    'import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream).get("twin_hash")
if not isinstance(value, str):
    raise SystemExit(2)
print(value)' \
    "${manifest}"
)" || fail "preflight twin manifest cannot be parsed"
[[ "${twin_hash}" =~ ^[0-9a-f]{64}$ ]] || fail "preflight twin hash is invalid"
[[ "${SLURM_JOB_NAME}" == "g4sm-p2c-${twin_hash:0:12}" ]] || \
  fail "SLURM_JOB_NAME does not match the preflight twin"

apptainer_bin="$(type -P apptainer || true)"
[[ "${apptainer_bin}" = /* && -x "${apptainer_bin}" ]] || \
  fail "apptainer is unavailable"
safe_path="${python_bin%/*}:${apptainer_bin%/*}:/usr/bin:/bin"

cd -- "${execution_root}"
exec /usr/bin/env -i \
  HOME="${home_root}" \
  LANG=C \
  PATH="${safe_path}" \
  SLURM_JOB_ID="${SLURM_JOB_ID}" \
  SLURM_JOB_NAME="${SLURM_JOB_NAME}" \
  SLURM_JOB_ACCOUNT="${SLURM_JOB_ACCOUNT:-pas2524}" \
  SLURMD_NODENAME="${SLURMD_NODENAME}" \
  "${python_bin}" "${runner}" \
  --repo-root "${repo_root}" \
  --execution-dir "${execution_root}"
