FROM python:3.12-slim

# Only what matplotlib needs at runtime. No system fonts beyond DejaVu, which
# ships with matplotlib, so rendering is identical on every host.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libexpat1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY pricelab/ ./pricelab/
COPY pages/ ./pages/
COPY migrations/ ./migrations/
COPY alembic.ini .
COPY tests/ ./tests/
COPY app.py ./
COPY .streamlit/ ./.streamlit/

# Fail the build rather than ship a broken image.
RUN ruff check .
RUN mypy
RUN python -m pytest tests/ -q

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    PRICELAB_ENVIRONMENT=production \
    PRICELAB_DATABASE_URL=sqlite:////data/pricelab.db

# A named volume mounted here (see docker-compose.yml) is what makes the
# audit log, the run registry and user accounts survive a container
# restart; without a mount this path is just the image's own writable
# layer, discarded with the container.
VOLUME ["/data"]

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
