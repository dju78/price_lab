"""Release 1.0.x: one version everywhere, and release documents that stand
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
    assert pricelab.__version__ == project["version"] == "1.0.2"


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


#: Import names whose distribution is declared under another name, or that
#: arrive with a declared distribution.
_DISTRIBUTION = {"argon2": "argon2-cffi", "docx": "python-docx", "pptx": "python-pptx",
                 "PIL": "pillow", "psycopg": "psycopg", "sdmx": "sdmx1"}


def test_every_third_party_import_is_a_declared_dependency():
    """1.0.0's first CI run failed at collection: `pypdf` was imported by the
    product and the tests but declared nowhere, and only the development
    environment happened to have it. Every import of a third-party module,
    at module level or inside a function, in the package, the pages, the app
    and the tests, must name a distribution in pyproject.toml."""
    import ast
    import sys

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    declared = {re.split(r"[<>=\[ ;!~]", d)[0].lower().replace("_", "-")
                for d in project["dependencies"] + project["optional-dependencies"]["dev"]}
    local = ({p.stem for p in (REPO_ROOT / "tests").glob("*.py")}
             | {"pricelab", "pages", "app", "dbtarget", "conftest"})
    files = [*(REPO_ROOT / "pricelab").rglob("*.py"), *(REPO_ROOT / "pages").glob("*.py"),
             *(REPO_ROOT / "tests").glob("*.py"), REPO_ROOT / "app.py"]
    undeclared: dict[str, set[str]] = {}
    for path in files:
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                if top in sys.stdlib_module_names or top in local:
                    continue
                dist = _DISTRIBUTION.get(top, top).lower().replace("_", "-")
                if dist not in declared:
                    undeclared.setdefault(dist, set()).add(path.relative_to(REPO_ROOT).as_posix())
    assert not undeclared, f"imported but not declared in pyproject.toml: {undeclared}"


def test_a_bare_pytest_can_import_the_pages_and_the_app():
    """CI runs `pytest`, not `python -m pytest`. Only the latter puts the
    repository root on sys.path, and `pages/` and `app.py` are not in the
    installed package, so 1.0.1's CI could not import `pages` at collection.
    The root is on pytest's own path; this keeps it there."""
    options = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    assert "." in options["tool"]["pytest"]["ini_options"]["pythonpath"]
