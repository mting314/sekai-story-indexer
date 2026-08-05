import { expect, test, describe } from 'bun:test';
import {
  dividerAt,
  predictionToItems,
  itemsToPrediction,
  enterOrdered,
  leaveOrdered,
  dividerToFlags,
  type SetlistItem
} from './setlist-items';

// --- helpers -------------------------------------------------------------
const item = (songId: string, encore = false, guests: string[] = []): SetlistItem => ({ songId, guests, encore });
// Structural fingerprint of a setlist, ORDER-INSENSITIVE within each section: the SET of songs, the
// SET of encore songs, and each song's guests keyed by songId. Used to assert mode switches don't
// lose structure even if membership order within a section changes.
const fingerprint = (items: SetlistItem[]) => ({
  songs: [...new Set(items.map((i) => i.songId))].sort(),
  encore: items.filter((i) => i.encore).map((i) => i.songId).sort(),
  guests: Object.fromEntries(items.filter((i) => i.guests.length).map((i) => [i.songId, [...i.guests].sort()]))
});

// =========================================================================
describe('dividerAt', () => {
  test('earliest encore position is the divider', () => {
    expect(dividerAt(5, [3, 4])).toBe(3);
    expect(dividerAt(5, [2, 3, 4])).toBe(2);
  });
  test('non-contiguous tags still use the earliest', () => {
    expect(dividerAt(5, [1, 3])).toBe(1);
  });
  test('out-of-range / empty -> null', () => {
    expect(dividerAt(5, [])).toBeNull();
    expect(dividerAt(5, undefined)).toBeNull();
    expect(dividerAt(5, [0])).toBeNull(); // divider at 0 means EVERYTHING is encore -> no divider
    expect(dividerAt(3, [5])).toBeNull(); // beyond the list
  });
  test('legacy encoreAt fallback', () => {
    expect(dividerAt(4, undefined, 2)).toBe(2);
    expect(dividerAt(4, undefined, 0)).toBeNull();
    expect(dividerAt(4, [], 3)).toBe(3);
  });
});

// =========================================================================
describe('predictionToItems / itemsToPrediction round-trip', () => {
  test('bag mode: keeps exact (non-contiguous) encore positions', () => {
    const pred = { songs: ['a', 'b', 'c', 'd'], guests: { '1': ['x'] }, encore: [0, 2], ordered: false };
    const items = predictionToItems(pred);
    expect(items.map((i) => i.encore)).toEqual([true, false, true, false]);
    expect(items[1].guests).toEqual(['x']);
    // serialize back -> identical wire shape
    expect(itemsToPrediction(items)).toEqual({ songs: ['a', 'b', 'c', 'd'], guests: { '1': ['x'] }, encore: [0, 2] });
  });

  test('order mode: encore becomes a contiguous trailing block from the earliest tag', () => {
    const pred = { songs: ['a', 'b', 'c', 'd'], guests: {}, encore: [2, 3], ordered: true };
    const items = predictionToItems(pred);
    expect(items.map((i) => i.encore)).toEqual([false, false, true, true]);
  });

  test('predictionToItems(itemsToPrediction(x)) is structure-stable (bag)', () => {
    const x = [item('a', false, ['g1']), item('b', true), item('c', true, ['g2', 'g3']), item('d', false)];
    const round = predictionToItems({ ...itemsToPrediction(x), ordered: false });
    expect(round).toEqual(x);
  });

  test('predictionToItems(itemsToPrediction(x)) is structure-stable (order, trailing encore)', () => {
    const x = [item('a'), item('b', false, ['g1']), item('c', true), item('d', true)];
    const round = predictionToItems({ ...itemsToPrediction(x), ordered: true });
    expect(round).toEqual(x);
  });

  test('empty + no-encore predictions', () => {
    expect(predictionToItems({ songs: [], guests: {}, encore: [], ordered: true })).toEqual([]);
    const noEnc = predictionToItems({ songs: ['a', 'b'], guests: {}, encore: [], ordered: false });
    expect(noEnc.every((i) => !i.encore)).toBe(true);
  });
});

