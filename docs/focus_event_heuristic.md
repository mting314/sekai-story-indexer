# Focus-event detection & nickname numbering

How we decide whether an event is a character's **solo focus event** and assign its
community nickname (`saki7`, `mizu5`, …). Implemented in
`src/sekai_story_indexer/source/catalog.py` (`build_catalog`); the per-character
result is in `docs/nickname_events_review.md`.

## What a focus event is

The community numbers each character's *solo focus events* — the events whose story
centers on that character — as `<abbrev><N>` (e.g. `mizu5` = Mizuki Akiyama's 5th
focus event, in release order). Nicknames are used for scoping the chat and for the
contextual-retrieval prefix, so the numbering has to match the community exactly.

## Why it can't be a single field

There is **no single master-DB field** that names an event's story protagonist. The
candidate signals genuinely disagree:

| Signal | What it really is | Fails on |
|---|---|---|
| `eventStories.bannerGameCharacterUnitId` | the banner **artwork** character | "Light Up the Fire" → Kohane art, but An's story |
| first `eventCards` entry / gacha pickups | featured **cards** (often several) | "アイドル・花里みのり" → first card is Toya, story is Minori's |
| `eventDeckBonuses`, card `bonusRate` | the whole participating roster | flat across all featured characters |

So the story lead is editorial. We use a heuristic that's correct for the large
majority, plus a small curated override file for the exceptions.

## The rule

An event is a solo focus event when **all** of these hold:

1. **Type** is `marathon` or `cheerful_carnival` (excludes World Link / `world_bloom`
   and other types).
2. **Banner-in-main-unit**: the event story has a single main unit
   (`resolve_unit_from_story_units`), and the banner focus character
   (`bannerGameCharacterUnitId`) belongs to that unit. This drops Virtual Singers
   (they headline some events but never get a solo focus) and crossover/anniversary
   events (no banner character).
3. **Multi-unit events must have a commissioned song.** A single-unit event (its
   story involves only the focus unit) counts regardless — a few early ones have no
   song (e.g. カーテンコールに惜別を). But a *multi-unit* event (focus unit + guest units)
   only counts if it has a commissioned song; a songless multi-unit event is a
   collab, not a solo focus (e.g. 響くトワイライトパレード, 夏祭り、鳴り響く音は).
4. **Cheerful Carnivals cap at 2 units.** A CC with the focus unit + at most one
   guest unit is a solo focus (e.g. ボクのあしあと キミのゆくさき — Mizuki + a VBS guest);
   a CC spanning 3+ units is a seasonal collab (Valentine / White Day / New Year).

Marathons and CCs that pass are numbered per character in release order.

## Curated overrides — `focus_overrides.json`

For the handful of events the master DB can't disambiguate, a curated file maps
`event_id → focus character id`, or `0` to force-exclude. Applied on top of the
rule (before numbering), so it survives re-fetches. Current entries:

| event | override | why |
|---|---|---|
| `97` Light Up the Fire | → An (10) | banner artwork is Kohane, but the story is An's |
| `22` お悩み聞かせて！わくわくピクニック | → excluded | a mixed event the rule would otherwise count |

To fix a future mistake: add one line to `focus_overrides.json` and re-run
`indexer fetch` (or regenerate `events_index.json`).

## Abbreviations

The `<abbrev>` per character lives in
`src/sekai_story_indexer/source/nicknames.py` (`CHARACTER_ID_TO_ABBREV`);
`docs/nickname_review.md` is the review table. Confirmed: `11 = aki` (Akito),
`13 = kasa` (Tsukasa), `20 = mizu` (Mizuki).
