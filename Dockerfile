# Production image for the Sekai Story Indexer web app — local, keyless backend.
#
# Runs the FastAPI app with the dependency-light lexical engine: no GOOGLE_API_KEY,
# no Chroma, no external LLM. Only the handful of deps the `local` serve path needs
# are installed (NOT the chromadb/google-genai base deps), so the image stays small.
# The committed story corpus + index are baked in, so no `indexer fetch` is needed
# at runtime. See docs/deploy.md.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    SEKAI_QUERY_BACKEND=local \
    SEKAI_STORY_ROOT=/app/story \
    SEKAI_EVENTS_INDEX=/app/events_index.json \
    SEKAI_SUMMARIES_CACHE=/app/summaries_cache.json \
    SEKAI_EVENT_SUMMARIES=/app/event_summaries.json \
    PORT=8000

WORKDIR /app

# Only what the keyless local serve path imports (see CLAUDE.md: "the sekai paths
# need only typer + fastapi/uvicorn for serve; no chromadb"). certifi gives the
# live-timeline fetch a CA bundle on slim images; python-dotenv/pyyaml are used by
# the engine's optional paths.
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.13" \
    pyyaml python-dotenv certifi

# Source + committed corpus/data (self-contained — no fetch at runtime).
COPY src/ ./src/
COPY webapp/ ./webapp/
COPY story/ ./story/
COPY events_index.json story_order.yaml glossary.json summaries_cache.json event_summaries.json ./

# Drop privileges.
RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/api/units').read()" || exit 1

# $PORT is injected by most hosts (Cloud Run, Render, Railway, Fly); default 8000.
CMD ["sh", "-c", "uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
