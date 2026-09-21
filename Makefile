.PHONY: install lint typecheck test run docker compose clean migrate

install:
	pip install -e ".[dev]"

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
