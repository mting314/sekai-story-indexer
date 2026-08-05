# frontend — sekai-story-indexer web UI

The React + Vike + Panda CSS frontend for **sekai-story-indexer**. It replaced the old vanilla
`webapp/static/{index.html,app.js}` page (migrated in-place). Four surfaces:

- **Ask** — grounded chat: streaming answers (`/api/query/stream`), markdown + citations,
  slash commands, quick-actions, scope/focus chips, and a transcript/excerpt sidebar.
- **Timeline** — unit-filtered event cards (banner art, nested card/area children); click an
  indexed event to scope the Ask tab to it.
- **Summaries** — the hierarchical event→episode summary tree; episodes open in the sidebar.
- **Setlist** — the Project Sekai setlist builder (units, encore, localStorage slots, share URL).

## Architecture
Static bundle served by the FastAPI app at `/`; it calls the same `/api/*` endpoints (query
engine, Chroma, `/api/img` proxy) and reuses `/static/{meta.json,units,chara}`. The Python
backend is unchanged. Stack: React 19 · Vike (prerendered) · Panda CSS + Park UI · @dnd-kit ·
lz-string · Bun/Vite.

## Develop / build (needs a normal environment with network egress)
```bash
bun install                 # runs `panda codegen` (prepare) → styled-system/
bun run fetch-songs         # data/songs.json for the Setlist tab (Sekai master DB; needs egress)
bun run dev                 # standalone dev at http://localhost:5173
bun run build               # panda codegen && tsc && vike build → dist/client/
bun test src                # unit tests (setlist-items transforms)
```
For standalone `bun run dev`, set `PUBLIC_HOST=http://localhost:8000` (a running story-indexer)
so `/api/*`, the jacket proxy, and `/static` icons resolve.

## Serve via sekai-story-indexer
After `bun run build`, `dist/client/` is served at `/` automatically:
```bash
cd ..   # sekai-story-indexer
sekai serve --story-root sample/story --events-index sample/events_index.json
```
Override the served location with `SEKAI_FRONTEND_DIST=/abs/path/to/dist/client`.

## Layout
- `src/lib/api.ts` — typed client + SSE stream parser for every `/api/*` endpoint.
- `src/lib/store.tsx` — shared catalog (units/events/children/meta) + scope + tab state.
- `src/lib/{markdown,decorate,format,assets,share,storage}.ts` — render + helpers.
- `src/components/` — `AskTab`, `TimelineTab`, `SummariesTab`, `Sidebar`, `Markdown`,
  `SetlistBuilder`, and `ask/`, `timeline/`, `summaries/`, `setlist/` subtrees.
- `src/pages/index/+Page.tsx` — the tabbed shell (AppProvider + SidebarProvider).
