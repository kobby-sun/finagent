# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 finagent
COPY requirements.lock pyproject.toml ./
RUN pip install -r requirements.lock
ARG INSTALL_EMBEDDINGS=true
COPY requirements-semantic.lock ./
RUN if [ "$INSTALL_EMBEDDINGS" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-semantic.lock; fi
COPY src ./src
COPY config ./config
COPY finance_rag_corpus ./finance_rag_corpus
COPY mock_data ./mock_data
COPY skills ./skills
COPY models ./models
COPY plugins ./plugins
COPY scripts ./scripts
COPY tests ./tests
COPY training ./training
RUN pip install --no-deps -e . && mkdir -p /data && chown -R finagent /data /app
USER finagent
ENV FINAGENT__APP__DATA_DIR=/data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
# ingest is idempotent: builds the index + vectors on first boot (or when the corpus changes)
CMD ["sh", "-c", "finagent ingest > /dev/null && finagent serve --host 0.0.0.0 --port 8080"]
