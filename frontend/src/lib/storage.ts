// localStorage-backed save slots for setlists (no backend). Each slot is a named
// snapshot of the builder's wire state.
import type { SetlistState } from '~/lib/share';

const KEY = 'sekai-setlist:slots';

export interface SavedSlot {
  name: string;
  savedAt: number; // epoch ms (stamped by the caller; pass Date.now())
  state: SetlistState;
}

function read(): SavedSlot[] {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? (JSON.parse(raw) as SavedSlot[]) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function write(slots: SavedSlot[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(slots));
  } catch {
    /* quota / private mode — ignore */
  }
}

export const listSlots = (): SavedSlot[] => read().sort((a, b) => b.savedAt - a.savedAt);

/** Upsert a slot by name. `savedAt` should be Date.now() from the caller. */
export function saveSlot(name: string, state: SetlistState, savedAt: number): void {
  const slots = read().filter((s) => s.name !== name);
  slots.push({ name, savedAt, state });
  write(slots);
}

export function loadSlot(name: string): SetlistState | undefined {
  return read().find((s) => s.name === name)?.state;
}

export function deleteSlot(name: string): void {
  write(read().filter((s) => s.name !== name));
}
