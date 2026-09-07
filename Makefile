.PHONY: install test run docker clean

install:
	pip install -r requirements.txt

test:
	python -m pytest tests/ -q

run: test
	streamlit run app.py

docker:
	docker build -t pricelab . && docker run -p 8501:8501 pricelab

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
