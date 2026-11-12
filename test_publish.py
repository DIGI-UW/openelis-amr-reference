"""Publication must preserve anonymous download visibility and immutable release tags."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import publish_package as publication


class PublicationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifact = Path(directory.name) / "package.zip"
        with zipfile.ZipFile(self.artifact, "w") as archive:
            archive.writestr("manifest.json", json.dumps({
                "packageId": "amr-reference-clsi", "packageVersion": "2026.1", "exporterSha256": {}}))
        self.destination = "ghcr.io/digi-uw/amr-reference-clsi"

    def test_publication_requires_an_existing_public_package(self):
        for visibility in (None, "private", "internal"):
            with patch.object(publication, "validate"), patch.object(publication, "package_visibility", return_value=visibility), patch.object(publication.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "public GHCR"):
                    publication.publish(self.artifact, self.destination)
                run.assert_not_called()

    def test_existing_version_cannot_be_replaced(self):
        with patch.object(publication, "validate"), patch.object(publication, "package_visibility", return_value="public"), patch.object(publication.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "sha256:existing", "")), patch.object(publication.subprocess, "check_output") as upload:
            with self.assertRaisesRegex(ValueError, "already published"):
                publication.publish(self.artifact, self.destination)
            upload.assert_not_called()

    def test_registry_error_is_not_treated_as_an_unused_tag(self):
        with patch.object(publication, "validate"), patch.object(publication, "package_visibility", return_value="public"), patch.object(publication.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "authentication required")), patch.object(publication.subprocess, "check_output") as upload:
            with self.assertRaisesRegex(RuntimeError, "Cannot confirm"):
                publication.publish(self.artifact, self.destination)
            upload.assert_not_called()

    def test_publisher_cannot_be_redirected_to_another_repository(self):
        with patch.object(publication, "validate"), patch.object(publication, "package_visibility") as visibility:
            with self.assertRaisesRegex(ValueError, "destination differs"):
                publication.publish(self.artifact, "ghcr.io/digi-uw/amr-reference-eucast")
            visibility.assert_not_called()

    def test_visibility_loss_after_upload_prevents_printing_a_pin(self):
        with patch.object(publication, "validate"), patch.object(publication, "package_visibility", side_effect=["public", "private"]), patch.object(publication.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "404 not found")), patch.object(publication.subprocess, "check_output", side_effect=["revision\n", '{"digest":"sha256:published"}']), patch("builtins.print") as output:
            with self.assertRaisesRegex(RuntimeError, "visibility verification"):
                publication.publish(self.artifact, self.destination)
            output.assert_not_called()
