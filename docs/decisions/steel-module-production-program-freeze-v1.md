# Steel Module Production Program — Phase-1 Freeze and BC-S1 Phase-2A Evidence

Status: formal Phase-1 production program frozen and runtime-verified; formal
Phase-2A managed `BC-S1` plan materialized and read-only verified on OSC;
non-submittable; Phase-2B managed submission and downstream evidence tooling
implemented for implementation-commit validation, with the formal OSC
readiness lock still pending

Study preset: `steel-module-scan-v1`

Phase-1 frozen and verified: 2026-07-17

Phase-2A materialized and verified: 2026-07-18

## Purpose and authority

This record indexes the formal OSC Phase-1 production-program artifact created
from the accepted SMS-016 through SMS-019 policy. It records the program
identity and the external checksum anchors needed by later managed-child
tooling. It does not replace the checksum-bound OSC directory, the sealed
pilot, or the policy documents.

The tracked machine-readable lock consumed by later tooling is
`hpc/osc/configurations/steel-module-production-program-v1.lock.json`. Runtime
CLI arguments must not be allowed to substitute a different expected program
identity for this reviewed lock.

The authoritative artifact is:

`/users/PAS2524/anolddriver66/g4optics-rn/campaigns/steel-module-production-program-cbc03814`

The directory suffix is a storage label. The program ID, semantic hash, and
external file digests below are the authoritative identity.

## Program identity

| Item | Frozen value |
| --- | --- |
| Schema | `steel-module-production-program-v1` |
| Policy | `steel-module-production-sms016-019-v1` |
| Program ID | `sm-v1-production-program-7c1cafa00829` |
| Program semantic hash | `7c1cafa00829cefc70b556ef82d5988c1ca688a6941f5deab8dfab9255cdbab2` |
| Parent plan hash | `6ac71a6dbc234e8fb4d93af026f0f5d552e0f217c78169585a89e9eb67f36a6a` |
| Authorization graph hash | `758b09e5703adc9f0fecb5511fee2583ec39b7607fb61fb32a0cc6100d343a40` |
| Program seed | `20260717` |
| Program manifest SHA-256 | `628d483efabc32a007e14de029e169f4298f9e379dc8d7903655dc19ca8c7526` |
| Root `SHA256SUMS` SHA-256 | `07f410086c50bc957292a109b7955fed5d2ea2b10912c63114b7d258fc841020` |
| Accepted statistical evidence | `true` for the Phase-1 plan and provenance |
| Submittable | `false` |

The semantic hash identifies the policy-bound program. The manifest digest is
the byte-level digest of `production_program.json`; the `SHA256SUMS` digest is
an external anchor for the program tree's checksum manifest. These three
hashes have different roles and must not be substituted for one another.

## Frozen allocation

| Child | Tasks | Events | Current authorization state |
| --- | ---: | ---: | --- |
| `FIXED` | `592` | `148,000` | locked |
| `BC-S1` | `32` | `8,000` | formally materialized and check-only verified; not submittable |
| `BC-S2` | `48` | `12,000` | locked |
| `BC-S3` | `80` | `20,000` | locked |
| `BC-S4` | `162` | `40,500` | locked; `continue` is forbidden at this ceiling |
| **Parent** | **`914`** | **`228,500`** | never submitted |

Every task contains `250` events. The parent reserved `1,828` globally unique
production seed integers. The formal loader verified that they are disjoint
from all `240` unique seed integers in the accepted pilot exclusion.

The child plan hashes, task-set hashes, reserved materialized campaign IDs,
child manifest digests, parent task/seed hashes, and creation timestamp remain
recorded in the checksum-bound `production_program.json`. Later tooling must
read and verify those values from the formal artifact rather than transcribing
or recomputing a second authority here.

## Program-source and runtime provenance

| Item | Frozen value |
| --- | --- |
| Program-source commit | `cbc03814e80e8d0742a3bfb17921c118c6ff1585` |
| Program-source tree | `b236ed2643c814336b08b293f16507b6f3b39dc6` |
| Branch | `exp/realistic-detector` |
| Checkout | clean |
| Runtime mode | `osc-production` |
| Geant4 | `11.4.2` |
| Runtime environment identity | `6984ac6b9a4b46bc958ca233cf6cddaa02d5b9213cff387f27363536a0f1f709` |
| Container image | `/users/PAS2524/anolddriver66/projects/g4optics/geant4.sif` |
| Container SHA-256 | `6a777120a8ad8ae4959580e5c1e9eaa4dc2a837a95cbfca533f2f07d71d54962` |
| Geant4 data manifest | `/users/PAS2524/anolddriver66/g4optics-rn/environment/71e5979a/g4-data-manifest.sha256` |
| Data-manifest SHA-256 | `ef94e0915e1fffd0c24923dec161252da608400f5572f088bac8d5ce77acf807` |
| Frozen executable | `/users/PAS2524/anolddriver66/g4optics-rn/builds/candidate-88f15eac/OpNovice2` |
| Executable SHA-256 | `adbcbc2facf4bb39b4671c7d47a8f9caf9e4f691b9a28a6d5e0d79d354b4cf41` |
| Executable build-source commit | `88f15eace17137475310913d585a96908a493c72` |
| Executable build-source tree | `1d3f2b60b1b6230d3209e7a9ab661ac304f4f458` |
| Build-provenance file | `/users/PAS2524/anolddriver66/g4optics-rn/environment/80ed7e59/build-environment.txt` |

The build-provenance file digest is recorded inside the formal program
manifest and bound by the semantic and manifest hashes above.

