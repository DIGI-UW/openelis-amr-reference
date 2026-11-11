#!/usr/bin/env python3
"""Build an OpenELIS configuration package from pinned WHONET and AMR for R releases."""
import argparse
import csv
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
EXPORTER_REPOSITORY = "https://github.com/DIGI-UW/openelis-amr-reference"
R_IMAGE = (
    "r-base@sha256:fa1972f31def171b83e0911e947ab8b57db143f0fc8a67af4c0d5ac329041646"
)
FORMAT = "openelis-configuration/v1"
CONFIGURATION_PATHS = {
    "organisms.csv": "configuration/backend/microbiology-organisms/organisms.csv",
    "organism-names.csv": "configuration/backend/microbiology-organism-names/organism-names.csv",
    "antimicrobials.csv": "configuration/backend/microbiology-antimicrobials/antimicrobials.csv",
    "breakpoints.csv": "configuration/backend/microbiology-breakpoints/breakpoints.csv",
}


def archive_path(name):
    return CONFIGURATION_PATHS.get(name, "evidence/" + name)



def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def package_files(directory, metadata):
    payload = {
        p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()
    }
    files = {}
    for name, data in payload.items():
        item = {"sha256": sha256(data), "bytes": len(data)}
        if name.endswith(".csv"):
            item["rows"] = len(list(csv.DictReader(io.StringIO(data.decode("utf-8")))))
        files[archive_path(name)] = item
    payload = {archive_path(name): data for name, data in payload.items()}
    payload["manifest.json"] = canonical_json({**metadata, "files": files})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return output.getvalue()


def read_pin(path, files=()):
    """A source version to build from: its repository, one full commit and the files read from it."""
    pin = json.loads(path.read_text())
    if set(pin) - {"release"} != {"repository", "commit", *files}:
        raise ValueError("The source pin requires repository and commit")
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", pin["repository"]):
        raise ValueError("The source repository must be a GitHub https URL")
    if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]):
        raise ValueError(
            "The source commit must be a full lowercase 40-character commit, not a moving ref"
        )
    return pin


def fetch_source(pin, directory):
    """Fetch exactly the pinned commit; a named release must still point at it."""
    def git(*args):
        return subprocess.check_output(["git", "-C", str(directory), *args])

    subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
    git("fetch", "--quiet", "--depth=1", pin["repository"], pin["commit"])
    if git("rev-parse", "FETCH_HEAD").decode().strip() != pin["commit"]:
        raise ValueError("Fetched source differs from the pinned commit")
    if pin.get("release"):
        listed = subprocess.check_output(
            ["git", "ls-remote", pin["repository"], "refs/tags/" + pin["release"]]
        ).decode().split()
        if not listed or listed[0] != pin["commit"]:
            raise ValueError("Release " + pin["release"] + " is not the pinned commit")
    return git


def validate_scope(scope):
    if set(scope) != {"guidelines", "organisms", "methods"}:
        raise ValueError("Scope requires guidelines, organisms and methods")
    if scope["methods"] != ["MIC", "ZONE"]:
        raise ValueError("Scope must account for MIC and ZONE contexts")
    if scope["guidelines"] not in (["EUCAST 2025", "EUCAST 2026"], ["CLSI 2025", "CLSI 2026"]):
        raise ValueError("Build each publisher separately for exactly 2025 and 2026")
    if scope["organisms"] != "whonet-organism-list":
        raise ValueError("Scope requires whonet-organism-list")


