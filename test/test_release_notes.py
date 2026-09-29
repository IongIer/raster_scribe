"""Release notes must match the packaged version and preserve Markdown."""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release-notes.py"
SPEC = importlib.util.spec_from_file_location("release_notes", SCRIPT)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)

METADATA = "[general]\nversion=0.1.1\n"
NOTES = "Fix tracing.\n\n### Details\n\n- Preserve **Markdown** and [links](https://example.org)."
CHANGELOG = f"""# Changelog

## Unreleased

Future work.

## 0.1.1 — 2026-09-29

{NOTES}

## 0.1.0 — 2026-09-26

First release.
"""


class ReleaseNotesTest(unittest.TestCase):
    def test_matching_section_preserves_markdown(self):
        for tag in (None, "scribe-v0.1.1"):
            with self.subTest(tag=tag):
                self.assertEqual(
                    RELEASE.release_notes(METADATA, CHANGELOG, tag), NOTES + "\n"
                )

    def test_last_section(self):
        metadata = "[general]\nversion=0.1.0\n"
        self.assertEqual(RELEASE.release_notes(metadata, CHANGELOG), "First release.\n")

    def test_tag_must_match_metadata(self):
        for tag in ("scribe-v0.1.0", "v0.1.1", "scribe-v0.1.1-rc1"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "Tag"):
                RELEASE.release_notes(METADATA, CHANGELOG, tag)

    def test_metadata_requires_release_version(self):
        for version in ("0.1", "0.1.1-rc1", "../0.1.1"):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "X.Y.Z"),
            ):
                RELEASE.release_notes(f"[general]\nversion={version}\n", CHANGELOG)

    def test_missing_empty_or_duplicate_entry_fails(self):
        for changelog in (
            CHANGELOG.replace("## 0.1.1 —", "## 0.1.10 —"),
            CHANGELOG.replace(NOTES, ""),
            CHANGELOG + "\n## 0.1.1 — 2026-09-30\n\nDuplicate.\n",
        ):
            with (
                self.subTest(changelog=changelog),
                self.assertRaisesRegex(ValueError, "nonempty CHANGELOG.md entry"),
            ):
                RELEASE.release_notes(METADATA, changelog)
