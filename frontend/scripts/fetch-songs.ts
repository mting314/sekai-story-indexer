// Build data/songs.json from the Project Sekai master DB.
//
//   bun run scripts/fetch-songs.ts
//
// Run where egress to sekai-world.github.io is allowed (the restricted Meta harness blocks it,
// same constraint as `indexer fetch`). Joins:
//   musics.json      -> id, title, pronunciation, assetbundleName, publishedAt
//   musicTags.json   -> song -> unit (the real ownership table; tag names differ from unit ids)
//   musicVocals.json -> song -> game-character ids (for icons)

export {}; // make this a module so top-level await is allowed

const MASTER = 'https://sekai-world.github.io/sekai-master-db-diff';

// musicTag string -> our unit id. A song with only unmapped tags (e.g. "other") gets units: [].
const TAG_TO_UNIT: Record<string, string> = {
  light_music_club: 'leo_need',
  idol: 'more_more_jump',
  street: 'vivid_bad_squad',
  theme_park: 'wonderlands_showtime',
  school_refusal: 'nightcord',
  vocaloid: 'virtual_singer'
};

interface Music { id: number; title: string; pronunciation?: string; assetbundleName?: string; publishedAt?: number; releasedAt?: number }
interface MusicTag { musicId: number; musicTag: string }
interface VocalChar { characterType: string; characterId: number }
interface MusicVocal { musicId: number; characters?: VocalChar[] }

const getJson = async <T>(name: string): Promise<T> => {
  const res = await fetch(`${MASTER}/${name}.json`);
  if (!res.ok) throw new Error(`fetch ${name}: ${res.status}`);
  return res.json() as Promise<T>;
};

const [musics, musicTags, musicVocals] = await Promise.all([
  getJson<Music[]>('musics'),
  getJson<MusicTag[]>('musicTags'),
  getJson<MusicVocal[]>('musicVocals')
]);

const tagsBySong = new Map<number, Set<string>>();
for (const t of musicTags) {
  const set = tagsBySong.get(t.musicId) ?? new Set<string>();
  set.add(t.musicTag);
  tagsBySong.set(t.musicId, set);
}

const charsBySong = new Map<number, Set<number>>();
for (const v of musicVocals) {
  const set = charsBySong.get(v.musicId) ?? new Set<number>();
  for (const c of v.characters ?? []) {
    if (c.characterType === 'game_character') set.add(c.characterId);
  }
  charsBySong.set(v.musicId, set);
}

const out = musics
  .map((m) => {
    const tags = [...(tagsBySong.get(m.id) ?? [])];
    const units = [...new Set(tags.map((t) => TAG_TO_UNIT[t]).filter(Boolean))];
    return {
      id: String(m.id),
      title: m.title,
      pronunciation: m.pronunciation ?? '',
      units,
      characters: [...(charsBySong.get(m.id) ?? [])].sort((a, b) => a - b),
      assetbundleName: m.assetbundleName ?? '',
      publishedAt: m.publishedAt ?? m.releasedAt ?? 0
    };
  })
  .sort((a, b) => (a.publishedAt ?? 0) - (b.publishedAt ?? 0));

await Bun.write('data/songs.json', JSON.stringify(out));
console.log(`wrote data/songs.json — ${out.length} songs`);
