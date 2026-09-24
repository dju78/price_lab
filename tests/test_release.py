"""Release 1.0.0: one version everywhere, and release documents that stand
on what they cite."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import pricelab

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
# The Docker image copies the code, the tests and the fixture, not the
# documents (.dockerignore and the Dockerfile's COPY lines); the document
# checks run in the repository and in CI, and say why they skip in the image.
needs_docs = pytest.mark.skipif(not (DOCS / "methodology").is_dir(),
                                reason="the release documents are not shipped in the image")


def test_the_package_and_the_project_state_one_version():
    """`pricelab.__version__` is what every provenance stamp records; it and
    pyproject.toml disagreed (0.1.0 against 0.2.0) until the release."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert pricelab.__version__ == project["version"] == "1.0.0"


@needs_docs
def test_the_changelog_has_an_entry_for_the_version():
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text("utf-8")
    assert f"## {pricelab.__version__} " in changelog


@needs_docs
def test_the_methodology_statement_cites_every_note_and_every_link_resolves():
    statement = (DOCS / "methodology_statement.md").read_text("utf-8")
    cited = set(re.findall(r"\]\((methodology/[a-z_]+\.md)\)", statement))
    notes = {f"methodology/{p.name}" for p in (DOCS / "methodology").glob("*.md")
             if p.name != "README.md"}
    assert cited == notes, (sorted(notes - cited), sorted(cited - notes))
    for link in cited:
        assert (DOCS / link).exists(), link


@needs_docs
def test_the_limitations_register_carries_every_phase_s_deferrals():
    """Every phase whose backlog entry defers something is named in the
    register, and each row says where it arose, why and what it means."""
    register = (DOCS / "limitations_register.md").read_text("utf-8")
    rows = [line for line in register.splitlines() if re.match(r"\| [ABC]\d+ \|", line)]
    assert len(rows) >= 40
    assert all(line.count("|") >= 6 for line in rows)
    arose = " ".join(line.split("|")[3] for line in rows)
    for phase in ("1", "2", "3", "4", "5", "6", "7a", "7b", "8", "9a", "9b", "10",
                  "quantity ingestion", "release"):
        assert re.search(rf"(^|[ ,]){re.escape(phase)}($|[ ,])", arose), phase
    for group in ("## A. Affects a published number", "## B. Affects only an analyst view",
                  "## C. Operational"):
        assert group in register
