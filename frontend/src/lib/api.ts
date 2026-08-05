// Typed client for the sekai-story-indexer FastAPI backend. All endpoints are same-origin
// (the app is served by that FastAPI app), so absolute `/api/...` paths resolve to the API
// regardless of the app's base path. Override with PUBLIC_HOST for standalone dev.
import type {
  ChildCounts, Citation, EventRow, Meta, QueryResult, SceneResult,
  SlashCommand, StreamEvent, TranscriptResult, UnitRow
} from '~/types/api';

const HOST = (import.meta.env?.PUBLIC_HOST as string | undefined) ?? '';
const url = (p: string) => `${HOST}${p}`;

const getJson = async <T>(p: string): Promise<T> => {
  const r = await fetch(url(p));
  if (!r.ok) throw new Error(`${p}: ${r.status}`);
  return r.json() as Promise<T>;
};

/* ---------- reads ---------- */
export const getUnits = () => getJson<UnitRow[]>('/api/units');
export const getEvents = () => getJson<EventRow[]>('/api/events');
export const getEventChildren = () => getJson<ChildCounts>('/api/event-children');
export const getCommands = () => getJson<SlashCommand[]>('/api/commands');
export const getMeta = () => getJson<Meta>('/static/meta.json');
export const getHierarchicalSummaries = () => getJson<Record<string, unknown>>('/api/hierarchical-summaries');

export const getEpisodeRaw = (arc: string, episode: string) =>
  getJson<TranscriptResult>(`/api/episode-raw?arc=${encodeURIComponent(arc)}&episode=${encodeURIComponent(episode)}`);

export const getScene = (arc: string, episode: string, q = '') =>
  getJson<SceneResult>(`/api/scene?arc=${encodeURIComponent(arc)}&episode=${encodeURIComponent(episode)}&q=${encodeURIComponent(q)}`);

/* ---------- session ---------- */
// Stable per-chat id so the server can keep sticky conversation focus. Persisted in
// localStorage; rotate it (rotateSession) to abandon server-side focus.
const SESSION_KEY = 'sekai_session_id';
export function sessionId(): string {
  try {
    let s = localStorage.getItem(SESSION_KEY);
    if (!s) {
      s = 's-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem(SESSION_KEY, s);
    }
    return s;
  } catch {
    return 's-ephemeral';
  }
}
export function rotateSession(): void {
  try { localStorage.removeItem(SESSION_KEY); } catch { /* ignore */ }
}

export interface QueryBody {
  question: string;
  unit: string | null;
  event_id: number | null;
  history: { role: string; text: string }[];
}

const withSession = (body: QueryBody) => ({ ...body, session_id: sessionId() });

/* ---------- query (non-streaming fallback) ---------- */
export async function query(body: QueryBody): Promise<QueryResult> {
  const r = await fetch(url('/api/query'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(withSession(body))
  });
  if (!r.ok) throw new Error(`/api/query: ${r.status}`);
  return r.json() as Promise<QueryResult>;
}

/* ---------- query (SSE stream) ---------- */
// Streams meta/delta/done frames (`data: {json}\n\n`, no SSE event names). Calls `onEvent`
// per frame; resolves with the final `done` result (or {answer:accumulatedText} if none).
export async function queryStream(
  body: QueryBody,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal
): Promise<QueryResult> {
  const r = await fetch(url('/api/query/stream'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(withSession(body)), signal
  });
  if (!r.ok || !r.body) throw new Error(`/api/query/stream: ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let done: QueryResult | null = null;
  let text = '';
  for (;;) {
    const { value, done: fin } = await reader.read();
    if (fin) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop() ?? ''; // keep incomplete tail
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      let evt: StreamEvent;
      try { evt = JSON.parse(line.slice(5).trim()) as StreamEvent; } catch { continue; }
      if (evt.type === 'delta') text += evt.text;
      if (evt.type === 'done') done = evt;
      onEvent(evt);
    }
  }
  // Defensive: process a trailing frame not terminated by \n\n (e.g. a proxy stripped the final
  // blank line), so the `done` payload (citations/focus) isn't silently dropped.
  if (!done) {
    const line = buf.split('\n').find((l) => l.startsWith('data:'));
    if (line) {
      try {
        const evt = JSON.parse(line.slice(5).trim()) as StreamEvent;
        if (evt.type === 'delta') text += evt.text;
        if (evt.type === 'done') done = evt;
        onEvent(evt);
      } catch { /* ignore malformed tail */ }
    }
  }
  return done ?? { answer: text };
}

/* ---------- slash command ---------- */
export async function command(cmd: string, unit: string | null): Promise<QueryResult> {
  const r = await fetch(url('/api/command'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: cmd, unit, session_id: sessionId() })
  });
  if (!r.ok) throw new Error(`/api/command: ${r.status}`);
  return r.json() as Promise<QueryResult>;
}

/* ---------- assets ---------- */
// Route external (sekai.best) art through the server image proxy; local /static passes through.
export const proxied = (u?: string): string =>
  u && /^https?:\/\//.test(u) ? url(`/api/img?u=${encodeURIComponent(u)}`) : (u ? url(u) : '');

export type { Citation };
