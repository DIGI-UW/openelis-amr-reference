"""Distribution contract tests; set AMR_REFERENCE_ARTIFACT for the real-source cases."""

import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from build_package import archive_path, package_files, read_pin, validate_scope
from validate_package import validate


class ScopeTest(unittest.TestCase):
    def test_only_explicit_two_editions_are_accepted(self):
        scope = json.loads(Path(__file__).with_name("scope-eucast.json").read_text())
        validate_scope(scope)
        for guidelines in (
            ["EUCAST 2024", "EUCAST 2025"],
            ["EUCAST 2025", "CLSI 2026"],
            ["EUCAST 2026"],
            ["latest"],
        ):
            with self.subTest(guidelines=guidelines), self.assertRaises(ValueError):
                validate_scope({**scope, "guidelines": guidelines})

    def test_source_pin_must_name_one_full_commit(self):
        pin = json.loads(Path(__file__).with_name("amr-source.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pin.json"
            for changes in (
                {"commit": "main"},
                {"commit": pin["commit"][:12]},
                {"repository": "git@github.com:msberends/AMR.git"},
                {"branch": "main"},
            ):
                path.write_text(json.dumps({**pin, **changes}))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    read_pin(path)
            path.write_text(json.dumps(pin))
            self.assertEqual(pin["commit"], read_pin(path)["commit"])

    def test_methods_and_duplicate_identities_cannot_be_silently_coerced(self):
        scope = json.loads(Path(__file__).with_name("scope-eucast.json").read_text())
        for changes in (
            {"methods": ["DISK"]},
            {"organisms": ["B_ESCHR_COLI", "B_ESCHR_COLI"]},
            {"organisms": []},
            {"organismList": "everything"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_scope({**scope, **changes})


@unittest.skipUnless(
    os.environ.get("AMR_REFERENCE_ARTIFACT"),
    "Build an artifact and set AMR_REFERENCE_ARTIFACT",
)
class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = Path(os.environ["AMR_REFERENCE_ARTIFACT"]).read_bytes()
        with zipfile.ZipFile(io.BytesIO(cls.content)) as archive:
            cls.payload = {Path(name).name: archive.read(name) for name in archive.namelist()}
        cls.metadata = json.loads(cls.payload["manifest.json"])
        del cls.metadata["files"]

    def repack(self, mutate):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for name, data in self.payload.items():
                if name != "manifest.json":
                    (directory / name).write_bytes(data)
            mutate(directory)
            return package_files(directory, self.metadata)

    @staticmethod
    def change_csv(directory, name, mutate):
        path = directory / name
        reader = csv.DictReader(io.StringIO(path.read_text()))
        fields, rows = reader.fieldnames, list(reader)
        mutate(rows)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_real_source_is_accounted_for(self):
        counts = validate(self.content)
        self.assertGreater(counts["organisms"], 5)
        self.assertGreater(counts["rules"], 0)
        self.assertGreater(counts["supportedRules"], 0)
        self.assertGreaterEqual(counts["vectors"], counts["rules"] * 2)
        self.assertEqual(
            set(self.metadata["scope"]["guidelines"]),
            {
                x["guideline"]
                for x in json.loads(self.payload["manifest.json"])["standards"]
            },
        )

    def test_non_meningitis_does_not_replace_unspecified_context(self):
        if self.metadata["packageVersion"] == "2026.1":
            self.skipTest("Legacy artifact predates explicit indication")
        rules = list(csv.DictReader(io.StringIO(self.payload["breakpoints.csv"].decode())))
        if self.metadata["packageId"] != "amr-reference-eucast":
            self.skipTest("EUCAST clinical context regression")
        for agent in ("CRO", "CIP", "MEM"):
            selected = [r for r in rules if r["organism_code"] == "B_ESCHR_COLI"
                        and r["antibiotic_code"] == agent and r["method"] == "MIC"
                        and r["version"].startswith("2026-")]
            by_context = {r["clinical_context"]: r for r in selected}
            self.assertEqual("", by_context[""]["susceptible_value"])
            self.assertTrue(by_context["NON_MENINGITIS"]["susceptible_value"])
            self.assertTrue(by_context["NON_MENINGITIS"]["resistant_value"])

    def rules(self):
        rules = list(csv.DictReader(io.StringIO(self.payload["breakpoints.csv"].decode())))
        if "disk_content" not in rules[0]:
            self.skipTest("Legacy artifact predates disk content")
        return rules

    def test_supported_zone_rules_name_their_disk_content(self):
        zones = [r for r in self.rules() if r["method"] == "ZONE" and r["susceptible_value"]]
        self.assertTrue(zones, "No zone rule is interpretable")
        self.assertTrue(all(r["disk_content"] for r in zones))
        self.assertTrue(all(not r["disk_content"] for r in self.rules() if r["method"] == "MIC"))
        ceftriaxone = [r for r in zones if r["organism_code"] == "B_ESCHR_COLI"
                       and r["antibiotic_code"] == "CRO" and r["version"].startswith("2026-")]
        self.assertEqual(1, len(ceftriaxone))

    def test_zone_rule_without_disk_content_is_rejected(self):
        self.rules()

        def mutate(rows):
            next(r for r in rows if r["method"] == "ZONE" and r["susceptible_value"])["disk_content"] = ""

        content = self.repack(lambda d: self.change_csv(d, "breakpoints.csv", mutate))
        with self.assertRaisesRegex(ValueError, "Zone rule without disk content"):
            validate(content)

    def test_organisms_carry_their_breakpoint_group(self):
        organisms = {r["code"]: r for r in csv.DictReader(io.StringIO(self.payload["organisms.csv"].decode()))}
        if "organism_group" not in next(iter(organisms.values())):
            self.skipTest("Legacy artifact predates organism group")
        self.assertEqual("Enterobacterales", organisms["B_ESCHR_COLI"]["organism_group"])

    def organisms(self):
        organisms = {r["code"]: r for r in csv.DictReader(io.StringIO(self.payload["organisms.csv"].decode()))}
        if "whonetName" not in next(iter(organisms.values())):
            self.skipTest("Legacy artifact predates WHONET names")
        return organisms

    def test_genus_entries_carry_the_whonet_code_and_name(self):
        organisms = self.organisms()
        for code, whonet, name in (
            ("B_KLBSL", "kl-", "Klebsiella sp."),
            ("B_ESCHR", "esc", "Escherichia sp."),
            ("B_STPHY", "sta", "Staphylococcus sp."),
        ):
            with self.subTest(code=code):
                self.assertEqual("genus", organisms[code]["rank"])
                self.assertEqual(whonet, organisms[code]["whonetCode"])
                self.assertEqual(name, organisms[code]["whonetName"])
        self.assertEqual("eco", organisms["B_ESCHR_COLI"]["whonetCode"])
        self.assertEqual("Escherichia coli", organisms["B_ESCHR_COLI"]["whonetName"])
        # WHONET's only genus label here is a diphtheroid subset, so no code is guessed.
        self.assertEqual("", organisms["B_CRYNB"]["whonetCode"])

    def test_every_accepted_organism_is_listed_but_rules_stay_in_scope(self):
        organisms = self.organisms()
        if self.metadata["scope"].get("organismList") != "accepted-taxa":
            self.skipTest("Rule-scope organism list")
        self.assertGreater(len(organisms), 50000)
        ruled = {r["organism_code"] for r in self.rules()}
        self.assertLess(len(ruled), 1000)
        self.assertTrue(ruled <= set(organisms))
        # A rare species beneath a breakpoint genus is listed with its group and no rule of its own.
        rare = next(r for r in organisms.values() if r["rank"] == "species"
                    and r["prevalence"] == "2" and r["organism_group"] and r["code"] not in ruled)
        self.assertTrue(rare["displayName"])

    def test_two_organisms_cannot_share_a_whonet_code(self):
        self.organisms()

        def mutate(rows):
            coded = [r for r in rows if r["whonetCode"]]
            coded[1]["whonetCode"] = coded[0]["whonetCode"]

        content = self.repack(lambda d: self.change_csv(d, "organisms.csv", mutate))
        with self.assertRaisesRegex(ValueError, "names two organisms"):
            validate(content)

    def test_zip_is_reproducible_despite_file_times_and_order(self):
        rebuilt = self.repack(
            lambda directory: [
                os.utime(path, (12345, 12345))
                for path in reversed(list(directory.iterdir()))
            ]
        )
        self.assertEqual(self.content, rebuilt)

    def test_strict_boundary_cannot_become_inclusive(self):
        def mutate(rows):
            rows[0]["resistant_inclusive"] = "false" if rows[0]["resistant_inclusive"] == "true" else "true"

        content = self.repack(lambda d: self.change_csv(d, "breakpoints.csv", mutate))
        with self.assertRaisesRegex(ValueError, "Publisher resistant"):
            validate(content)

    def test_wrong_interpretation_fails_even_with_valid_checksums(self):
        def mutate(rows):
            row = next(row for row in rows if row["expected"] in {"S", "I", "R"})
            row["expected"] = "R" if row["expected"] == "S" else "S"

        content = self.repack(
            lambda d: self.change_csv(d, "interpretation-vectors.csv", mutate)
        )
        with self.assertRaisesRegex(ValueError, "differs from translated"):
            validate(content)

    def test_unsupported_context_cannot_gain_a_numeric_rule(self):
        def mutate(rows):
            row = next(row for row in rows if row["status"] == "SUPPORTED")
            row.update(status="BLOCKED", reason="conditional_note_requires_review")

        content = self.repack(lambda d: self.change_csv(d, "coverage.csv", mutate))
        with self.assertRaisesRegex(ValueError, "Blocked context has a numeric rule"):
            validate(content)

    def test_missing_boundary_is_rejected(self):
        def mutate(directory):
            def remove_boundary(rows):
                first = rows[0]
                # Removing a whole rule's vectors must fail despite intact rule and coverage files.
                rows[:] = [
                    row
                    for row in rows
                    if any(
                        row[key] != first[key]
                        for key in (
                            "version",
                            "organism_code",
                            "antibiotic_code",
                            "method",
                        )
                    )
                ]

            self.change_csv(directory, "interpretation-vectors.csv", remove_boundary)

        with self.assertRaisesRegex(ValueError, "Missing boundary"):
            validate(self.repack(mutate))

    def test_duplicate_concepts_fail_before_loading(self):
        content = self.repack(
            lambda d: self.change_csv(
                d, "organisms.csv", lambda rows: rows.append(rows[0])
            )
        )
        with self.assertRaisesRegex(ValueError, "Organism inventory"):
            validate(content)

    def test_unaccounted_source_row_fails(self):
        def mutate(rows):
            next(row for row in rows if row["status"] == "SEE_COVERAGE")[
                "status"
            ] = "OUTSIDE_ORGANISM_SCOPE"

        content = self.repack(lambda d: self.change_csv(d, "source-rows.csv", mutate))
        with self.assertRaisesRegex(ValueError, "Incomplete source"):
            validate(content)

    def test_path_or_duplicate_entries_fail(self):
        for extra in ("../outside", archive_path("organisms.csv")):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                for name, data in self.payload.items():
                    archive.writestr(archive_path(name) if name != "manifest.json" else name, data)
                archive.writestr(extra, b"extra")
            with self.subTest(extra=extra), self.assertRaisesRegex(
                ValueError, "inventory"
            ):
                validate(output.getvalue())

    def test_tampering_is_detected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, data in self.payload.items():
                archive.writestr(
                    archive_path(name) if name != "manifest.json" else name, data + b"\n" if name == "breakpoints.csv" else data
                )
        with self.assertRaisesRegex(ValueError, "Checksum"):
            validate(output.getvalue())


if __name__ == "__main__":
    unittest.main()
