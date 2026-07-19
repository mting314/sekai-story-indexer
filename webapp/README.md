# Sekai Story Indexer — web app

Lightweight chat + event-timeline UI. Vanilla JS front end (no build step) over a
small FastAPI backend.

* **Timeline** reads `events_index.json` (from `indexer fetch`) — event logos,
  unit filter chips, dates, community nickname (`kasa5`), focus character, and
  commissioned song. Images stream from the sekai.best CDN.
* **Chat** posts to `/api/query`, which calls the RAG engine. If the index isn't
  built / keys aren't set, it returns a clear message and the timeline still
  works.
* Clicking an event scopes your next question to it.
* **Grounded answers**: the chat quotes lines from the story and lists sources.
  Click any quote or source `[n]` to open a **side panel with the full scene
  excerpt** (from `citations[].excerpt` in the query response) — verify without
  leaving the chat.
* Each event shows an **indexed indicator**: a filled green dot = queryable in
  chat now; a hollow dot + dimmed card = on the timeline but not yet ingested
  (chat-answerable after the next `scripts/sync.sh`). A legend at the top counts
  each. Clicking a pending event explains it's not queryable yet instead of
  scoping. This reflects the timeline-leads-index design (see `../DESIGN.md`).

## Run

```bash
uv sync --extra web
uv run uvicorn webapp.server:app --reload
# open http://127.0.0.1:8000
```

Point it at an events index explicitly with:

```bash
SEKAI_EVENTS_INDEX=events_index.json uv run uvicorn webapp.server:app
```

### Preview the UI before fetching real data

```bash
SEKAI_EVENTS_INDEX=webapp/sample_events_index.json uv run uvicorn webapp.server:app
```

(The sample has 3 events so you can see the timeline/filter/nickname layout.
Chat will report the index isn't built — expected until you run `indexer ingest`.)

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/` | single-page UI |
| GET | `/api/units` | unit slugs + display names |
| GET | `/api/events` | timeline rows from the events index |
| POST | `/api/query` | `{question, unit?, event_id?}` → RAG answer |

## Notes / next
* Unit/event **scoping** of chat (`unit`, `event_id` in the request) is plumbed
  through but becomes an actual retrieval filter in Phase 4.
* Character icons aren't shown yet; event logos cover the main visual need.
  Per-character icons can be added from the master-DB card art or bundled assets.