## Sealed-pilot exclusion

| Item | Accepted value |
| --- | --- |
| Pilot campaign | `sm-v1-convergence-pilot-4e9ef8618948` |
| Pilot directory | `/users/PAS2524/anolddriver66/g4optics-rn/campaigns/steel-module-convergence-pilot-cfd7d974` |
| Pilot plan hash | `4e9ef861894846347cee7c2422383e013f774c74120fe05f5d1e52a980d18413` |
| Pilot source commit | `cfd7d974af2c5f40f7d76948172fc87ee70bfa3c` |
| Pilot exclusion hash | `262cb224eab2b4db760a8f41b0ab03bdb69fa9ea57e2ce4c1e97a16e206ed8e6` |
| Snapshot verification | `true` |
| Pilot tasks / excluded seeds | `120 / 240` |

The formal loader verified the exact pilot snapshot, integrated event audit,
task/seed mapping, seed-set hash, and runtime identity before constructing the
production program.

## Validation evidence

The OSC checkout passed the complete infrastructure gate:

```text
steel-module campaign infrastructure: PASS (18-task smoke, 120-task frozen pilot, 914-task production program, managed BC-S1 Phase-2A, finalizer, v1/v2 analyzers)
```

The formal generator reported all `914` tasks, `228,500` events, the exact
five-child allocation, and `accepted_statistical_evidence=true`. The archived
program validator was then run with `--verify-runtime-artifacts` and reported:

```text
steel-module production program: PASS
```

Phase-1 generation and validation did not run Geant4, create ROOT events,
materialize a campaign, create a submission attempt, contact Slurm, or assign a
Slurm job ID. The later Phase-2A operation materialized only the managed plan
described below; it still created no ordinary campaign or submission attempt.

## Formal Phase-2A BC-S1 materialization

The machine-readable evidence lock is
`hpc/osc/configurations/steel-module-production-bc-s1-phase2a-v1.lock.json`.
It anchors the exact OSC directory and must be the starting identity for any
future managed-attempt implementation:

`/users/PAS2524/anolddriver66/g4optics-rn/campaigns/steel-module-production-bc-s1`

| Item | Frozen value |
| --- | --- |
| Phase | `Phase-2A` |
| Child | `BC-S1` |
| Reserved campaign ID | `sm-v1-production-bc-s1-ad01d98af612` |
| Tasks / events | `32 / 8,000` |
| Child plan hash | `1d1bf8a24d7a3e23cca745b718d53e79f8ec6e178b3df44980d75e01f1c609bb` |
| Binding semantic hash | `14a3764457247c981145bd7277b2abb78b4b5038c1c9a40aa7354d3275d993a0` |
| `managed_child.json` SHA-256 | `4c4b6dcbabe82962d90bfd4140cabebc9302d1571b823a53cd50b879294b8a00` |
| `program_binding.json` SHA-256 | `2607b958018e8a32086b9ebe65e424edc5e388702ceac881e5c9d70503526269` |
| Root `SHA256SUMS` SHA-256 | `7fee31135156a6f288f34ed990a87ea19f31d6a39bbde24dd7efe5e6dfcaf47b` |
| Control-plane commit | `4a07a40c43849356959dde60bce3e7b3ef025495` |
| Control-plane tree | `753cb27dba1df1f84a283baa07b8c614c8e5d913` |
| Checkout | clean |
| Accepted statistical evidence | `true` for plan identity and provenance |
| Submittable | `false` |

The formal materializer and dedicated `--check-only` validator both passed.
The root checksum verification returned exit code `0`. No `campaign.json`,
submission journal, source archive, Slurm command, ROOT event, or job ID was
created. The semantic binding hash and the three byte-level file digests have
different roles and must all remain unchanged. The OSC directory is now frozen
and must not be edited or rematerialized.

## Execution boundary and next gate

`accepted_statistical_evidence=true` here means that the immutable Phase-1
plan, pilot exclusion, source identity, and runtime provenance passed their
formal gates. It is not production physics evidence and does not make the
parent or any child directly submittable.

The parent and all five child-plan directories intentionally contain no
`campaign.json`. The generic campaign submitter must continue to reject them.
Phase-2A now provides managed, non-overwriting materialization of the exact
`BC-S1` child plan and a dedicated read-only validation wrapper. The formal CLI
has no output-path, program, child, seed, or expected-identity override; it
reads this record's tracked lock and writes one canonical sibling directory.
The output deliberately contains `managed_child.json` but no `campaign.json`,
so it cannot be submitted by either current or historical generic campaign
wrappers. It preserves the frozen program source as the future execution
source and records the clean Phase-2A checkout separately as the control plane.
The generic submitter is also restricted to the known non-production stage
allowlist. The dedicated wrapper is hard-locked to `--check-only` and exposes
no Slurm, resume, retry, account, or frozen-source option.

Formal OSC child materialization and read-only verification are complete, but
they do not change the authorization boundary. The Phase-2B implementation now
provides the managed attempt contract, whole-child finalization and event/seed
audit, exact `BC-ONLY-S1` checkpoint and analyzer, static human review,
append-only progression decisions, adversarial fake-scheduler tests, and a
separate readiness-evidence proposal. Its detailed contract is
`docs/decisions/steel-module-production-phase2b-v1.md`.

This implementation must still be committed as implementation commit C,
validated on OSC without scheduler contact, and followed by a separately
reviewed readiness-lock commit R. Until that lock is accepted, `BC-S1` remains
unsubmitted and cannot create a formal intent; `FIXED` and
`BC-S2...BC-S4` remain locked.
