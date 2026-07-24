#!/usr/bin/env bash
# Shared Geant4 dataset discovery for OSC Apptainer entrypoints.

configure_geant4_data_env() {
  local print_environment="${1:-0}"

  set_g4data_env() {
    local var_name="$1"
    local dir_pattern="$2"
    if [[ -n "${!var_name:-}" && -d "${!var_name}" ]]; then
      return
    fi

    local candidate
    for candidate in \
      /opt/geant4-data/${dir_pattern} \
      /opt/geant4/data/${dir_pattern} \
      /opt/geant4/share/Geant4/data/${dir_pattern} \
      /opt/geant4/share/Geant4-*/data/${dir_pattern} \
      /usr/local/share/Geant4/data/${dir_pattern} \
      /usr/local/share/Geant4-*/data/${dir_pattern} \
      /usr/share/Geant4/data/${dir_pattern} \
      /usr/share/Geant4-*/data/${dir_pattern}; do
      if [[ -d "${candidate}" ]]; then
        export "${var_name}=${candidate}"
        return
      fi
    done

    local root
    for root in /opt/geant4-data /opt/geant4 /usr/local/share /usr/share; do
      if [[ -d "${root}" ]]; then
        candidate="$(find "${root}" -type d -name "${dir_pattern}" -print -quit 2>/dev/null || true)"
        if [[ -n "${candidate}" ]]; then
          export "${var_name}=${candidate}"
          return
        fi
      fi
    done
  }

  set_g4data_env G4ENSDFSTATEDATA "G4ENSDFSTATE*"
  set_g4data_env G4NEUTRONHPDATA "G4NDL*"
  set_g4data_env G4LEDATA "G4EMLOW*"
  set_g4data_env G4LEVELGAMMADATA "PhotonEvaporation*"
  set_g4data_env G4RADIOACTIVEDATA "RadioactiveDecay*"
  set_g4data_env G4PARTICLEXSDATA "G4PARTICLEXS*"
  set_g4data_env G4PIIDATA "G4PII*"
  set_g4data_env G4REALSURFACEDATA "RealSurface*"
  set_g4data_env G4SAIDXSDATA "G4SAIDDATA*"
  set_g4data_env G4ABLADATA "G4ABLA*"
  set_g4data_env G4INCLDATA "G4INCL*"
  set_g4data_env G4CHANNELINGDATA "G4CHANNELING*"

  if [[ "${print_environment}" == "1" ]]; then
    env | sort | grep -E "^G4.*DATA=" || true
  fi

  if [[ -z "${G4ENSDFSTATEDATA:-}" || ! -d "${G4ENSDFSTATEDATA}" ]]; then
    echo "Missing Geant4 data directory for G4ENSDFSTATEDATA inside the Apptainer image." >&2
    echo "Set G4_DATA_ROOT to a host directory containing Geant4 datasets, or inspect the image data directories." >&2
    return 1
  fi
}
