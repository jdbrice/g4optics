#!/usr/bin/env bash
# Copy-safe Slurm entrypoint for the one-job successor-v5 R3 isolation probe.

set -euo pipefail

fail() {
  printf 'Cannot launch steel-module R3 container probe: %s\n' "$1" >&2
  exit 2
}

canonical_execution_name="steel-module-production-bc-s1-execution-v5"

[[ "$#" -eq 0 ]] || fail "positional arguments are forbidden"
[[ "${SLURM_JOB_ID:-}" =~ ^[1-9][0-9]*$ ]] || fail "invalid SLURM_JOB_ID"
[[ "${SLURM_JOB_NAME:-}" =~ ^g4sm-r3-[0-9a-f]{12}$ ]] || \
  fail "invalid SLURM_JOB_NAME"
[[ -n "${SLURMD_NODENAME:-}" ]] || fail "missing compute-node identity"
[[ -z "${SLURM_ARRAY_JOB_ID:-}" && -z "${SLURM_ARRAY_TASK_ID:-}" &&
   -z "${SLURM_ARRAY_TASK_COUNT:-}" && -z "${SLURM_ARRAY_TASK_MIN:-}" &&
   -z "${SLURM_ARRAY_TASK_MAX:-}" && -z "${SLURM_ARRAY_TASK_STEP:-}" ]] || \
  fail "R3 probe must be a non-array job"

execution_root="$(pwd -P)"
case "${execution_root}" in
  /*/campaigns/"${canonical_execution_name}") ;;
  *) fail "job working directory is not the canonical execution-v5 path" ;;
esac
[[ ! -L "${execution_root}" && -d "${execution_root}" ]] || \
  fail "job working directory must be a regular directory"

suffix="/campaigns/${canonical_execution_name}"
work_root="${execution_root%"${suffix}"}"
[[ -n "${work_root}" && "${work_root}" != "${execution_root}" &&
   "${work_root}" == */g4optics-rn ]] || \
  fail "cannot derive the canonical work root"
home_root="${work_root%/g4optics-rn}"
[[ -n "${home_root}" && "${home_root}" = /* && -d "${home_root}" ]] || \
  fail "cannot derive the canonical home root"

control_root="${execution_root}/sources/control"
managed_execution="${execution_root}/managed_execution.json"
runner="${control_root}/hpc/osc/run_steel_module_production_r3_container_probe.py"
r3_root="${work_root}/evidence/steel-module-production-r3"
raw_parent="${r3_root}/raw"
workspace="${raw_parent}/${SLURM_JOB_NAME}"

[[ -d "${control_root}" && ! -L "${control_root}" ]] || \
  fail "missing frozen control-plane root"
[[ -f "${managed_execution}" && ! -L "${managed_execution}" ]] || \
  fail "missing managed execution manifest"
[[ -f "${runner}" && ! -L "${runner}" ]] || \
  fail "missing frozen R3 Python runner"
[[ -d "${raw_parent}" && ! -L "${raw_parent}" ]] || \
  fail "missing canonical R3 raw parent"
[[ ! -e "${workspace}" && ! -L "${workspace}" ]] || \
  fail "canonical R3 raw workspace already exists"

python_bin="$(type -P python3 || true)"
[[ "${python_bin}" = /* && -x "${python_bin}" ]] || fail "python3 is unavailable"
parser_path="${python_bin%/*}:/usr/bin:/bin"
execution_hash="$(
  /usr/bin/env -i \
    HOME="${home_root}" \
    LANG=C \
    PATH="${parser_path}" \
    "${python_bin}" -I -c \
    'import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream).get("execution_hash")
if not isinstance(value, str):
    raise SystemExit(2)
print(value)' \
    "${managed_execution}"
)" || fail "managed execution manifest cannot be parsed"
[[ "${execution_hash}" =~ ^[0-9a-f]{64}$ ]] || \
  fail "managed execution hash is missing or ambiguous"
expected_job_name="g4sm-r3-${execution_hash:0:12}"
[[ "${SLURM_JOB_NAME}" == "${expected_job_name}" ]] || \
  fail "SLURM_JOB_NAME does not match the managed execution hash"

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
  SLURMD_NODENAME="${SLURMD_NODENAME}" \
  "${python_bin}" "${runner}" \
  --execution-dir "${execution_root}" \
  --workspace "${workspace}"
