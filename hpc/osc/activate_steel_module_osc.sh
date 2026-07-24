#!/usr/bin/env bash
# Source this file to enter the steel-module OSC analysis environment.

_steel_module_env_is_sourced=0
if [[ -n "${BASH_VERSION:-}" && "${BASH_SOURCE[0]}" != "$0" ]]; then
  _steel_module_env_is_sourced=1
elif [[ -n "${ZSH_VERSION:-}" && "${ZSH_EVAL_CONTEXT:-}" == *:file ]]; then
  _steel_module_env_is_sourced=1
fi

if [[ "${_steel_module_env_is_sourced}" != "1" ]]; then
  echo "This helper must be sourced so its environment persists:" >&2
  echo "  source hpc/osc/activate_steel_module_osc.sh" >&2
  exit 2
fi
unset _steel_module_env_is_sourced

_activate_steel_module_osc() {
  local repo="${STEEL_MODULE_REPO:-${HOME}/projects/g4optics}"
  local work="${STEEL_MODULE_WORK:-${HOME}/g4optics-rn}"
  local campaign="${STEEL_MODULE_CAMPAIGN:-${work}/campaigns/steel-module-convergence-pilot-cfd7d974}"
  local data_root="${STEEL_MODULE_DATA_ROOT:-${HOME}/geant4-data/11.4.2}"
  local analysis_venv="${STEEL_MODULE_ANALYSIS_VENV:-${work}/analysis-venv}"

  if [[ ! -d "${repo}" ]]; then
    echo "Missing OSC source repository: ${repo}" >&2
    return 1
  fi
  if ! git -C "${repo}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Not a Git worktree: ${repo}" >&2
    return 1
  fi
  if [[ ! -d "${work}" ]]; then
    echo "Missing steel-module work root: ${work}" >&2
    return 1
  fi
  if [[ ! -f "${analysis_venv}/bin/activate" ]]; then
    echo "Missing analysis environment: ${analysis_venv}/bin/activate" >&2
    return 1
  fi
  if [[ ! -f "${campaign}/campaign.json" ]]; then
    echo "Missing sealed pilot campaign: ${campaign}/campaign.json" >&2
    return 1
  fi
  if [[ ! -f "${campaign}/finalized/SHA256SUMS" ]]; then
    echo "Missing sealed pilot checksums: ${campaign}/finalized/SHA256SUMS" >&2
    return 1
  fi
  if [[ ! -d "${data_root}" ]]; then
    echo "Missing Geant4 data root: ${data_root}" >&2
    return 1
  fi

  export REPO="${repo}"
  export WORK="${work}"
  export CAMPAIGN="${campaign}"
  export DATA_ROOT="${data_root}"
  export G4_DATA_ROOT="${data_root}"
  export ANALYSIS_VENV="${analysis_venv}"

  # shellcheck disable=SC1091
  . "${ANALYSIS_VENV}/bin/activate" || return 1
  cd "${REPO}" || return 1

  echo "Steel-module OSC environment ready."
  echo "  REPO=${REPO}"
  echo "  WORK=${WORK}"
  echo "  CAMPAIGN=${CAMPAIGN}"
  echo "  G4_DATA_ROOT=${G4_DATA_ROOT}"
  echo "  Python=$(command -v python3)"
  echo "  HEAD=$(git rev-parse --short HEAD)"
}

_activate_steel_module_osc
_steel_module_env_status=$?
unset -f _activate_steel_module_osc
if [[ "${_steel_module_env_status}" != "0" ]]; then
  unset _steel_module_env_status
  return 1
fi
unset _steel_module_env_status
