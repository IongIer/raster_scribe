"""Check a built ZIP using this repository's release preflight policy."""

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def run_json(command, *, cwd=None, allowed_status=(0,)):
    result = subprocess.run(
        command, cwd=cwd, check=False, capture_output=True, text=True
    )
    if result.returncode not in allowed_status:
        raise ValueError(f"{command[0]} failed: {result.stderr}")
    try:
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or "results" not in report:
            raise ValueError("missing results")
        return report
    except ValueError as error:
        raise ValueError(f"Could not parse {command[0]} output: {error}") from error


def extract_package(package, destination):
    if package.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Plugin ZIP exceeds QGIS's 25 MB package limit")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if not names or any(
            not PurePosixPath(name).parts
            or name.startswith("/")
            or ".." in PurePosixPath(name).parts
            or "\\" in name
            for name in names
        ):
            raise ValueError("ZIP is empty or contains an unsafe path")
        roots = {PurePosixPath(name).parts[0] for name in names}
        if roots != {"raster_scribe"}:
            raise ValueError("Expected one raster_scribe/ root directory in ZIP")
        archive.extractall(destination)
    plugin = destination / "raster_scribe"
    if not (plugin / "metadata.txt").is_file():
        raise ValueError("Package is missing raster_scribe/metadata.txt")
    return plugin


def check_bandit(plugin):
    report = run_json(
        ["bandit", "-r", str(plugin), "-f", "json"], allowed_status=(0, 1)
    )
    if not isinstance(report["results"], list):
        raise ValueError("Could not parse Bandit results")
    if report.get("errors"):
        raise ValueError(f"Bandit could not scan all files: {report['errors']}")
    problems = []
    # Local policy; QGIS applies its own rule database after submission.
    for issue in report["results"]:
        filename = Path(issue["filename"]).relative_to(plugin)
        description = (
            f"Bandit {issue['test_id']} {issue['issue_severity']} "
            f"{filename}:{issue['line_number']}: {issue['issue_text']}"
        )
        if issue["issue_severity"].upper() in {"HIGH", "MEDIUM"}:
            problems.append(description)
        else:
            print(f"Advisory: {description}")
    return problems


def check_secrets(plugin):
    baseline = plugin / ".secrets.baseline"
    known_secrets = set()
    if baseline.is_file():
        try:
            baseline_report = json.loads(baseline.read_text(encoding="utf-8"))
            known_secrets = {
                (filename, finding["type"], finding["hashed_secret"])
                for filename, findings in baseline_report["results"].items()
                for finding in findings
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(
                f"Could not read detect-secrets baseline: {error}"
            ) from error
    # --baseline updates the file and emits no JSON. Match finding identities
    # ourselves, so moving an acknowledged finding to another line is harmless.
    report = run_json(
        [
            "detect-secrets",
            "scan",
            "--all-files",
            "--exclude-files",
            r"(^|/)metadata\.txt$|(^|/)\.secrets\.baseline$",
            ".",
        ],
        cwd=plugin,
    )
    return [
        f"Potential secret in {filename}:{finding['line_number']} ({finding['type']})"
        for filename, findings in report["results"].items()
        for finding in findings
        if (filename, finding["type"], finding["hashed_secret"]) not in known_secrets
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Built plugin ZIP")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory() as directory:
            plugin = extract_package(args.package, Path(directory))
            problems = check_bandit(plugin) + check_secrets(plugin)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        zipfile.BadZipFile,
    ) as error:
        sys.exit(str(error))
    if problems:
        print("Release preflight failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        sys.exit(1)
    print("Release preflight passed")


if __name__ == "__main__":
    main()
