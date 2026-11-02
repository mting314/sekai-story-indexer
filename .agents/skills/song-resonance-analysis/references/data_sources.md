# Data Sources & Retrieval Guide

This document outlines how to locate, extract, and verify narrative and musical data within the `sekai-story-indexer` workspace and external repositories.

---

## 1. Event & Song Metadata Discovery

### `events_index.json`
Located at the root of the repository: `/Users/michaelting/github/sekai-story-indexer/events_index.json`.

Contains a JSON array of all game events with rich metadata:
- `event_id`: Integer event ID (e.g., `34`, `49`, `195`).
- `name`: Official Japanese event title (e.g., `"Knock the Future!!"`, `"Legend still vivid"`).
- `unit`: Unit identifier (`"leo_need"`, `"more_more_jump"`, `"vivid_bad_squad"`, `"wonderlands_showtime"`, `"nightcord"`).
- `arc_slug`: Folder slug formatted as `0034-knock-the-future`.
- `focus_character`: Kanji name of the focus character (e.g., `"星乃一歌"`, `"日野森志歩"`).
- `nickname`: Community standard shorthand (e.g., `"ichi1"`, `"shiho6"`, `"koha2"`).
- `song_id`: Associated music ID.
- `song_title`: Commissioned song title in Japanese.
- `song_composer`, `song_lyricist`, `song_arranger`: Creator credits.

#### Quick Extraction Commands:
```bash
# Find an event by number or slug:
python3 -c "
import json
with open('events_index.json') as f:
    events = json.load(f)
ev = next(e for e in events if e['event_id'] == 34)
print(ev['name'], ev['focus_character'], ev['song_title'], ev['song_composer'])
"
```

---

## 2. Story Scripts & Quotes Extraction

### Directory Structure
Event story transcripts are stored in:
`story/<unit>/event/<arc_slug>/`

Each folder contains:
- `01_<slug>.md` ... `08_<slug>.md`: Raw Japanese transcript with speaker tags.
- `01_<slug>.md.en` ... `08_<slug>.md.en`: Localized English translation.

### Fast Searching for Dialogue & Keywords
```bash
# Search for dialogue in an event's scripts:
grep -n "一歌" story/leo_need/event/0034-knock-the-future/*.md
```

### Event Summaries Cache
- `summaries_cache.json`: Contains high-level event overviews, episode-by-episode breakdowns, character trajectories, and continuity facts. Keyed by `EVENT|<arc_slug>`.

---

## 3. Song Lyrics, Translations & Vocal Allocations

### VocaDB API (Primary Endpoint for Lyrics & Translations)
Lyrics are fetched live via the public VocaDB API:
- Endpoint: `https://vocadb.net/api/songs`
- Query parameters:
  - `query`: Song title (e.g., `"流星のパルス"`, `"このまんまでいこう"`, `"Flyer!"`).
  - `fields`: `"Lyrics"`
  - `maxResults`: `3`

```python
import json, ssl, urllib.parse, urllib.request

ctx = ssl._create_unverified_context()
q = urllib.parse.urlencode({"query": "流星のパルス", "fields": "Lyrics"})
req = urllib.request.Request(
    f"https://vocadb.net/api/songs?{q}", headers={"User-Agent": "Mozilla/5.0"}
)
with urllib.request.urlopen(req, context=ctx) as r:
  data = json.load(r)
lyrics = data["items"][0]["lyrics"]
```

### Sekaipedia (Vocal Line Assignments)
For full-version character line assignments (who sings which verse/chorus):
- Check Sekaipedia page wikitext or color-coded lyric references on Vocaloid Lyrics Wiki / Project SEKAI Wiki.

### Creator Commentary
- Search official Project SEKAI Japanese Twitter announcements (`@pj_sekai`) or the producer's official Twitter/note/YouTube description for their official comments upon the song's implementation.

---

## 4. Archiving & Publishing Completed Reports

When a report is generated:
1. **Brain Artifact:** Always write to the active conversation artifact directory:
   `<appDataDir>/brain/<conversation-id>/<song_slug>_lyric_resonance_report.md`
2. **Workspace Archive:** Save a permanent copy to:
   `reports/<song_slug>_lyric_resonance_report.md`
