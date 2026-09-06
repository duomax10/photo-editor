"""The version is written in several places; they must agree.

The published download URL contains the version, and the workflow refuses to
publish when the release tag disagrees with ``__version__``. A stale copy in
pyproject.toml or the Windows version resource would ship an executable whose
reported version contradicts the file it came in.
"""

from __future__ import annotations

import re
from pathlib import Path

import photo_timestamp_editor

PROJECT = Path(__file__).resolve().parent.parent


def test_pyproject_matches_dunder_version():
    text = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    assert match.group(1) == photo_timestamp_editor.__version__


def test_windows_version_resource_matches_dunder_version():
    text = (PROJECT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    expected = photo_timestamp_editor.__version__
    tuple_form = "(" + ", ".join(expected.split(".")) + ", 0)"

    for field in ("filevers", "prodvers"):
        assert f"{field}={tuple_form}" in text, f"{field} should be {tuple_form}"

    for field in ("FileVersion", "ProductVersion"):
        match = re.search(rf'StringStruct\("{field}", "([^"]+)"\)', text)
        assert match, f"{field} is missing from the version resource"
        assert match.group(1) == f"{expected}.0"


def test_version_looks_like_a_release_tag():
    # The workflow derives the tag as "v" + this, so it has to be plain.
    assert re.fullmatch(r"\d+\.\d+\.\d+", photo_timestamp_editor.__version__)
