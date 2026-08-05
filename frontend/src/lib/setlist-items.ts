// Pure setlist-item transforms shared by the SetlistBuilder (no React here so the logic is unit
// testable). The builder has TWO modes:
//
//  • BAG mode (ordered === false): an unordered grid of songs split into Main set / ✦ Encore by a
//    per-song `encore` boolean (non-contiguous tags allowed).
//  • ORDER mode (ordered === true): a drag-reorder list with a single ✦ Encore divider; songs
//    at/after the divider are the encore (a contiguous trailing block).
//
// The single source of truth is each song's `encore` boolean. Mode switches must NEVER lose
// structure: the SET of songs, WHICH songs are encore, and per-song collab guests are preserved.
//
// `encore` is persisted on the wire as `number[]` = the SONG positions whose flag is true
// (membership-based; no order/position precision). These helpers convert between that wire shape
// and the plain item model the builder edits.

// Plain item model — no React keys, just the structural fields tests care about.
export type SetlistItem = { songId: string; guests: string[]; encore: boolean };

// Wire shape (matches NightPrediction): songs in order, guests keyed by SONG position (stringified),
// encore = the song positions flagged as encore, plus the order opt-in.
export type SetlistPrediction = {
  songs: string[];
  guests: Record<string, string[]>; // song position (string) -> guest member ids
  encore: number[]; // song positions whose encore flag is true
  ordered?: boolean;
};

// Derive the encore-divider position (count of songs before it) from stored encore positions. The
// earliest encore index is the divider; if the encore tags aren't a contiguous trailing block
// (possible in bag mode) we still place the divider before the earliest one — entering order mode
// then normalizes everything from there to encore. Falls back to legacy `encoreAt` (count of songs
// before a divider). Returns null when there's no valid divider.
export function dividerAt(len: number, enc?: number[], encAt?: number | null): number | null {
  if (enc && enc.length) {
    const at = Math.min(...enc);
    return at >= 1 && at <= len - 1 ? at : null;
  }
  if (encAt != null) return encAt >= 1 && encAt <= len - 1 ? encAt : null;
  return null;
}

// Seed items from a stored prediction (load / day switch). In ORDER mode the encore is the
// contiguous block at/after the derived divider; in BAG mode it's exactly the stored positions
// (non-contiguous allowed). `encAt` supports legacy rows that only had a divider count.
export function predictionToItems(
  pred: SetlistPrediction & { encoreAt?: number | null }
): SetlistItem[] {
  const { songs, guests, encore, encoreAt } = pred;
  const ord = !!pred.ordered;
  const at = dividerAt(songs.length, encore, encoreAt);
  const encSet = new Set(encore ?? []);
  return songs.map((songId, i) => ({
    songId,
    guests: guests?.[String(i)] ?? [],
    encore: ord ? at != null && i >= at : encSet.has(i)
  }));
}

// Serialize items back to the wire shape: songs in order, guests keyed by SONG position, encore =
// the positions whose flag is true. Works in both modes (a contiguous trailing block in order mode,
// an arbitrary set in bag mode).
export function itemsToPrediction(items: SetlistItem[]): {
  songs: string[];
  guests: Record<string, string[]>;
  encore: number[];
} {
  const songs = items.map((it) => it.songId);
  const guests: Record<string, string[]> = {};
  items.forEach((it, i) => {
    if (it.guests.length) guests[String(i)] = it.guests;
  });
  const encore = items.map((it, i) => (it.encore ? i : -1)).filter((i) => i >= 0);
  return { songs, guests, encore };
}

// Enter ORDER mode (bag -> order): the encore must be a contiguous trailing block, so MOVE every
// encore-tagged song to the END (preserving their relative order); non-encore songs keep their
// relative order at the front. The encore SET (which songIds) is unchanged, the per-song guests
// ride along on the item, and no other song becomes encore. No encore tags -> unchanged order.
export function enterOrdered(items: SetlistItem[]): SetlistItem[] {
  const enc = items.filter((it) => it.encore);
  if (enc.length === 0) return items.map((it) => ({ ...it }));
  const nonEnc = items.filter((it) => !it.encore);
  return [...nonEnc, ...enc].map((it) => ({ ...it }));
}

// Leave ORDER mode (order -> bag): keep each song's encore flag + order exactly as-is. (Pure copy;
// exists as the symmetric counterpart to enterOrdered so callers don't special-case it.)
export function leaveOrdered(items: SetlistItem[]): SetlistItem[] {
  return items.map((it) => ({ ...it }));
}

// ORDER mode: re-derive each song's encore flag from the divider position — songs at index >=
// dividerIndex are encore, the rest are not. The component holds the divider as a list entry; here
// we take its index directly (the count of songs before it). dividerIndex < 0 / null => no encore.
export function dividerToFlags(items: SetlistItem[], dividerIndex: number | null): SetlistItem[] {
  return items.map((it, i) => ({ ...it, encore: dividerIndex != null && dividerIndex >= 0 && i >= dividerIndex }));
}
