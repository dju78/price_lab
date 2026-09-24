.PHONY: install lint typecheck test run docker compose clean migrate lock requirements

# The locked versions (requirements.lock), exactly what CI and the image use.
install:
	python -m pip install -r requirements.lock
	python -m pip install -e . --no-deps

# Recompile the lock from pyproject.toml against current upstream (needs uv:
# python -m pip install uv), then the runtime requirements.txt from it. Commit
# both only after the suite passes.
lock:
	uv pip compile pyproject.toml --all-extras --universal --python-version 3.11 --upgrade --no-emit-package pricelab --output-file requirements.lock
	$(MAKE) requirements

# requirements.txt: the runtime dependencies only (no test, lint or type-check
# tools), at exactly the lock's versions. Streamlit Community Cloud installs
# from it. Generated; never edited by hand.
requirements:
	uv pip compile pyproject.toml --universal --python-version 3.11 --constraint requirements.lock --no-emit-package pricelab --custom-compile-command "make requirements (generated from pyproject.toml, pinned to requirements.lock; never edit by hand)" --output-file requirements.txt

lint:
	ruff check .

typecheck:
	mypy

test:
	python -m pytest tests/ -q

# The engine is where the index arithmetic lives, so it is the package
# with a coverage floor: 80 percent, enforced rather than reported, so a
# formula added without tests fails the build instead of quietly
# lowering the number.
coverage:
	python -m pytest tests/ -q --cov=pricelab/engine --cov-report=term-missing --cov-fail-under=80

migrate:
	alembic upgrade head

run: test
	streamlit run app.py

docker:
	docker build -t pricelab . && docker run -p 8501:8501 pricelab

compose:
	docker compose up --build

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
