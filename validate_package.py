#!/usr/bin/env python3
"""Check an OpenELIS reference package without an OpenELIS installation."""
import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from build_package import FORMAT, NOTICE, PACKAGE_IDS, TABLES, rows

MAX_BYTES = 64 * 1024 * 1024
# What OpenELIS reads each table by. A table without these cannot be loaded.
COLUMNS = {
    "Organisms.txt": ["WHONET_ORG_CODE", "ORGANISM", "TAXONOMIC_STATUS", "REPLACED_BY", "SEROVAR_GROUP",
                      "SPECIES_GROUP", "GENUS_CODE", "GENUS_GROUP", "FAMILY_CODE", "SUBKINGDOM_CODE", "ANAEROBE"],
    "Antibiotics.txt": ["WHONET_ABX_CODE", "ANTIBIOTIC"],
    "Breakpoints.txt": ["GUIDELINES", "YEAR", "TEST_METHOD", "ORGANISM_CODE", "ORGANISM_CODE_TYPE",
                        "BREAKPOINT_TYPE", "SITE_OF_INFECTION", "WHONET_TEST", "R", "I", "SDD", "S"],
    "ExpectedResistancePhenotypes.txt": ["GUIDELINE", "ORGANISM_CODE", "ORGANISM_CODE_TYPE", "ABX_CODE",
                                         "ABX_CODE_TYPE"],
    "ExpertInterpretationRules.txt": ["RULE_CODE", "ORGANISM_CODE", "ORGANISM_CODE_TYPE", "RULE_CRITERIA"],
    "QC_Ranges.txt": ["GUIDELINE", "YEAR", "STRAIN", "WHONET_ABX_CODE", "METHOD", "MINIMUM", "MAXIMUM"],
}
# The levels OpenELIS's matcher walks. A rule at any other level would never be found.
LEVELS = {"SEROVAR_GROUP", "WHONET_ORG_CODE", "SPECIES_GROUP", "GENUS_CODE", "GENUS_GROUP", "FAMILY_CODE",
          "SUBKINGDOM_CODE", "ANAEROBE+SUBKINGDOM_CODE", "ANAEROBE", "ALL", ""}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def table(data):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter="\t", quoting=csv.QUOTE_NONE)
    return reader.fieldnames or [], list(reader)


def validate(content):
    require(len(content) <= MAX_BYTES, "Archive exceeds size limit")
    expected = set(TABLES.values()) | {NOTICE, "manifest.json"}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        require(set(names) == expected and len(names) == len(expected), "Unexpected archive inventory")
        payload = {name: archive.read(name) for name in names}
    manifest = json.loads(payload["manifest.json"])
    require(manifest["format"] == FORMAT, "Unknown format")
    require(manifest["packageId"] in PACKAGE_IDS, "Unknown package identity")
    require(re.fullmatch(r"[0-9a-f]{40}", manifest["source"]["commit"]), "Source commit is not pinned")
    require(
        re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", manifest["packageVersion"]), "Invalid package version"
    )
    require(set(manifest["files"]) == expected - {"manifest.json"}, "Manifest inventory mismatch")
    for name, item in manifest["files"].items():
        data = payload[name]
        require(
            item["sha256"] == hashlib.sha256(data).hexdigest() and item["bytes"] == len(data),
            "Checksum mismatch: " + name,
        )
        if name in TABLES.values():
            require(item["rows"] == rows(data) and item["rows"] > 0, "Row count mismatch: " + name)
    counts = {}
    for name, path in TABLES.items():
        header, data = table(payload[path])
        require(not set(COLUMNS[name]) - set(header), "Missing column in " + name)
        counts[name] = data
    organisms = counts["Organisms.txt"]
    require(
        all(row["TAXONOMIC_STATUS"] in {"C", "O"} for row in organisms), "Unknown organism name status"
    )
    current = [row for row in organisms if row["TAXONOMIC_STATUS"] == "C"]
    codes = [row["WHONET_ORG_CODE"] for row in current]
    require(len(set(codes)) == len(codes), "A WHONET code has two current names")
    # OpenELIS shows organisms by name, so two codes cannot share a current one.
    require(
        len({row["ORGANISM"].lower() for row in current}) == len(current),
        "Two WHONET codes share a current name",
    )
    for name in ("Breakpoints.txt", "ExpectedResistancePhenotypes.txt", "ExpertInterpretationRules.txt"):
        unknown = {row["ORGANISM_CODE_TYPE"] for row in counts[name]} - LEVELS
        require(not unknown, "Organism level OpenELIS does not match on, in " + name)
    return {
        "organisms": len(current),
        "organismRows": len(organisms),
        "antibiotics": len(counts["Antibiotics.txt"]),
        "breakpoints": len(counts["Breakpoints.txt"]),
        "expectedResistance": len(counts["ExpectedResistancePhenotypes.txt"]),
        "expertRules": len(counts["ExpertInterpretationRules.txt"]),
        "qcRanges": len(counts["QC_Ranges.txt"]),
    }


if __name__ == "__main__":
    import pathlib

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument("--sha256", help="Expected archive SHA-256 from the deployment pin")
    args = parser.parse_args()
    content = args.artifact.read_bytes()
    if args.sha256:
        require(hashlib.sha256(content).hexdigest() == args.sha256, "Archive differs from the deployment pin")
    print(json.dumps(validate(content), sort_keys=True))
