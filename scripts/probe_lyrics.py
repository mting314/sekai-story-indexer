#!/usr/bin/env python3
"""Lyric-source spike — can we obtain Sekai song lyric TEXT, and from where?

Gate for the lyric<->story resonance feature. Runs where egress to the external
hosts is allowed (the restricted Meta harness blocks them). Stdlib only — no deps,
no install. Prints RAW responses so we judge each source from ground truth, not
from anyone's memory of the API.

Probes four sources for a real event theme song (auto-resolved from the master DB,
or pass --title / --music-id / --event-id):

  1. First-party master DB  — does musics.json carry a lyric/lyric-asset field?
  2. First-party asset CDN   — is there a fetchable music-score/lyric asset?
  3. VocaDB public API       — lyrics via documented API (licensed, translations).
  4. Sekaipedia (MediaWiki)  — song page wikitext with a lyrics section.

Usage:
  python3 scripts/probe_lyrics.py                 # auto-pick an event song
  python3 scripts/probe_lyrics.py --title "ニーゴ"
  python3 scripts/probe_lyrics.py --event-id 6
  python3 scripts/probe_lyrics.py --music-id 74
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

MASTER_DB = "https://sekai-world.github.io/sekai-master-db-diff"
ASSET_CDN = "https://storage.sekai.best/sekai-jp-assets"
VOCADB_API = "https://vocadb.net/api"
SEKAIPEDIA_API_CANDIDATES = (
    "https://www.sekaipedia.org/w/api.php",
    "https://www.sekaipedia.org/api.php",
)
UA = "sekai-story-indexer-lyric-spike/0.1 (research; contact via repo)"
TIMEOUT = 30


def _get(url: str, *, want: str = "json"):
    """GET a URL. Returns (status, parsed_or_text_or_None, error). Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — spike: report anything, don't crash
        return None, None, f"{type(exc).__name__}: {exc}"
    if want == "json":
        try:
            return status, json.loads(raw.decode("utf-8")), None
        except Exception as exc:  # noqa: BLE001
            return status, None, f"non-JSON body ({exc})"
    return status, raw.decode("utf-8", "replace"), None


