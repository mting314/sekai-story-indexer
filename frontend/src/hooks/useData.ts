// Bundled Sekai catalog accessors (import JSON, build O(1) Maps). These are plain
// functions (named use*) returning module-level constants — not React hooks.
import songsData from '../../data/songs.json';
import unitsData from '../../data/units.json';
import type { Song, UnitMeta } from '~/types/sekai';

const songs = songsData as unknown as Song[];
const units = unitsData as unknown as UnitMeta[];

const songById = new Map<string, Song>(songs.map((s) => [s.id, s]));
const unitById = new Map<string, UnitMeta>(units.map((u) => [u.id, u]));

export const useSongById = () => songById;

export const unitName = (id: string) => unitById.get(id)?.name ?? id;
export const unitColor = (id: string) => unitById.get(id)?.color ?? '#8a8a8a';