def build(args):
    scope = json.loads(args.scope.read_text())
    validate_scope(scope)
    pin = read_pin(args.source)
    whonet_pin = read_pin(args.whonet_source, files=("organisms", "license"))
    if len(args.package_version) > 40 or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,2}", args.package_version
    ):
        raise ValueError("package-version must be a numeric release such as 2026.1")
    commit = pin["commit"]
    with tempfile.TemporaryDirectory(prefix="openelis-reference-") as work:
        work = pathlib.Path(work)
        (work / "upstream").mkdir()
        git = fetch_source(pin, work / "upstream")
        description = git("show", commit + ":DESCRIPTION").decode()
        version = re.search(r"^Version: (.+)$", description, re.MULTILINE).group(1)
        source = work / "source"
        source.mkdir()
        archive = git(
            "archive", commit, "DESCRIPTION", "NAMESPACE", "R", "data", "inst"
        )
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(source, filter="data")
        output = work / "artifact"
        output.mkdir()
        (work / "library").mkdir()
        (work / "whonet").mkdir()
        whonet_git = fetch_source(whonet_pin, work / "whonet")
        whonet_organisms = whonet_git("show", whonet_pin["commit"] + ":" + whonet_pin["organisms"])
        (work / "whonet-organisms.txt").write_bytes(whonet_organisms)
        whonet_rows = list(
            csv.DictReader(io.StringIO(whonet_organisms.decode("utf-8-sig")), delimiter="\t")
        )
        (work / "guidelines.txt").write_text(
            "\n".join(sorted(set(scope["guidelines"]))) + "\n"
        )
        if args.runtime == "docker":
            mount = [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{work}:/work",
                "-v",
                f"{HERE}:/exporter:ro",
                R_IMAGE,
            ]
            subprocess.run(
                mount
                + [
                    "R",
                    "CMD",
                    "INSTALL",
                    "--no-docs",
                    "--no-html",
                    "--library=/work/library",
                    "/work/source",
                ],
                check=True,
            )
            subprocess.run(
                mount
                + [
                    "Rscript",
                    "--vanilla",
                    "/exporter/export.R",
                    "/work/library",
                    "/work/whonet-organisms.txt",
                    "/work/guidelines.txt",
                    args.package_version,
                    "/work/artifact",
                ],
                check=True,
            )
        else:
            subprocess.run(
                [
                    "R",
                    "CMD",
                    "INSTALL",
                    "--no-docs",
                    "--no-html",
                    "--library=" + str(work / "library"),
                    str(source),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "Rscript",
                    "--vanilla",
                    str(HERE / "export.R"),
                    str(work / "library"),
                    str(work / "whonet-organisms.txt"),
                    str(work / "guidelines.txt"),
                    args.package_version,
                    str(output),
                ],
                check=True,
            )
        (output / "scope.json").write_bytes(canonical_json(scope))
        (output / "NOTICE.txt").write_text(
            "Organism list: WHONET " + (whonet_pin.get("release") or whonet_pin["commit"]) + ", "
            + whonet_pin["organisms"] + ", WHO Collaborating Centre for Surveillance of Antimicrobial\n"
            "Resistance, Brigham and Women's Hospital / Mass General Brigham. https://whonet.org\n"
            "Exact source: " + whonet_pin["repository"] + "/tree/" + whonet_pin["commit"] + "\n"
            "Its licence follows the AMR for R licence below.\n"
            "Breakpoints: AMR for R " + version + " (GPL-2.0), built by DIGI-UW/openelis-amr-reference.\n"
            "Clinical breakpoint source: " + scope["guidelines"][0].split()[0] + "; retain its attribution and usage terms.\n"
            "https://amr-for-r.org/reference/clinical_breakpoints.html\n"
            "https://www.eucast.org/clinical_breakpoints\n"
            "This is a scoped testing reference package, not the complete guideline or clinical approval.\n"
            "Numeric MIC rules require unconditional or explicitly selected supported clinical context.\n"
            "Zone rules apply only to the disk content they name.\n"
            "Unsupported site, route, UTI, SDD and conditional-note contexts, and zone rules without one disk\n"
            "content, load as explicit No breakpoint rules.\n"
            "The AMR package documents public distribution of its EUCAST and CLSI breakpoint datasets.\n"
            "Berends MS et al. (2022), Journal of Statistical Software 104(3), 1-31.\n"
            "https://doi.org/10.18637/jss.v104.i03\n"
            "Exact source: " + pin["repository"] + "/tree/" + commit + "\n"
            "Coverage and source-rows CSVs account for exclusions. New editions load inactive.\n\n"
            + git("show", commit + ":LICENSE").decode("utf-8")
            + "\n\n"
            + whonet_git("show", whonet_pin["commit"] + ":" + whonet_pin["license"]).decode("utf-8"),
            encoding="utf-8",
        )
        metadata = {
            "format": FORMAT,
            "runtime": {
                "mode": args.runtime,
                "image": R_IMAGE if args.runtime == "docker" else None,
            },
            "packageId": "amr-reference-" + scope["guidelines"][0].split()[0].lower(),
            "packageVersion": args.package_version,
            "source": {
                "repository": pin["repository"],
                "commit": commit,
                "release": pin.get("release"),
                "packageVersion": version,
                "dataSha256": {
                    name: sha256(git("show", commit + ":data/" + name + ".rda"))
                    for name in [
                        "microorganisms",
                        "microorganisms.codes",
                        "microorganisms.groups",
                        "antimicrobials",
                        "clinical_breakpoints",
                    ]
                },
            },
            "whonetSource": {
                "repository": whonet_pin["repository"],
                "commit": whonet_pin["commit"],
                "release": whonet_pin.get("release"),
                "organisms": whonet_pin["organisms"],
                "organismsSha256": sha256(whonet_organisms),
                "rows": len(whonet_rows),
                "names": len(
                    {(row["WHONET_ORG_CODE"].strip(), row["ORGANISM"].strip()) for row in whonet_rows}
                ),
                "codes": len({row["WHONET_ORG_CODE"].strip() for row in whonet_rows}),
            },
            "exporterRepository": EXPORTER_REPOSITORY,
            "exporterSha256": {
                name: sha256((HERE / name).read_bytes())
                for name in ["build_package.py", "export.R", "validate_package.py"]
            },
            "scope": scope,
            "standards": [
                {
                    "publisher": x.split()[0],
                    "version": x.split()[1] + "-" + args.package_version,
                    "guideline": x,
                }
                for x in sorted(set(scope["guidelines"]))
            ],
        }
        content = package_files(output, metadata)
        from validate_package import validate

        counts = validate(content)
        print(json.dumps({"bytes": len(content), **counts}, sort_keys=True), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and args.output.read_bytes() != content:
        raise ValueError(
            "Refusing to replace an existing artifact with different content"
        )
    args.output.write_bytes(content)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        sha256(content) + "  " + args.output.name + "\n"
    )
    print(f"{args.output}: sha256:{sha256(content)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=pathlib.Path, default=HERE / "amr-source.json",
        help="Pin file naming the AMR for R repository and commit",
    )
    parser.add_argument(
        "--whonet-source", type=pathlib.Path, default=HERE / "whonet-source.json",
        help="Pin file naming the WHONET repository, commit and organism file",
    )
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--scope", type=pathlib.Path, default=HERE / "scope-eucast.json")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--runtime", choices=["local", "docker"], default="docker")
    build(parser.parse_args())
