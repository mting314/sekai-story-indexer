// Project Sekai song/unit catalog types (event songs on Summaries, unit colors on Timeline).
// Song data is baked into data/songs.json by scripts/fetch-songs.ts (from the Sekai master DB).

// The six in-game units. A song belongs to zero-or-more of these (via musicTags);
// a song with no unit is the "Other" (commissioned / Vocaloid-only) category.
export type UnitId =
  | 'virtual_singer'
  | 'leo_need'
  | 'more_more_jump'
  | 'vivid_bad_squad'
  | 'wonderlands_showtime'
  | 'nightcord';

export interface Song {
  id: string; // musics.json id, stringified
  title: string; // JP title
  pronunciation?: string; // kana reading, used for search
  englishName?: string; // optional official/romanized name, when known
  units: UnitId[]; // derived from musicTags; [] = no owning unit ("Other")
  characters: number[]; // game-character ids from musicVocals (for icons)
  assetbundleName: string; // drives the jacket image URL
  commissioned: boolean; // true = written for Project Sekai; false = a cover of an existing song
  publishedAt?: number; // epoch ms (for year sort/filter)
}

// A unit (name + color), plus the synthetic 'other' bucket. Sourced from data/units.json.
export interface UnitMeta {
  id: UnitId | 'other';
  name: string;
  color: string;
}
