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
    assert pricelab.__version__ == project["version"] == "1.0.3"


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


# ---------------------------------------------------------------------
# The lock: local, CI and the image resolve identically
# ---------------------------------------------------------------------
LOCK = REPO_ROOT / "requirements.lock"


def _locked(python: str = "3.12.14", platform: str = "linux",
            path: Path = LOCK) -> dict[str, str]:
    """name -> version for the entries of a pinned requirements file (the
    lock by default) that apply to the given interpreter and platform (CI's,
    by default)."""
    from packaging.markers import Marker
    from packaging.utils import canonicalize_name

    env = {"python_full_version": python, "python_version": python.rsplit(".", 1)[0],
           "sys_platform": platform, "platform_system": platform.capitalize(),
           "os_name": "posix" if platform == "linux" else "nt",
           "implementation_name": "cpython", "platform_python_implementation": "CPython"}
    pins: dict[str, str] = {}
    for line in path.read_text("utf-8").splitlines():
        if not line or line.startswith((" ", "#")):
            continue
        requirement, _, marker = line.partition(";")
        name, _, version = requirement.strip().partition("==")
        if marker.strip() and not Marker(marker.strip()).evaluate(env):
            continue
        pins[canonicalize_name(name)] = version.strip()
    return pins


def test_every_declared_dependency_is_locked_at_a_version_it_allows():
    """Compiled by uv from pyproject.toml, all extras, universal; every
    direct dependency pinned, and at a version its specifier accepts, on
    CI's interpreter (Python 3.12, Linux) and this machine's (3.14, Windows)
    alike -- the two resolve to the same versions."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    header = LOCK.read_text("utf-8").splitlines()[1]
    assert "uv pip compile pyproject.toml --all-extras --universal" in header
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    ci, local = _locked(), _locked("3.14.0", "win32")
    for spec in project["dependencies"] + project["optional-dependencies"]["dev"]:
        requirement = Requirement(spec)
        name = canonicalize_name(requirement.name)
        assert name in ci, f"{name} is declared but not in requirements.lock"
        assert requirement.specifier.contains(ci[name], prereleases=True), (name, ci[name])
        assert ci[name] == local.get(name), f"{name}: CI {ci[name]}, local {local.get(name)}"


def test_streamlit_is_pinned_to_one_minor_series():
    """The pages and their tests depend on Streamlit APIs that change between
    minor releases; 1.64 alone failed 25 tests. Pinned, with an upper bound."""
    from packaging.requirements import Requirement

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    streamlit = next(Requirement(d) for d in project["dependencies"]
                     if Requirement(d).name == "streamlit")
    assert any(s.operator == "<" for s in streamlit.specifier)
    assert _locked()["streamlit"].startswith("1.64.")


needs_ci_files = pytest.mark.skipif(not (REPO_ROOT / ".github").is_dir(),
                                    reason="the CI workflows are not shipped in the image")


@needs_ci_files
def test_ci_and_the_image_install_the_lock_not_a_fresh_resolution():
    tests_yml = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text("utf-8")
    assert tests_yml.count("pip install -r requirements.lock") == 2      # both jobs
    assert '".[dev]"' not in tests_yml
    dockerfile = (REPO_ROOT / "Dockerfile").read_text("utf-8")
    assert "pip install --no-cache-dir -r requirements.lock" in dockerfile
    assert '".[dev]"' not in dockerfile


@needs_ci_files
def test_a_scheduled_job_tries_current_upstream_on_purpose():
    refresh = (REPO_ROOT / ".github" / "workflows" / "lock-refresh.yml").read_text("utf-8")
    assert "schedule:" in refresh and "workflow_dispatch:" in refresh
    assert "--upgrade" in refresh                         # recompiles against upstream
    assert "pip install --upgrade streamlit" in refresh   # and probes past the pin


RUNTIME = REPO_ROOT / "requirements.txt"
DEVELOPMENT_ONLY = {"pytest", "pytest-cov", "hypothesis", "ruff", "mypy", "pre-commit",
                    "pandas-stubs", "types-tabulate", "types-requests", "responses", "uv",
                    "pip-tools"}


@pytest.mark.skipif(not RUNTIME.exists(), reason="requirements.txt is not shipped in the image")
def test_the_runtime_requirements_are_the_lock_s_runtime_subset():
    """requirements.txt is what Streamlit Community Cloud installs. It is
    generated from pyproject.toml's runtime dependencies at exactly the
    lock's versions, so the demonstration runs what CI tested, and it holds
    no test, lint or type-check tool."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    header = RUNTIME.read_text("utf-8").splitlines()[1]
    assert "make requirements" in header and "never edit by hand" in header
    locked = _locked()
    for python, platform in (("3.12.14", "linux"), ("3.13.1", "linux"), ("3.14.0", "win32")):
        runtime = _locked(python, platform, RUNTIME)
        lock = _locked(python, platform)
        drift = {n: (v, lock.get(n)) for n, v in runtime.items() if lock.get(n) != v}
        assert not drift, f"requirements.txt differs from requirements.lock: {drift}"
    runtime = _locked(path=RUNTIME)
    assert not DEVELOPMENT_ONLY & set(runtime), DEVELOPMENT_ONLY & set(runtime)
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    for spec in project["dependencies"]:
        name = canonicalize_name(Requirement(spec).name)
        assert runtime.get(name) == locked[name], name
