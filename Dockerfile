FROM python:3.12-slim

# Only what matplotlib needs at runtime. No system fonts beyond DejaVu, which
# ships with matplotlib, so rendering is identical on every host.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libexpat1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pricelab/ ./pricelab/
COPY tests/ ./tests/
COPY app.py ./
COPY .streamlit/ ./.streamlit/

# Fail the build rather than ship a broken image.
RUN python -m pytest tests/ -q

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
