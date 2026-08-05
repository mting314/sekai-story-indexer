// Song search/filter for the setlist builder. Sekai songs are grouped by UNIT; a song
// with no owning unit is the "Other" bucket. Pure functions over the bundled catalog.
import type { Song, UnitId } from '~/types/sekai';

export const NON_UNIT = 'other' as const;
export type UnitFilter = UnitId | typeof NON_UNIT;

// commissioned = written for Project Sekai; cover = a cover of an existing song.
export type SongKind = 'all' | 'commissioned' | 'cover';

export interface SongFilters {
  search: string;
  units: UnitFilter[]; // OR-ed; empty = all units
  kind: SongKind;
  yearFrom?: string;
  yearTo?: string;
}

export const EMPTY_SONG_FILTERS: SongFilters = { search: '', units: [], kind: 'all' };

const yearOf = (s: Song): string | undefined =>
  s.publishedAt ? new Date(s.publishedAt).getFullYear().toString() : undefined;

/** A song matches a unit filter value: a real unit id it carries, or 'other' when it has none. */
export function songMatchesUnit(song: Song, unit: UnitFilter): boolean {
  if (unit === NON_UNIT) return (song.units ?? []).length === 0;
  return (song.units ?? []).includes(unit);
}

export const songReleaseYears = (songs: Song[]): string[] =>
  [...new Set(songs.map(yearOf).filter((y): y is string => !!y))].sort();

export function filterSongs(songs: Song[], filters: SongFilters): Song[] {
  const q = filters.search.trim().toLowerCase();
  return songs.filter((song) => {
    if (q) {
      const hay = `${song.title} ${song.pronunciation ?? ''} ${song.englishName ?? ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (filters.units.length > 0 && !filters.units.some((u) => songMatchesUnit(song, u))) return false;
    if (filters.kind === 'commissioned' && !song.commissioned) return false;
    if (filters.kind === 'cover' && song.commissioned) return false;
    const year = yearOf(song);
    if (filters.yearFrom && (!year || year < filters.yearFrom)) return false;
    if (filters.yearTo && (!year || year > filters.yearTo)) return false;
    return true;
  });
}
