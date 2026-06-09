#!/usr/bin/env bash
set -euo pipefail

N_EVENTS=100

# Near-field scan around bottom-center SiPM.
# Units are mm to avoid decimal coordinates and decimal file names.
Z0_MM="4"

mkdir -p scan_macros scan_outputs scan_logs

# Grid settings in mm.
# For a 3 mm x 3 mm SiPM, step = 3 mm.
# Only the center point (0,0) is directly above the SiPM footprint.
XS_MM=(-6 -3 0 3 6)
YS_MM=(-6 -3 0 3 6)

label_int() {
  local v="$1"
  if [[ "$v" == -* ]]; then
    echo "m${v#-}"
  else
    echo "p${v}"
  fi
}

for x_mm in "${XS_MM[@]}"; do
  for y_mm in "${YS_MM[@]}"; do
    lx=$(label_int "$x_mm")
    ly=$(label_int "$y_mm")
    tag="x${lx}mm_y${ly}mm"

    macro="scan_macros/sipm_scan_${tag}.mac"
    outfile="scan_outputs/sipm_scan_${tag}"
    log="scan_logs/sipm_scan_${tag}.log"

    sed \
      -e "s|/analysis/setFileName ej200_sipm_test|/analysis/setFileName ${outfile}|" \
      -e "s|/gun/position -6 4 0 cm|/gun/position ${x_mm} ${y_mm} ${Z0_MM} mm|" \
      -e "s|/gun/direction 1 0 0|/gun/direction 0 0 -1|" \
      -e "s|/run/beamOn 100|/run/beamOn ${N_EVENTS}|" \
      ej200_sipm_test.mac > "${macro}"

    echo "Running ${tag}: x=${x_mm} mm, y=${y_mm} mm"
    ./build/OpNovice2 "${macro}" > "${log}" 2>&1
  done
done