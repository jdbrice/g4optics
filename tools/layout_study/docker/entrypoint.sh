#!/usr/bin/env bash
set -e -o pipefail
export GEANT4_DATA_DIR=/g4data
source /opt/geant4/bin/geant4.sh
export G4RUN_MANAGER_TYPE=Serial
exec "$@"