// =========================================================================
describe('enterOrdered (bag -> order)', () => {
  test('round-trip bag -> order -> bag preserves song set, encore set, and guests', () => {
    const bag = [item('a', false, ['g1']), item('b', true), item('c', false), item('d', true, ['g2'])];
    const ordered = enterOrdered(bag);
    const back = leaveOrdered(ordered);
    expect(fingerprint(back)).toEqual(fingerprint(bag));
    expect(fingerprint(ordered)).toEqual(fingerprint(bag));
  });

  test('encore-tagged songs move to a contiguous trailing block (rest keep order)', () => {
    const bag = [item('a'), item('b', true), item('c'), item('d', true), item('e')];
    const ordered = enterOrdered(bag);
    expect(ordered.map((i) => i.songId)).toEqual(['a', 'c', 'e', 'b', 'd']);
    expect(ordered.map((i) => i.encore)).toEqual([false, false, false, true, true]);
  });

  test('KNOWN BUG: scattered mid-list encore tags -> exactly those songs are encore, no others, none dropped', () => {
    // b (idx1) and d (idx3) are encore; a/c/e are not. After enterOrdered exactly b,d are encore.
    const bag = [item('a'), item('b', true), item('c'), item('d', true), item('e')];
    const ordered = enterOrdered(bag);
    const encSongs = ordered.filter((i) => i.encore).map((i) => i.songId).sort();
    expect(encSongs).toEqual(['b', 'd']);
    // every original song survives
    expect(ordered.map((i) => i.songId).sort()).toEqual(['a', 'b', 'c', 'd', 'e']);
    // no NON-tagged song became encore
    expect(ordered.filter((i) => i.encore).length).toBe(2);
    // serialize -> trailing contiguous block
    expect(itemsToPrediction(ordered).encore).toEqual([3, 4]);
  });

  test('KNOWN BUG: only encore song is the FIRST song (old divider-at-0 case) is NOT lost', () => {
    const bag = [item('a', true), item('b'), item('c')];
    const ordered = enterOrdered(bag);
    // 'a' is the sole encore; it moves to the end and stays encore (it must NOT be dropped).
    expect(ordered.map((i) => i.songId)).toEqual(['b', 'c', 'a']);
    expect(ordered.filter((i) => i.encore).map((i) => i.songId)).toEqual(['a']);
    expect(itemsToPrediction(ordered).encore).toEqual([2]);
    // and round-tripping back to bag keeps 'a' as the (now only) encore song
    expect(fingerprint(leaveOrdered(ordered))).toEqual(fingerprint(bag));
  });

  test('ALL songs encore -> all stay encore, order preserved, none dropped', () => {
    const bag = [item('a', true), item('b', true), item('c', true)];
    const ordered = enterOrdered(bag);
    expect(ordered.map((i) => i.songId)).toEqual(['a', 'b', 'c']);
    expect(ordered.every((i) => i.encore)).toBe(true);
  });

  test('no encore tags -> order unchanged, nothing becomes encore', () => {
    const bag = [item('a'), item('b'), item('c')];
    const ordered = enterOrdered(bag);
    expect(ordered.map((i) => i.songId)).toEqual(['a', 'b', 'c']);
    expect(ordered.every((i) => !i.encore)).toBe(true);
  });

  test('guests stay attached to the right SONG across the reorder (not left on the old index)', () => {
    // b moves from index 1 to the trailing encore block; its guests must travel WITH it.
    const bag = [item('a', false, ['ga']), item('b', true, ['gb1', 'gb2']), item('c', false, ['gc'])];
    const ordered = enterOrdered(bag);
    const byId = Object.fromEntries(ordered.map((i) => [i.songId, i.guests]));
    expect(byId['a']).toEqual(['ga']);
    expect(byId['b']).toEqual(['gb1', 'gb2']);
    expect(byId['c']).toEqual(['gc']);
    // and through serialization the guests land on b's NEW position, not its old one (index 2 now)
    const pred = itemsToPrediction(ordered);
    expect(ordered[pred.encore[0]].songId).toBe('b');
    expect(pred.guests[String(pred.encore[0])]).toEqual(['gb1', 'gb2']);
  });

  test('enterOrdered does not mutate the input', () => {
    const bag = [item('a', true), item('b')];
    const copy = bag.map((i) => ({ ...i, guests: [...i.guests] }));
    enterOrdered(bag);
    expect(bag).toEqual(copy);
  });
});

// =========================================================================
describe('leaveOrdered (order -> bag) round-trip', () => {
  test('round-trip order -> bag -> order preserves song set, encore set, and guests', () => {
    // an ordered list with a trailing encore block
    const ordered = [item('a'), item('b', false, ['g1']), item('c', true), item('d', true, ['g2'])];
    const bag = leaveOrdered(ordered);
    const back = enterOrdered(bag);
    expect(fingerprint(bag)).toEqual(fingerprint(ordered));
    expect(fingerprint(back)).toEqual(fingerprint(ordered));
  });

  test('keeps each song flag + order as-is (pure copy)', () => {
    const ordered = [item('a'), item('c', false, ['x']), item('b', true)];
    const bag = leaveOrdered(ordered);
    expect(bag.map((i) => i.songId)).toEqual(['a', 'c', 'b']);
    expect(bag.map((i) => i.encore)).toEqual([false, false, true]);
    expect(bag[1].guests).toEqual(['x']);
  });
});

// =========================================================================
describe('dividerToFlags', () => {
  test('songs at/after the divider index are encore, the rest are not', () => {
    const items = [item('a'), item('b'), item('c'), item('d')];
    const flagged = dividerToFlags(items, 2);
    expect(flagged.map((i) => i.encore)).toEqual([false, false, true, true]);
  });
  test('divider at 0 -> all encore', () => {
    expect(dividerToFlags([item('a'), item('b')], 0).every((i) => i.encore)).toBe(true);
  });
  test('null / negative divider -> none encore', () => {
    expect(dividerToFlags([item('a', true), item('b', true)], null).every((i) => !i.encore)).toBe(true);
    expect(dividerToFlags([item('a', true)], -1).every((i) => !i.encore)).toBe(true);
  });
  test('preserves songId + guests, only rewrites encore', () => {
    const flagged = dividerToFlags([item('a', false, ['g']), item('b', true)], 1);
    expect(flagged[0]).toEqual(item('a', false, ['g']));
    expect(flagged[1].encore).toBe(true);
  });
});
