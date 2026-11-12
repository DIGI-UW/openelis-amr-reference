# OpenELIS AMR reference package

This repository packages the reference tables [WHONET](https://whonet.org)
publishes, so that OpenELIS offers the organisms, codes, antibiotics and
interpretations a WHONET user expects. The tables are carried **byte for byte**
from one pinned WHONET release. Nothing is filtered, renamed, matched or
generated, and the build runs no R.

GHCR stores the package under the two public names deployments already pin,
`amr-reference-eucast` and `amr-reference-clsi`. From version 2026.4 both
carry the same tables, both guidelines included; a deployment keeps the name it
pins and changes only the version. A deployment pins the digest and places the
files in OpenELIS's configuration folder, where OpenELIS's loaders read them.

## What the package carries

| WHONET table | Holds | Package path under `configuration/backend/` |
|---|---|---|
| `Organisms.txt` | Organism names, codes, older names, replaced codes, and the groups rules are matched on | `microbiology-whonet-organisms/` |
| `Antibiotics.txt` | Antibiotic codes, classes, ATC and LOINC codes, WHO AWaRe category | `microbiology-whonet-antibiotics/` |
| `Breakpoints.txt` | Clinical breakpoints and epidemiological cut-offs, every guideline and year WHONET publishes | `microbiology-whonet-breakpoints/` |
| `ExpectedResistancePhenotypes.txt` | Expected (intrinsic) resistance | `microbiology-whonet-expected-resistance/` |
| `ExpertInterpretationRules.txt` | WHONET's expert rules | `microbiology-whonet-expert-rules/` |
| `QC_Ranges.txt` | Quality control ranges for AST | `microbiology-whonet-qc-ranges/` |

Each file keeps WHONET's name, columns, tab separators and line endings. The
package also holds:

- `evidence/NOTICE.txt`: the source, its exact commit, and the licence file
  of that commit. Release v26.3.26 is under GPL v3. WHONET's main branch moved
  to the MGB Open Access License 1.0 in August 2026, so a later pin carries
  that licence instead.
- `manifest.json`: the package version, the source pin, and each file's
  SHA-256, size and row count.

OpenELIS decides which guidelines and years a laboratory uses. The package does
not select them.

## The WHONET pin

[`whonet-source.json`](whonet-source.json) names the source: WHONET's
[AMRIE repository](https://github.com/AClark-WHONET/AMRIE), one tagged release,
that release's full commit, the directory holding the tables and the licence
file. The build fetches exactly that commit and fails if the release tag no
longer points at it.

To take a newer WHONET release, change `release` and `commit` (and `directory`
if WHONET has moved the tables), open a pull request and read the row counts
the build prints.

WHONET's tables transcribe what CLSI and EUCAST publish. A laboratory checks
its breakpoints against the published documents before reporting on patients.

## Checks

The build copies; the checks only confirm that OpenELIS can load the result:

- The archive holds the six tables, the notice and the manifest, and nothing else.
- Every file matches the manifest's checksum, size and row count.
- Each table has the columns OpenELIS reads it by.
- A WHONET code has one current name, and no two codes share a current name.
- Every rule targets an organism level OpenELIS matches on.

The tests add one more: every table in a built package equals the file WHONET
serves for the pinned commit.

## Build and test

Python 3.12 and git. A build takes a few seconds.

```sh
python3 build_package.py --package-id amr-reference-clsi --package-version 0.0 --output dist/package.zip
AMR_REFERENCE_ARTIFACT=dist/package.zip python3 -m unittest discover -p 'test_*.py' -v
```

The same source pin, version and build scripts always give the same archive
bytes.

## GHCR publication and deployment pins

Every pull request builds the package and runs the tests. Nothing runs on merge.

To release, tag the commit on main and push the tag. The tag names the version:

```sh
git tag v2026.4 && git push origin v2026.4
```

The workflow builds the package from the tagged commit, tests it and publishes
it to `ghcr.io/digi-uw/amr-reference-eucast` and
`ghcr.io/digi-uw/amr-reference-clsi` as OCI data artifacts with one
`application/zip` layer using ORAS. Published versions are immutable; a tag for
a published version fails.

Both packages are **public** in GHCR and this repository has write access to
them. The publisher checks they are public before and after uploading.

For an authorized local release, authenticate `gh` and `oras` to GHCR, then run:

```sh
python3 publish_package.py dist/package.zip --repository ghcr.io/digi-uw/amr-reference-clsi
```

The command prints a JSON deployment pin with `id`, `version`, the immutable
`oci://ghcr.io/...@sha256:<manifest digest>` URL and the ZIP `sha256`. Commit
that pin in the distribution, never a placeholder or moving tag. Verify the
distribution's initializer with an empty cache and no registry credentials
before accepting a release.

## Earlier versions

Versions 2026.1 to 2026.3 of both packages were built from AMR for R, one
guideline each, and remain in GHCR unchanged.
