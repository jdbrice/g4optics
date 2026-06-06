#!/usr/bin/env bash
set -euo pipefail

N_EVENTS=100
# The height of shooting beam. The unit is in cm, and need to be changed according to the thickness of tile
Z0="0.4"

mkdir -p scan_macros scan_outputs scan_logs

# Grid settings. The unit is in cm.
XS=(-4 0 4)
YS=(-4 0 4)

label_num() {
  local v="$1"
  if [[ "$v" == -* ]]; then
    echo "m${v#-}"
  else
    echo "p${v}"
  fi
}

for x in "${XS[@]}"; do
  for y in "${YS[@]}"; do
    lx=$(label_num "$x")
    ly=$(label_num "$y")
    tag="x${lx}_y${ly}"

    macro="scan_macros/sipm_scan_${tag}.mac"
    outfile="scan_outputs/sipm_scan_${tag}"
    log="scan_logs/sipm_scan_${tag}.log"

    # Start from your current tested SiPM macro, then replace only what changes.
    # Grammar: s|<original text>|<replaced text>
    sed \
      -e "s|/analysis/setFileName ej200_sipm_test|/analysis/setFileName ${outfile}|" \
      -e "s|/gun/position -6 4 0 cm|/gun/position ${x} ${y} ${Z0} cm|" \
      -e "s|/gun/direction 1 0 0|/gun/direction 0 0 -1|" \
      -e "s|/run/beamOn 100|/run/beamOn ${N_EVENTS}|" \
      ej200_sipm_test.mac > "${macro}"

    echo "Running ${tag}: x=${x} cm, y=${y} cm"
    ./build/OpNovice2 "${macro}" > "${log}" 2>&1
  done
done