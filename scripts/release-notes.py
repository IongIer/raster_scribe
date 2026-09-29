"""Validate the release version and print its CHANGELOG.md entry."""

import argparse
import configparser
import re
from pathlib import Path


def release_notes(metadata_text, changelog, tag=None):
    metadata = configparser.ConfigParser(interpolation=None)
    metadata.read_string(metadata_text)
    version = metadata["general"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Expected X.Y.Z version in metadata.txt, found {version!r}")
    expected_tag = f"scribe-v{version}"
    if tag is not None and tag != expected_tag:
        raise ValueError(f"Tag {tag!r} does not match metadata.txt: {expected_tag}")

    sections = re.split(r"^## ", changelog, flags=re.MULTILINE)[1:]
    entries = []
    for section in sections:
        heading, _, body = section.partition("\n")
        if re.fullmatch(rf"{re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}", heading):
            entries.append(body.strip())
    if len(entries) != 1 or not entries[0]:
        raise ValueError(f"Expected one nonempty CHANGELOG.md entry for {version}")
    return entries[0] + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="Expected tag: scribe-vX.Y.Z")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        notes = release_notes(
            (root / "metadata.txt").read_text(encoding="utf-8"),
            (root / "CHANGELOG.md").read_text(encoding="utf-8"),
            args.tag or None,
        )
    except (OSError, ValueError, KeyError, configparser.Error) as error:
        parser.exit(1, f"Release notes: {error}\n")
    print(notes, end="")


if __name__ == "__main__":
    main()
