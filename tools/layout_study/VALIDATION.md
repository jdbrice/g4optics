# Reproducibility checks and current status

This is a draft research implementation. The fixed first 24,000-event scan specified
in [FIRST_SCAN.md](FIRST_SCAN.md) is complete; [results and reproduction evidence](results/first-scan-d0defcdc/README.md) are available for scientific review.

## Portable local check

The documented Docker route was exercised from a source archive with no `.git`,
with a newly installed public Geant4 11.4.2 runtime and a fresh data volume.
All twelve official datasets were downloaded and archive-checked; 43,526 unpacked
files were SHA-256 inventoried. Application compilation, tests and four ten-event
4 mm runs then succeeded with networking disabled, datasets read-only, and a
non-root user. The cold-check snapshot passed 68 tests (the final complete suite, including the runtime builder and scientific workflow,
then passed 77 tests in the same offline Docker environment).

All ROOT physics branches and histograms, aggregate CSV values and log bytes
matched the previously verified Mac sample for the same seeds. ROOT serialization
metadata were excluded. [The small reference summary](reference/mac-docker-t04.json)
records versions, conditions and counts. It is an engineering example, not a
layout ranking or a cross-architecture exact-output requirement.

The first cold check exposed an unreadable root-owned dataset checksum receipt.
The installer now makes its public checksum manifest readable by the non-root
runtime; the full offline check above passed after that fix. Physics files were
not changed.

## OSC background qualification

The C++ files packaged in this PR are byte-identical to the model previously
qualified on Geant4 11.4.2 x86_64 Serial at OSC: 24 configurations, 25 events each,
all process, geometry and event audits passed, and all 42 then-existing tests
passed with Python 3.9.21 / NumPy 2.0.2 / uproot 5.6.9. A downloaded independent
re-audit checked 611 archived files and all 600 events. The measured 25-event
runs took 56–311 seconds; the complete batch-step memory maximum was about
252 MiB. Those samples are excluded from the scientific scan.

## Exact PR commit and first scientific sample

Commit `d0defcdc3bc7513b4feccdaa64a4512ea621c797` was independently built on OSC
using the pinned public Alma9/amd64 image. All 77 then-existing tests and four
10-event layout qualifications passed; a separate 25-event back-four comparison
matched the saved historical physics fields and summaries exactly. The new ELF
checksum also matched the earlier qualified executable.

Full data preflight found 12 old installation-receipt files in the existing data
directory; all 43,526 physical data files matched the independent fresh-install
reference. Those files were copied into a new directory and passed the unchanged
strict preflight. The old directory and failed preflight were preserved, and no
simulation events ran in that failed preflight.

The registered 240-task, 24,000-event scan and collection/analysis completed
normally. Independent raw-output, bootstrap, provenance and geometry checks
passed. The [completed sample](results/first-scan-d0defcdc/README.md) records exact
identities, figures, intervals, execution resources and interpretation limits.
The simulation and statistical analysis remain bound to `d0defcdc`; subsequent
plotting and documentation are separate from that frozen execution.

The inherited run-header text prints the nominal gun energy of 1 MeV. The actual
configured neutron source and audited event records contain 1,000 MeV kinetic
energy; the frozen input and event record define this scan's source.

## Limits

The inherited generated-light denominator and first-boundary diagnostic are
preserved. Log checks are not a complete optical birth/fate or all-trajectory
NoRINDEX audit. The SiPM response is collected optical photons, without PDE or
readout electronics. Functional checks and timing do not establish scientific
precision, experimental material agreement or a preferred detector layout.

[engineering-seeds.json](reference/engineering-seeds.json) lists seed values used
by the recorded smoke/qualification/benchmark runs, including the bundled local
example. Extend this list if you use additional engineering seeds before a new
independent scan. Do not mix these small samples into the first scientific scan.
