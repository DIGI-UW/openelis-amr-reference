#!/usr/bin/env python3
"""Build an OpenELIS configuration package that carries one pinned WHONET release, byte for byte."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
EXPORTER_REPOSITORY = "https://github.com/DIGI-UW/openelis-amr-reference"
FORMAT = "openelis-configuration/v2"
# The public registry packages deployments already pin. Each carries the same tables.
PACKAGE_IDS = ("amr-reference-eucast", "amr-reference-clsi")
# WHONET's tables, each under the configuration folder OpenELIS loads it from.
TABLES = {
    "Organisms.txt": "configuration/backend/microbiology-whonet-organisms/Organisms.txt",
    "Antibiotics.txt": "configuration/backend/microbiology-whonet-antibiotics/Antibiotics.txt",
    "Breakpoints.txt": "configuration/backend/microbiology-whonet-breakpoints/Breakpoints.txt",
    "ExpectedResistancePhenotypes.txt": "configuration/backend/microbiology-whonet-expected-resistance/ExpectedResistancePhenotypes.txt",
    "ExpertInterpretationRules.txt": "configuration/backend/microbiology-whonet-expert-rules/ExpertInterpretationRules.txt",
    "QC_Ranges.txt": "configuration/backend/microbiology-whonet-qc-ranges/QC_Ranges.txt",
}
NOTICE = "evidence/NOTICE.txt"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def rows(data):
    """Data rows of a WHONET table: its lines, less the header."""
    return max(len(data.splitlines()) - 1, 0)


def package_files(payload, metadata):
    """The archive for these files, the same bytes whatever the build machine."""
    files = {}
    for path, data in payload.items():
        files[path] = {"sha256": sha256(data), "bytes": len(data)}
        if path in TABLES.values():
            files[path]["rows"] = rows(data)
    payload = {**payload, "manifest.json": canonical_json({**metadata, "files": files})}
    output = __import__("io").BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data, compresslevel=9)
    return output.getvalue()


def read_pin(path):
    """The WHONET release to carry: its repository, one full commit and where its tables are."""
    pin = json.loads(path.read_text())
    if set(pin) - {"release"} != {"repository", "commit", "directory", "license"}:
        raise ValueError("The source pin requires repository, commit, directory and license")
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


def build(args):
    pin = read_pin(args.source)
    if len(args.package_version) > 40 or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,2}", args.package_version
    ):
        raise ValueError("package-version must be a numeric release such as 2026.4")
    commit = pin["commit"]
    with tempfile.TemporaryDirectory(prefix="openelis-reference-") as work:
        git = fetch_source(pin, pathlib.Path(work))
        payload = {
            path: git("show", commit + ":" + pin["directory"] + "/" + name)
            for name, path in TABLES.items()
        }
        licence = git("show", commit + ":" + pin["license"])
    payload[NOTICE] = (
        "WHONET reference tables, " + (pin.get("release") or commit) + ", carried unchanged.\n"
        "WHO Collaborating Centre for Surveillance of Antimicrobial Resistance, Brigham and Women's\n"
        "Hospital / Mass General Brigham, Boston. https://whonet.org\n"
        "Exact source: " + pin["repository"] + "/tree/" + commit + "\n"
        "The tables transcribe breakpoints, expected resistance and quality control ranges published by\n"
        "CLSI and EUCAST. Check them against the published documents before reporting on patients.\n"
        "Packaged by DIGI-UW/openelis-amr-reference. WHONET's licence follows.\n\n"
    ).encode("utf-8") + licence
    metadata = {
        "format": FORMAT,
        "packageId": args.package_id,
        "packageVersion": args.package_version,
        "source": {
            "repository": pin["repository"],
            "commit": commit,
            "release": pin.get("release"),
            "directory": pin["directory"],
        },
        "exporterRepository": EXPORTER_REPOSITORY,
        "exporterSha256": {
            name: sha256((HERE / name).read_bytes())
            for name in ["build_package.py", "validate_package.py", "whonet-source.json"]
        },
    }
    content = package_files(payload, metadata)
    from validate_package import validate

    counts = validate(content)
    print(json.dumps({"bytes": len(content), **counts}, sort_keys=True), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and args.output.read_bytes() != content:
        raise ValueError("Refusing to replace an existing artifact with different content")
    args.output.write_bytes(content)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        sha256(content) + "  " + args.output.name + "\n"
    )
    print(f"{args.output}: sha256:{sha256(content)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=pathlib.Path, default=HERE / "whonet-source.json",
        help="Pin file naming the WHONET repository, commit and table directory",
    )
    parser.add_argument("--package-id", required=True, choices=PACKAGE_IDS)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    build(parser.parse_args())
