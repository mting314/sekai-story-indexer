// Shared app state: the catalog (units/events/children/meta) loaded once, the active unit
// filter, the current tab, and the chat "scope" (an event the Ask tab is focused on). Timeline
// sets the scope + switches to Ask; the Ask tab (Phase 4) reads it. A pending-question bridge
// lets other tabs push a question into Ask.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { getEvents, getUnits, getEventChildren, getMeta } from '~/lib/api';
import type { ChildCounts, EventRow, Meta, UnitRow } from '~/types/api';

export type TabId = 'ask' | 'timeline' | 'summaries';

interface AppStore {
  meta: Meta;
  units: UnitRow[];
  events: EventRow[];
  eventChildren: ChildCounts;
  loading: boolean;
  activeUnit: string; // slug or "all"
  setActiveUnit: (u: string) => void;
  scope: EventRow | null;
  setScope: (e: EventRow | null) => void;
  tab: TabId;
  setTab: (t: TabId) => void;
  // Phase 4 wires this so Timeline/quick-actions can submit into Ask. Until then it's a no-op
  // that just focuses the Ask tab (the pending question is stashed for the Ask tab to consume).
  pendingQuestion: string | null;
  ask: (q: string) => void;
  consumePending: () => string | null;
}

const EMPTY_META: Meta = { characters: {}, units: {} };
const Ctx = createContext<AppStore | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta>(EMPTY_META);
  const [units, setUnits] = useState<UnitRow[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [eventChildren, setEventChildren] = useState<ChildCounts>({});
  const [loading, setLoading] = useState(true);
  const [activeUnit, setActiveUnit] = useState('all');
  const [scope, setScope] = useState<EventRow | null>(null);
  const [tab, setTab] = useState<TabId>('ask');
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      getUnits().catch(() => [] as UnitRow[]),
      getEvents().catch(() => [] as EventRow[]),
      getMeta().catch(() => EMPTY_META),
      getEventChildren().catch(() => ({} as ChildCounts))
    ]).then(([u, e, m, c]) => {
      if (!alive) return;
      setUnits(u); setEvents(e); setMeta(m); setEventChildren(c); setLoading(false);
    });
    return () => { alive = false; };
  }, []);

  const value = useMemo<AppStore>(() => ({
    meta, units, events, eventChildren, loading,
    activeUnit, setActiveUnit, scope, setScope, tab, setTab,
    pendingQuestion,
    ask: (q: string) => { setPendingQuestion(q); setTab('ask'); },
    consumePending: () => { const q = pendingQuestion; setPendingQuestion(null); return q; }
  }), [meta, units, events, eventChildren, loading, activeUnit, scope, tab, pendingQuestion]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): AppStore {
  const s = useContext(Ctx);
  if (!s) throw new Error('useStore outside AppProvider');
  return s;
}

// Convenience colour/label helpers off meta.
export function useMetaHelpers() {
  const { meta } = useStore();
  return {
    unitName: (slug: string) => meta.units[slug]?.name ?? slug,
    unitColor: (slug: string) => meta.units[slug]?.color ?? '#8a8a8a',
    unitSymbol: (slug: string) => meta.units[slug]?.symbol,
    charName: (id?: number | string) => (id != null ? meta.characters[String(id)]?.en : undefined),
    charIcon: (id?: number | string) => (id != null ? meta.characters[String(id)]?.icon : undefined)
  };
}
