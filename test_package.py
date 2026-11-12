"""Package contract tests; set AMR_REFERENCE_ARTIFACT for the cases that read a built package."""

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.parse
import urllib.request
import zipfile

from build_package import NOTICE, TABLES, package_files, read_pin
from validate_package import validate

PIN = Path(__file__).with_name("whonet-source.json")
ORGANISMS = TABLES["Organisms.txt"]
BREAKPOINTS = TABLES["Breakpoints.txt"]


def repack(content, changes, manifest=None):
    """The package with some files replaced. Checksums follow unless a manifest is given."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        payload = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(payload.pop("manifest.json"))
    metadata.pop("files")
    payload.update(changes)
    if manifest is None:
        return package_files(payload, metadata)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in {**payload, "manifest.json": manifest}.items():
            archive.writestr(name, data)
    return output.getvalue()


class PinTest(unittest.TestCase):
    def test_source_pin_must_name_one_full_commit(self):
        pin = json.loads(PIN.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pin.json"
            for changes in (
                {"commit": "main"},
                {"commit": pin["commit"][:12]},
                {"repository": "git@github.com:AClark-WHONET/AMRIE.git"},
                {"branch": "main"},
            ):
                path.write_text(json.dumps({**pin, **changes}))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    read_pin(path)
            path.write_text(json.dumps(pin))
            self.assertEqual(pin["commit"], read_pin(path)["commit"])


@unittest.skipUnless(os.environ.get("AMR_REFERENCE_ARTIFACT"), "needs a built package")
class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = Path(os.environ["AMR_REFERENCE_ARTIFACT"]).read_bytes()
        with zipfile.ZipFile(io.BytesIO(cls.content)) as archive:
            cls.payload = {name: archive.read(name) for name in archive.namelist()}
        cls.manifest = json.loads(cls.payload["manifest.json"])

    def test_every_table_is_whonets_published_file_byte_for_byte(self):
        pin = read_pin(PIN)
        self.assertEqual(pin["commit"], self.manifest["source"]["commit"])
        raw = pin["repository"].replace("github.com", "raw.githubusercontent.com")
        for name, path in TABLES.items():
            url = "/".join([raw, pin["commit"], urllib.parse.quote(pin["directory"]), name])
            with self.subTest(table=name), urllib.request.urlopen(url) as published:
                self.assertEqual(
                    hashlib.sha256(published.read()).hexdigest(),
                    hashlib.sha256(self.payload[path]).hexdigest(),
                )

    def test_the_package_holds_whonets_tables_and_nothing_else(self):
        self.assertEqual(
            set(TABLES.values()) | {NOTICE, "manifest.json"}, set(self.payload)
        )
        counts = validate(self.content)
        self.assertEqual(
            counts["organismRows"], self.manifest["files"][ORGANISMS]["rows"]
        )
        self.assertEqual(
            counts["breakpoints"], self.manifest["files"][BREAKPOINTS]["rows"]
        )

    def test_whonets_licence_travels_with_the_tables(self):
        pin = read_pin(PIN)
        raw = pin["repository"].replace("github.com", "raw.githubusercontent.com")
        with urllib.request.urlopen("/".join([raw, pin["commit"], pin["license"]])) as published:
            licence = published.read()
        self.assertTrue(self.payload[NOTICE].endswith(licence))
        self.assertIn(pin["commit"].encode(), self.payload[NOTICE])

    def test_the_archive_does_not_depend_on_the_build_machine(self):
        self.assertEqual(self.content, repack(self.content, {}))

    def test_a_changed_table_is_detected(self):
        changed = self.payload[ORGANISMS].replace(b"Klebsiella", b"Klebsiela", 1)
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            validate(
                repack(self.content, {ORGANISMS: changed}, self.payload["manifest.json"])
            )

    def test_an_extra_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inventory"):
            validate(
                repack(
                    self.content,
                    {"configuration/backend/other.csv": b"x"},
                    self.payload["manifest.json"],
                )
            )

    def test_a_table_openelis_cannot_read_is_rejected(self):
        renamed = self.payload[ORGANISMS].replace(b"WHONET_ORG_CODE", b"ORG_CODE", 1)
        with self.assertRaisesRegex(ValueError, "Missing column in Organisms.txt"):
            validate(repack(self.content, {ORGANISMS: renamed}))

    def test_a_package_the_registry_does_not_hold_is_rejected(self):
        manifest = {**self.manifest, "packageId": "amr-reference"}
        with self.assertRaisesRegex(ValueError, "Unknown package identity"):
            validate(repack(self.content, {}, json.dumps(manifest).encode()))

    def test_a_rule_at_a_level_openelis_does_not_match_on_is_rejected(self):
        relevelled = self.payload[BREAKPOINTS].replace(b"\tGENUS_CODE\t", b"\tORDER_CODE\t", 1)
        with self.assertRaisesRegex(ValueError, "Organism level"):
            validate(repack(self.content, {BREAKPOINTS: relevelled}))


if __name__ == "__main__":
    unittest.main()
