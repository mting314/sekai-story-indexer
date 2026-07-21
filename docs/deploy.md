# Deploying the web app (public hosting)

The web app ships as a self-contained Docker image running the **local, keyless
backend** — the dependency-light lexical engine. No `GOOGLE_API_KEY`, no Chroma,
no external LLM; the committed story corpus + index are baked into the image, so
there's **no `indexer fetch` at runtime**.

> ⚠️ **Content note.** The corpus is SEGA / Colorful Palette's copyrighted Project
> Sekai story text (JP + official EN). A localhost tool is low-risk; **publicly**
> rehosting full transcripts is a different risk profile — that call is yours.

## Build & run locally

```bash
docker build -t sekai-story-indexer .
docker run --rm -p 8000:8000 sekai-story-indexer
# or:  docker compose up --build
open http://localhost:8000
```

The image binds `0.0.0.0:$PORT` (default 8000); every host below injects `$PORT`.

## What's in the image

- FastAPI app (`webapp.server:app`) on the `local` backend (`SEKAI_QUERY_BACKEND=local`)
- The committed `story/` corpus (JP + `.md.en`), `events_index.json`, summaries
- Only `fastapi`/`uvicorn`/`pydantic`/`pyyaml`/`python-dotenv`/`certifi` — **not**
  the heavy `chromadb`/`google-genai` base deps (the keyless path never imports them)

## Resource sizing

The engine builds an in-memory TF-IDF index over ~1,800 scenes + a ~125k-entry
JP→EN quote map on first use. Give it **≥1 GB RAM**; 1 vCPU is fine. Single worker
(the session focus + engine caches are per-process — don't scale to N workers
without a shared store).

## Deploy — pick a host

All of these consume the Dockerfile as-is. Run them from a normal terminal with
your account credentials (not from a restricted sandbox).

### Fly.io
```bash
fly launch --no-deploy         # generates fly.toml; set internal_port = 8000
fly deploy
```
In `fly.toml` set `[http_service] internal_port = 8000` and a `[[vm]] memory = "1gb"`.

### Render (Blueprint or dashboard)
New → Web Service → "Deploy from a Dockerfile". Instance type with ≥1 GB. Render
sets `$PORT` automatically. (Free tier sleeps on idle → cold starts.)

### Google Cloud Run
```bash
gcloud run deploy sekai-story-indexer \
  --source . --region us-central1 --allow-unauthenticated \
  --memory 1Gi --port 8000
```

### Railway
`railway init` → `railway up` (auto-detects the Dockerfile; sets `$PORT`).

## Optional: full (Gemini) backend later

To run the richer LLM backend instead, build with the base deps
(`uv sync` / add `chromadb`+`google-genai`), set `SEKAI_QUERY_BACKEND=full` and a
`GOOGLE_API_KEY` **secret** (never bake it into the image), and build the Chroma
index (`indexer ingest`). Add rate-limiting first — a public key-backed endpoint
can run up API cost.
