// Bundled Sekai catalog accessors (import JSON, build O(1) Maps). These are plain
// functions (named use*) returning module-level constants — not React hooks.
import songsData from '../../data/songs.json';
import unitsData from '../../data/units.json';
import type { Song, UnitMeta } from '~/types/sekai';

const songs = songsData as unknown as Song[];
const units = unitsData as unknown as UnitMeta[];

const songById = new Map<string, Song>(songs.map((s) => [s.id, s]));
const unitById = new Map<string, UnitMeta>(units.map((u) => [u.id, u]));

export const useSongs = () => songs;
export const useSongById = () => songById;
export const useUnits = () => units;

// Song display name — JP title primary, official/romanized name as a fallback, then id.
export const songName = (id: string) => {
  const s = songById.get(id);
  return s?.title ?? s?.englishName ?? id;
};

export const unitName = (id: string) => unitById.get(id)?.name ?? id;
export const unitColor = (id: string) => unitById.get(id)?.color ?? '#8a8a8a';

// Unit hex colors for a song's left color bar. A no-unit song falls back to the
// "Other" color; multi-unit songs get every involved color (segmented bar).
export function songColors(id: string): string[] {
  const s = songById.get(id);
  const cs = (s?.units ?? []).map((u) => unitColor(u));
  return cs.length ? cs : [unitColor('other')];
}

// CSS background for a left color bar: a single color, or equal hard-stop segments
// (top→bottom) when a song spans multiple units.
export function colorBarBackground(colors: string[]): string {
  if (colors.length <= 1) return colors[0] ?? '#8a8a8a';
  const n = colors.length;
  const stops = colors
    .map((c, i) => `${c} ${((i * 100) / n).toFixed(2)}% ${(((i + 1) * 100) / n).toFixed(2)}%`)
    .join(', ');
  return `linear-gradient(to bottom, ${stops})`;
}
