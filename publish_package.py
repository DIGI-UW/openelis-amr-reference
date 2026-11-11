#!/usr/bin/env python3
"""Publish a validated data artifact with ORAS; print its immutable deployment pin."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
from validate_package import validate


def package_visibility(name):
    result = subprocess.run(["gh", "api", "orgs/DIGI-UW/packages/container/" + name],
                            text=True, capture_output=True)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None  # An absent package has no verified visibility.
        raise RuntimeError("Cannot verify GHCR package visibility: " + result.stderr)
    return json.loads(result.stdout)["visibility"]


def publish(artifact, repository):
    content = artifact.read_bytes()
    validate(content)
    with zipfile.ZipFile(artifact) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    identity, version = manifest["packageId"], manifest["packageVersion"]
    if repository != "ghcr.io/digi-uw/" + identity:
        raise ValueError("Registry destination differs from publisher identity")
    visibility = package_visibility(identity)
    if visibility != "public":
        raise ValueError("Publication requires an existing public GHCR package")
    reference = repository + ":" + version
    existing = subprocess.run(["oras", "resolve", reference], text=True, capture_output=True)
    if existing.returncode == 0:
        raise ValueError("Version already published; never replace an immutable release")
    if "not found" not in existing.stderr.lower() and "404" not in existing.stderr:
        raise RuntimeError("Cannot confirm an unused release tag: " + existing.stderr)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    for name, expected in manifest["exporterSha256"].items():
        committed = subprocess.check_output(["git", "show", revision + ":" + name])
        if hashlib.sha256(committed).hexdigest() != expected:
            raise ValueError("Artifact exporter differs from the committed release revision: " + name)
    result = subprocess.check_output([
        "oras", "push", "--format", "json", "--artifact-type", "application/vnd.openelis.configuration.v1+zip",
        "--annotation", "org.opencontainers.image.source=https://github.com/DIGI-UW/openelis-amr-reference",
        "--annotation", "org.opencontainers.image.revision=" + revision,
        "--annotation", "org.opencontainers.image.version=" + version,
        reference, artifact.name + ":application/zip",
    ], cwd=artifact.resolve().parent, text=True)
    descriptor = json.loads(result)
    if package_visibility(identity) != "public":
        raise RuntimeError("Public package visibility verification failed after upload")
    pin = {"id": identity, "version": version,
           "url": "oci://" + repository + "@" + descriptor["digest"],
           "sha256": hashlib.sha256(content).hexdigest()}
    print(json.dumps(pin, indent=2))
    return pin


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    publish(args.artifact, args.repository)