def _head(url: str):
    """HEAD-ish probe (GET, discard body) — returns status code or an error string."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def resolve_song(args) -> dict | None:
    """Pick a musics.json record: by --music-id, --title, --event-id, or the first
    event that has a theme song."""
    status, musics, err = _get(f"{MASTER_DB}/musics.json")
    if err or not isinstance(musics, list):
        print(f"  ! could not load musics.json: {err} (status {status})")
        return None
    by_id = {m.get("id"): m for m in musics}

    if args.music_id:
        return by_id.get(args.music_id)
    if args.title:
        t = args.title.strip()
        return next((m for m in musics if t in (m.get("title") or "")), None)

    # via event -> eventMusics -> musicId
    _, event_musics, _ = _get(f"{MASTER_DB}/eventMusics.json")
    if isinstance(event_musics, list):
        if args.event_id:
            row = next((r for r in event_musics if r.get("eventId") == args.event_id), None)
        else:
            row = next((r for r in event_musics if r.get("musicId") in by_id), None)
        if row:
            return by_id.get(row.get("musicId"))
    return musics[0] if musics else None


def probe_master_db(song: dict) -> str:
    _rule("1. FIRST-PARTY — musics.json record")
    print(json.dumps(song, ensure_ascii=False, indent=2)[:2000])
    lyric_fields = [k for k in song if "lyric" in k.lower()]
    text_fields = [
        k for k, v in song.items()
        if isinstance(v, str) and "lyric" in k.lower() and k != "lyricist" and len(v) > 40
    ]
    print(f"\n  lyric-ish fields: {lyric_fields or 'none'}")
    print(f"  lyricist (metadata only): {song.get('lyricist', 'N/A')!r}")
    if text_fields:
        return "PASS — musics.json carries lyric TEXT"
    return "FAIL — metadata only (lyricist name), no lyric text"


def probe_asset_cdn(song: dict) -> str:
    _rule("2. FIRST-PARTY — asset CDN music/score/lyric paths")
    bundle = song.get("assetbundleName", "")
    if not bundle:
        return "SKIP — no assetbundleName on this song"
    # Candidate paths (guesses — the whole point is to see which resolve).
    candidates = [
        f"{ASSET_CDN}/music/jacket/{bundle}/{bundle}.webp",       # known-good sanity check
        f"{ASSET_CDN}/music/music_score/{bundle}/{bundle}.txt",
        f"{ASSET_CDN}/music/music_score/{bundle}/lyrics.txt",
        f"{ASSET_CDN}/music/lyric/{bundle}/{bundle}.asset",
        f"{ASSET_CDN}/music/lyrics/{bundle}/{bundle}.json",
        f"{ASSET_CDN}/music/long/{bundle}/{bundle}.mp3",         # audio sanity check
    ]
    hits = []
    for url in candidates:
        code = _head(url)
        marker = "OK " if code == 200 else "   "
        print(f"  {marker}[{code}] {url}")
        if code == 200 and "jacket" not in url and "long" not in url:
            hits.append(url)
    if hits:
        return f"MAYBE — candidate lyric/score asset(s) resolved: {hits}"
    return "FAIL — no first-party lyric/score asset path resolved (guesses only)"


def probe_vocadb(song: dict) -> str:
    _rule("3. VOCADB — public API (search + lyrics)")
    title = song.get("title", "")
    q = urllib.parse.urlencode(
        {"query": title, "maxResults": 3, "fields": "Lyrics",
         "nameMatchMode": "Auto", "lang": "Default"}
    )
    status, data, err = _get(f"{VOCADB_API}/songs?{q}")
    if err:
        return f"FAIL — search error: {err} (status {status})"
    items = (data or {}).get("items") or []
    print(f"  search '{title}' -> {len(items)} hit(s)")
    if not items:
        return "FAIL — no VocaDB match (try a romanized/English title)"
    top = items[0]
    print(f"  top: id={top.get('id')} name={top.get('name')!r} "
          f"artists={top.get('artistString')!r}")

    # Lyrics may ride on the search item, else fetch the song detail.
    lyrics = top.get("lyrics")
    if not lyrics:
        _, detail, _ = _get(f"{VOCADB_API}/songs/{top.get('id')}?fields=Lyrics&lang=Default")
        lyrics = (detail or {}).get("lyrics") or []
    print(f"  lyric entries: {len(lyrics)}")
    for ly in lyrics[:4]:
        val = (ly.get("value") or "")
        print(f"    - keys={sorted(ly.keys())} "
              f"type={ly.get('translationType')} "
              f"culture={ly.get('cultureCodes') or ly.get('cultureCode')} "
              f"source={ly.get('source')!r} len(value)={len(val)}")
        if val:
            snippet = val.replace("\n", " / ")[:120]
            print(f"        value[:120]: {snippet}")
    has_text = any((ly.get("value") or "").strip() for ly in lyrics)
    return ("PASS — VocaDB returns lyric TEXT via API"
            if has_text else
            "PARTIAL — VocaDB has the song but no inline lyric text (check lyricsId endpoint)")


def probe_sekaipedia(song: dict) -> str:
    _rule("4. SEKAIPEDIA — MediaWiki API")
    title = song.get("title", "")
    api = None
    for cand in SEKAIPEDIA_API_CANDIDATES:
        status, data, err = _get(
            f"{cand}?{urllib.parse.urlencode({'action': 'query', 'meta': 'siteinfo', 'format': 'json'})}"
        )
        if not err and isinstance(data, dict):
            api = cand
            print(f"  API endpoint: {cand}")
            break
    if not api:
        return "FAIL — no reachable MediaWiki api.php"

    q = urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": title,
         "srlimit": 3, "format": "json"}
    )
    _, data, _ = _get(f"{api}?{q}")
    hits = (((data or {}).get("query") or {}).get("search")) or []
    print(f"  search '{title}' -> {[h.get('title') for h in hits]}")
    if not hits:
        return "FAIL — no Sekaipedia page found for this title"

    page = hits[0]["title"]
    q2 = urllib.parse.urlencode(
        {"action": "parse", "page": page, "prop": "wikitext", "format": "json"}
    )
    _, parsed, _ = _get(f"{api}?{q2}")
    wikitext = (((parsed or {}).get("parse") or {}).get("wikitext") or {}).get("*", "")
    print(f"  page '{page}': {len(wikitext)} chars of wikitext")
    has_lyrics = any(m in wikitext for m in ("Lyrics", "lyrics", "歌詞", "== Lyric"))
    idx = next((wikitext.find(m) for m in ("Lyrics", "歌詞") if m in wikitext), -1)
    if idx >= 0:
        print(f"  ...around lyrics marker:\n    {wikitext[idx:idx + 200]!r}")
    return ("PASS — Sekaipedia page has a lyrics section (wikitext)"
            if has_lyrics else
            "PARTIAL — page found but no obvious lyrics section")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--title", help="song title substring (JP)")
    ap.add_argument("--music-id", type=int, help="musics.json id")
    ap.add_argument("--event-id", type=int, help="resolve the theme song of this event")
    args = ap.parse_args()

    song = resolve_song(args)
    if not song:
        print("Could not resolve a song. Pass --title / --music-id / --event-id.")
        return 1
    print(f"Probing song: id={song.get('id')} title={song.get('title')!r} "
          f"lyricist={song.get('lyricist')!r} bundle={song.get('assetbundleName')!r}")

    verdicts = {
        "master_db": probe_master_db(song),
        "asset_cdn": probe_asset_cdn(song),
        "vocadb": probe_vocadb(song),
        "sekaipedia": probe_sekaipedia(song),
    }

    _rule("VERDICT")
    for src, v in verdicts.items():
        print(f"  {src:12} {v}")
    print("\nDecision: prefer a first-party PASS; else the cleanest API PASS "
          "(VocaDB > Sekaipedia). Any source is fetch-live / never-rehost, like transcripts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
