// Types for the sekai-story-indexer FastAPI contract (see webapp/server.py). The React
// frontend calls these same endpoints; the backend is unchanged by the migration.

export interface UnitRow { slug: string; name: string }

// A timeline event row from /api/events (EN-overlaid, live-cached). Only the fields the
// UI reads are typed; extra fields pass through.
export interface EventRow {
  event_id: number;
  arc_slug: string;
  name: string;
  name_jp?: string;
  nickname?: string;
  unit: string;
  indexed: boolean; // has story on disk / chat-answerable
  is_key_story?: boolean;
  started_at?: number;
  ended_at?: number;
  focus_character?: string;
  focus_character_id?: number;
  song_id?: number;
  song_title?: string;
  // image URLs (external sekai.best; route through proxied())
  story_banner_url?: string;
  banner_url?: string;
  jacket_url?: string;
  logo_url?: string;
  episode_titles_en?: Record<string, string>;
  regions?: Record<string, { startAt?: number; endAt?: number }>;
  [k: string]: unknown;
}

export type ChildCounts = Record<string, { cards: number; area_talks: number }>;

export interface SlashCommand { command: string; args: string; desc: string }

// The one result object shared by /api/query, /api/query/stream (`done`), and /api/command.
export interface AnswerPart { type: 'text' | 'quote'; text: string; text_en?: string; ref?: number }

export interface Citation {
  ref: number;
  arc_id: string;
  episode?: string;
  unit?: string;
  label?: string;
  nickname?: string;
  episode_title?: string;
  score?: number;
  excerpt?: string;
  quote?: string;
  quote_en?: string;
  source?: string;
}

export interface Scope {
  unit?: string | null;
  arc_id?: string;
  arc_ids?: string[];
  nickname?: string;
  label?: string;
}

export interface Focus {
  arcs: string[];
  character_id?: number | null;
  label?: string | null;
  unit?: string;
  nickname?: string;
}

export interface QueryResult {
  answer: string;
  answer_parts?: AnswerPart[];
  characters?: string[];
  citations?: Citation[];
  scope?: Scope;
  focus?: Focus | null;
  intent?: string;
  backend?: string;
  resolved_question?: string;
  generated?: boolean;
  error?: string | null;
  notice?: string;
  sections?: Record<string, string>;
  section_order?: string[];
  options?: { label: string }[];
}

// SSE frame types from /api/query/stream (discriminated by `type`, no SSE event names).
export type StreamEvent =
  | ({ type: 'meta' } & Partial<QueryResult>)
  | { type: 'delta'; text: string }
  | ({ type: 'done' } & QueryResult);

// Sidebar loaders
export interface TranscriptResult { title: string; text: string; region?: 'en' | 'jp' | null }
export interface SceneResult { title: string; text: string; quote?: string; region?: string }

// meta.json
export interface CharaMeta { jp?: string; en?: string; color?: string; unit?: string; icon?: string }
export interface UnitMetaEntry { name: string; color: string; symbol?: string }
export interface Meta { characters: Record<string, CharaMeta>; units: Record<string, UnitMetaEntry> }
