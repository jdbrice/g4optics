#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${1:-${G4_DATA_ROOT:-${HOME}/geant4-data/11.4.2}}"
DATASET_URL="${G4_DATASET_URL:-https://cern.ch/geant4-data/datasets}"

mkdir -p "${DATA_ROOT}"

download_file() {
  local url="$1"
  local output="$2"

  if [[ -f "${output}" ]]; then
    return
  fi

  if command -v curl >/dev/null 2>&1; then
    curl -L -f -o "${output}" "${url}"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "${output}" "${url}"
  else
    echo "Missing curl or wget; cannot download ${url}" >&2
    exit 1
  fi
}

check_md5() {
  local file="$1"
  local expected="$2"
  local actual

  if command -v md5sum >/dev/null 2>&1; then
    echo "${expected}  ${file}" | md5sum -c -
  elif command -v openssl >/dev/null 2>&1; then
    actual="$(openssl dgst -md5 -r "${file}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
      echo "MD5 mismatch for ${file}: expected ${expected}, got ${actual}" >&2
      exit 1
    fi
  else
    echo "Warning: md5sum/openssl not found; skipping checksum for ${file}" >&2
  fi
}

install_dataset() {
  local archive="$1"
  local directory="$2"
  local md5="$3"
  local archive_path="${DATA_ROOT}/${archive}"

  if [[ -d "${DATA_ROOT}/${directory}" ]]; then
    echo "Installed: ${directory}"
    return
  fi

  echo "Downloading: ${archive}"
  download_file "${DATASET_URL}/${archive}" "${archive_path}"
  check_md5 "${archive_path}" "${md5}"

  echo "Extracting: ${archive}"
  tar -xzf "${archive_path}" -C "${DATA_ROOT}"
}

install_dataset "G4NDL.4.7.1.tar.gz" "G4NDL4.7.1" "54f0ed3995856f02433d42ec96d70bc6"
install_dataset "G4EMLOW.8.6.1.tar.gz" "G4EMLOW8.6.1" "9db67a37acc3eae9b0ffdace41a23b74"
install_dataset "G4PhotonEvaporation.6.1.tar.gz" "PhotonEvaporation6.1" "92d68b937cdad0fd49892a66878863de"
install_dataset "G4RadioactiveDecay.6.1.2.tar.gz" "RadioactiveDecay6.1.2" "20d494f73d4bddabd7fab5c06a58895c"
install_dataset "G4PARTICLEXS.4.1.tar.gz" "G4PARTICLEXS4.1" "878252a464ba6b38f085741840f053e6"
install_dataset "G4PII.1.3.tar.gz" "G4PII1.3" "05f2471dbcdf1a2b17cbff84e8e83b37"
install_dataset "G4RealSurface.2.2.tar.gz" "RealSurface2.2" "ea8f1cfa8d8aafd64b71fb30b3e8a6d9"
install_dataset "G4SAIDDATA.2.0.tar.gz" "G4SAIDDATA2.0" "d5d4e9541120c274aeed038c621d39da"
install_dataset "G4ABLA.3.3.tar.gz" "G4ABLA3.3" "b25d093339e1e4532e31038653580ca6"
install_dataset "G4INCL.1.2.tar.gz" "G4INCL1.2" "0a76df936839bb557dae7254117eb58e"
install_dataset "G4ENSDFSTATE.3.0.tar.gz" "G4ENSDFSTATE3.0" "c500728534ce3e9fb2fefa0112eb3a74"
install_dataset "G4CHANNELING.1.0.tar.gz" "G4CHANNELING1.0" "b2f692ec7109418c6354ea1ecbc62da7"

echo
echo "Geant4 datasets are ready in: ${DATA_ROOT}"
echo "Run scans with:"
echo "  G4_DATA_ROOT=${DATA_ROOT} ./hpc/osc/run_scan_apptainer.sh"
