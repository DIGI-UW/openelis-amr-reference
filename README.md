# OpenELIS AMR reference packages

This repository builds OpenELIS configuration packages (organisms,
antimicrobials and breakpoints) from one pinned release of
[AMR for R](https://github.com/msberends/AMR). GHCR stores the packages. A
deployment pins a package's digest and places its files in OpenELIS's existing
configuration folder. OpenELIS uses its normal catalog handlers; it needs
neither R nor a package archive importer.

## The AMR for R pin

`amr-source.json` names the upstream repository and one full commit, with the
release it belongs to:

```json
{
  "repository": "https://github.com/msberends/AMR",
  "release": "v3.1.0",
  "commit": "052edc476158f1bad7a3df3b0fc78c95eea8ace4"
}
```

The build fetches exactly that commit and refuses to run if the named release
no longer points at it. Pin a release by default; a commit between releases is
allowed when a fix is needed, by leaving `release` out. To take a newer AMR for
R version, change the pin in a pull request: its test build shows whether the
packages still build and pass.

## Scope and interpretation

The scope is **human EUCAST 2025/2026 and CLSI 2025/2026**, built separately.
There is no rolling latest-year selection. Veterinary standards, epidemiological
cutoffs and expert rules are outside this release.

Two scope settings choose organisms. `organisms: human-breakpoint-taxa` is the
**rule scope**: every organism target in the selected source editions, plus
accepted species of common human prevalence beneath those targets that have
WHONET mappings in the source. Breakpoint rules are expanded for these organisms
only. `organismList` chooses which organisms are **listed as concepts**:
`rule-scope` lists the rule scope alone; `accepted-taxa` also lists every taxon
the source marks accepted (about 70,000), so a laboratory can record any
identification. A listed organism outside the rule scope has no rule of its own
and reads No breakpoint. Synonyms are not listed.
Taxonomic ancestry and group membership come from the same pinned AMR for R commit.
All antimicrobial concepts referenced by candidate contexts are included, even
when a particular context cannot produce an interpretation. WHONET mappings use
unambiguous exact source mappings; missing mappings are left blank.

Each organism/agent/method/edition context receives an exact organism rule.
Supported MIC rules retain the original thresholds. EUCAST uses a strict
resistant boundary; CLSI uses an inclusive boundary and rounds the measured MIC
up to the conventional dilution ladder for interpretation. The measured value
itself is preserved by OpenELIS. Proposed numeric results are checked against
AMR's actual `as.sir()` using its normal source data and matching logic.

The exporter writes explicit empty rules for unsupported contexts. These return
**No breakpoint** and prevent a broad numeric fallback. Coverage records explain:

- Disk diffusion when the source gives no single disk content for the rule.
- Site, route, urinary-tract conditions and conditional notes, except the exact
  `Non-meningitis` MIC context described below.
- Susceptible-dose-dependent, incomplete and no-susceptible-category rules.
- Ambiguous equally ranked source rules or disagreement with source interpretation.

Nonempty notes are treated conservatively. Unsupported cases are accounted for,
not converted to convenient clinical thresholds. `coverage.csv` is the exact
supported/blocked inventory. This is a testing reference package, not approval
of every PNG clinical workflow. Capped observations and expert-rule logic require
separate receiving support.

## Explicit clinical indication

The CSV `clinical_context` column is blank for unconditional or blocked rules.
`NON_MENINGITIS` supplies an additional exact source context only when the
best-ranked source rows explicitly say `Non-meningitis`. Notes, urinary-tract
conditions, incomplete thresholds, disk methods, and ambiguous rules remain
blocked. The same source-interpreter verification applies to these numeric rules.
The receiving workflow must explicitly record that indication on the run; it must
not infer it from a blood specimen or use these rows when context is unspecified.
Existing package versions are immutable; this support requires a new release.

## Disk content and organism groups

A zone breakpoint holds only for the disk content it was published for. Each
zone row carries that content in `disk_content`, spelled as the source spells it
(for example `30 mcg`), and is blank for MIC rows. A receiving system must
interpret a zone reading only against a rule with the same disk content.
Zone rules are verified against the source interpreter the same way as MIC
rules: a larger zone is more susceptible, CLSI's resistant boundary is
inclusive and EUCAST's is strict.

`organisms.csv` carries `organism_group`: the nearest group above species that
breakpoint rules target, either a named group (for example Beta-haemolytic
Streptococcus) or the genus, family, order or class. It is blank when no rule
targets any of them.

## WHONET codes and names

`whonetCode` and `whonetName` come from the source's WHONET-labelled codes. A
genus takes the code WHONET labels `<genus> sp.` (for example `kl-`,
`Klebsiella sp.`), the entry a laboratory uses when it has not identified the
species. Every other organism takes the code labelled with its own name. A label
that fits neither rule, or that names more than one code, leaves both columns
blank; nothing is matched by similarity. `rank` and `prevalence` are the
source's values, for ordering and filtering organism searches.

## Build and test

Use Git, Python 3.12+ and Docker. The builder fetches the pinned AMR for R
commit and installs it in an isolated library using a digest-pinned R image,
without container network access or third-party R/Python dependencies.

```sh
python3 build_package.py --package-version 0.0 --output dist/eucast.zip

# CLSI is a separate package with its own deployment pin.
python3 build_package.py --scope scope-clsi.json --package-version 0.0 \
  --output dist/clsi.zip

AMR_REFERENCE_ARTIFACT=dist/eucast.zip python3 -m unittest discover -p 'test_*.py' -v
```

Run the package tests for each publisher. `--runtime local` is a development
convenience; release builds use pinned Docker. A changed dataset, scope or
exporter requires a new package version. Never replace an existing release. New
edition identities preserve earlier result references.

## Package layout

The generic envelope is `openelis-configuration/v1`. `manifest.json` records
package identity/version, source commit, dataset and exporter checksums, scope,
editions and each file's SHA-256, size and CSV row count. Package IDs are
`amr-reference-eucast` and `amr-reference-clsi`.

```text
manifest.json
configuration/backend/microbiology-organisms/organisms.csv
configuration/backend/microbiology-antimicrobials/antimicrobials.csv
configuration/backend/microbiology-breakpoints/breakpoints.csv
evidence/scope.json
evidence/coverage.csv
evidence/source-rows.csv
evidence/interpretation-vectors.csv
evidence/NOTICE.txt
```

Concept identity is `source=AMR` plus the source code. Display names and WHONET
codes are matching aids. Group rules expand into exact organism rules; this
package does not introduce a shared group fallback in OpenELIS.

Coverage records each context as SUPPORTED or BLOCKED with its source row IDs.
The source ledger accounts for all human rows in the selected editions. Numeric
comparison cases come from the source interpreter; blocked cases assert the
receiving application's No breakpoint behavior. Evidence stays outside the
runtime configuration scan. No executable code is installed.

Each edition version includes the package version, such as `2026-2026.1`.
The receiving application rejects changes to an existing managed edition. Changes
to unrelated release evidence do not change that edition's content identity.

## GHCR publication and deployment pins

Every pull request builds each package once and runs the package tests. Both
builds must pass before a merge. Nothing runs on merge.

To release, tag the commit on main and push the tag. The tag names the version:

```sh
git tag v2026.3 && git push origin v2026.3
```

The workflow builds both packages from the tagged commit, tests them and
publishes them as OCI data artifacts with one `application/zip` layer using
ORAS. Existing versions are immutable; a tag for a published version fails.
Versions 2026.1 and 2026.2 were built in the DIGI-UW/AMR fork, branch
`c/ogc-1533-reference-package`, and have no tag here.

Both packages must be **public** in GHCR, independently of their source
repository visibility. The publisher checks this before and after uploading.
For first publication, create an empty registry bootstrap artifact, set the
package to public in GitHub's package settings, then publish the release.
An internal package still requires credentials and is unsuitable for these pins.

[AMR's dataset documentation](https://amr-for-r.org/reference/clinical_breakpoints.html)
describes public distribution of both datasets and their source attribution.
This package distributes those scoped data, not the publishers' complete
standards documents. Preserve the included attribution and clinical limitations.
The private-only CLSI sentence in the original 2026.1 NOTICE was an erroneous
exporter restriction; it is superseded by this policy. The published release
bytes and checksums remain unchanged.

For an authorized local release, authenticate `gh` and `oras` to GHCR, then run:

```sh
python3 publish_package.py dist/eucast.zip \
  --repository ghcr.io/digi-uw/amr-reference-eucast
```

The command prints a JSON deployment pin with `id`, `version`, the immutable
`oci://ghcr.io/...@sha256:<manifest digest>` URL and the ZIP `sha256`. Commit that
actual pin in the distribution, never a placeholder or moving tag. Verify the
normal distribution initializer using a new empty cache and no registry
credentials before accepting a release; an authenticated publisher pull is not
evidence of anonymous deployment access.

The PNG initializer verifies the manifest digest, layer digest, ZIP checksum and
file inventory, caches the archive, then installs ordinary configuration files.
It preserves local files and historical versions and reuses verified cache files
offline. The existing OpenELIS configuration loader imports concepts before
breakpoints and preserves referenced IDs and local customizations.

Before deployment, run the receiving Java compatibility test against each real
artifact, verify fresh loading and reload/upgrade, and inspect import reports.
New editions load inactive; activation is an explicit laboratory administration
step. HTTP health alone is not evidence that configuration loaded successfully.
No concept data belongs in Liquibase or the OpenELIS application image.
