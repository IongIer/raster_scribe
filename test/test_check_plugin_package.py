"""Package scanner regressions with subprocess results supplied by fixtures."""

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-plugin-package.py"
SPEC = importlib.util.spec_from_file_location("check_plugin_package", SCRIPT)
CHECK_PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK_PACKAGE)

KNOWN_FINDING = {
    "type": "Secret Keyword",
    "hashed_secret": "a" * 40,
    "line_number": 1,
}
KNOWN_RESULTS = {"example.py": [KNOWN_FINDING]}


class PackageScanTest(unittest.TestCase):
    def scan_package(self, results, baseline=None, scan_stdout=None, scan_status=0):
        stdout, stderr = io.StringIO(), io.StringIO()

        def run(command, **kwargs):
            if command[0] == "bandit":
                return subprocess.CompletedProcess(command, 0, '{"results": []}', "")
            if command[0] == "detect-secrets":
                output = json.dumps({"results": results})
                if scan_stdout is not None:
                    output = scan_stdout
                # Model the CLI's baseline-update mode: it emits no JSON.
                if "--baseline" in command:
                    output = ""
                return subprocess.CompletedProcess(
                    command, scan_status, output, "scan error" if scan_status else ""
                )
            self.fail(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("raster_scribe/metadata.txt", "[general]\nname=Test\n")
                archive.writestr("raster_scribe/example.py", "# Test fixture\n")
                if baseline is not None:
                    archive.writestr("raster_scribe/.secrets.baseline", baseline)
            with (
                patch.object(CHECK_PACKAGE.sys, "argv", [str(SCRIPT), str(package)]),
                patch.object(CHECK_PACKAGE.subprocess, "run", side_effect=run),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                try:
                    CHECK_PACKAGE.main()
                except SystemExit as error:
                    return error.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def test_clean_scan_with_and_without_baseline(self):
        for baseline in (None, '{"results": {}}'):
            with self.subTest(baseline=baseline):
                code, stdout, stderr = self.scan_package({}, baseline)
                self.assertEqual(code, 0)
                self.assertIn("preflight passed", stdout)
                self.assertEqual(stderr, "")

    def test_known_finding_can_move_to_another_line(self):
        code, stdout, stderr = self.scan_package(
            {"example.py": [{**KNOWN_FINDING, "line_number": 42}]},
            json.dumps({"results": KNOWN_RESULTS}),
        )
        self.assertEqual(code, 0)
        self.assertIn("preflight passed", stdout)
        self.assertEqual(stderr, "")

    def test_unlisted_findings_fail_with_and_without_baseline(self):
        for baseline in (None, {"results": {}}, {"results": KNOWN_RESULTS}):
            with self.subTest(baseline=baseline):
                code, _, stderr = self.scan_package(
                    {
                        "example.py": [
                            KNOWN_FINDING,
                            {
                                **KNOWN_FINDING,
                                "hashed_secret": "b" * 40,
                                "line_number": 2,
                            },
                        ]
                    },
                    json.dumps(baseline) if baseline is not None else None,
                )
                self.assertEqual(code, 1)
                self.assertIn("Potential secret in example.py:2", stderr)
                if baseline == {"results": KNOWN_RESULTS}:
                    self.assertNotIn("Potential secret in example.py:1", stderr)

    def test_baseline_does_not_accept_different_file_or_type(self):
        for filename, finding in (
            ("other.py", KNOWN_FINDING),
            ("example.py", {**KNOWN_FINDING, "type": "Private Key"}),
        ):
            with self.subTest(filename=filename, finding=finding):
                code, _, stderr = self.scan_package(
                    {filename: [finding]}, json.dumps({"results": KNOWN_RESULTS})
                )
                self.assertEqual(code, 1)
                self.assertIn(f"Potential secret in {filename}:1", stderr)

    def test_invalid_baseline_fails(self):
        for baseline in ("not JSON", "{}", '{"results": []}'):
            with self.subTest(baseline=baseline):
                code, _, _ = self.scan_package({}, baseline)
                self.assertIn("Could not read detect-secrets baseline", code)

    def test_failed_scanner_and_invalid_output_fail(self):
        for options, message in (
            ({"scan_status": 1}, "detect-secrets failed: scan error"),
            ({"scan_stdout": ""}, "Could not parse detect-secrets output"),
            ({"scan_stdout": "{}"}, "Could not parse detect-secrets output"),
            ({"scan_stdout": "[]"}, "Could not parse detect-secrets output"),
        ):
            with self.subTest(options=options):
                code, _, _ = self.scan_package({}, **options)
                self.assertIn(message, code)

    def test_bandit_severity_policy(self):
        plugin = Path("/plugin")
        findings = [
            {
                "filename": str(plugin / "example.py"),
                "test_id": "B101",
                "issue_severity": severity,
                "line_number": index,
                "issue_text": "Test finding",
            }
            for index, severity in enumerate(("LOW", "MEDIUM", "HIGH"), start=1)
        ]
        with (
            patch.object(CHECK_PACKAGE, "run_json", return_value={"results": findings}),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            problems = CHECK_PACKAGE.check_bandit(plugin)
        self.assertEqual(len(problems), 2)
        self.assertIn("MEDIUM example.py:2", problems[0])
        self.assertIn("HIGH example.py:3", problems[1])
        self.assertIn("Advisory: Bandit B101 LOW example.py:1", stdout.getvalue())

    def test_bandit_incomplete_scan_fails(self):
        for report in (
            {"results": [], "errors": [{"reason": "syntax error"}]},
            {"results": {}},
        ):
            with (
                self.subTest(report=report),
                patch.object(CHECK_PACKAGE, "run_json", return_value=report),
                self.assertRaises(ValueError),
            ):
                CHECK_PACKAGE.check_bandit(Path("/plugin"))

    def test_invalid_archives_fail_before_extraction(self):
        for names in (
            [],
            ["."],
            ["raster_scribe/../../outside.txt"],
            ["/outside.txt"],
            ["raster_scribe\\..\\outside.txt"],
            ["other/metadata.txt"],
            ["raster_scribe/example.py"],
        ):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                package = Path(directory) / "plugin.zip"
                with zipfile.ZipFile(package, "w") as archive:
                    for name in names:
                        archive.writestr(name, "fixture")
                with self.assertRaises(ValueError):
                    CHECK_PACKAGE.extract_package(package, Path(directory) / "unpacked")
                self.assertFalse((Path(directory) / "outside.txt").exists())
