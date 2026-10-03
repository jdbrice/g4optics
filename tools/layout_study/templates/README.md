# Packaged baseline input

`baseline_template.mac` is the exact 4 mm `steel-module-stack-v1` macro produced
by the historical runner at `a2d05dfe1a6c76df2acd072d7fab8cff366a02f6`. It was
captured using the verified input generator from
`404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5`, with one dry-run event and seeds
`71004 72004`. Only the temporary `/analysis/setFileName` output path was
normalized to `baseline_result`, exactly as that generator already did.

`baseline_template.provenance.json` preserves that generator's original
provenance object: source commit and tree, all five input-source SHA256 values,
the physics entrypoint SHA256, seeds, event count, and template SHA256. Those
historical source files are not needed to generate new layout inputs.

`prepare.py` independently pins both the template and provenance file hashes.
It verifies their bytes before generating anything. The historical Git IDs
document origin; preparation works from a source ZIP, shallow checkout, or
copied directory without Git, Bash, Geant4, or a previous simulation output.
Changing physics requires an intentional new template/provenance revision and
review, not an automatic regeneration from whichever checkout is available.

`macro_reference.json` is a regression oracle captured from the same verified
generator. It records the 24 generated macro SHA256 values for six thicknesses,
four layouts, two events, and seeds `20261002 20261003`. Tests compare current
output bytes against those values, including after copying the package to a
directory with no `.git` or historical source files.

These files are reproducible input fixtures, not statistical simulation results.
