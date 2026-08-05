// The Summaries surface: the hierarchical event→episode summary tree. Event album cards are
// filtered by the active unit chip; each expands to its event-tier summary + episode rows,
// and episodes open their transcript in the shared Sidebar.
import { useEffect, useState } from 'react';
import { css } from 'styled-system/css';
import { Stack } from 'styled-system/jsx';
import { getHierarchicalSummaries } from '~/lib/api';
import { useStore } from '~/lib/store';
import { UnitFilters } from '~/components/timeline/UnitFilters';
import { EventSummaryCard } from '~/components/summaries/EventSummaryCard';
import { arcOf, type Hierarchy } from '~/types/hier';

export function SummariesTab() {
  const { events, activeUnit } = useStore();
  const [hier, setHier] = useState<Hierarchy | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getHierarchicalSummaries().then((d) => setHier(d as unknown as Hierarchy)).catch((x) => setErr(String(x)));
  }, []);

  if (err) return <p className={css({ color: 'red.500' })}>Failed to load: {err}</p>;
  if (!hier) return <p className={css({ color: 'fg.muted' })}>Loading summaries…</p>;
  if (!hier.roots.length) {
    return <p className={css({ color: 'fg.muted' })}>No summaries yet — run <code>indexer ingest --summaries hierarchical</code> (or <code>sekai summarize</code>) to populate.</p>;
  }

  const eventByArc = new Map(events.map((e) => [e.arc_slug, e]));
  const roots = hier.roots.filter((rid) => {
    if (activeUnit === 'all') return true;
    return eventByArc.get(arcOf(rid))?.unit === activeUnit;
  });

  return (
    <Stack gap="3">
      <UnitFilters />
      <p className={css({ fontSize: 'xs', color: 'fg.muted' })}>{roots.length} events · {hier.counts.episodes} episodes summarized</p>
      {roots.length === 0 ? (
        <p className={css({ color: 'fg.muted' })}>No summarized events for this filter.</p>
      ) : (
        <Stack gap="2">
          {roots.map((rid) => {
            const node = hier.nodes[rid];
            return node ? <EventSummaryCard key={rid} node={node} hier={hier} /> : null;
          })}
        </Stack>
      )}
    </Stack>
  );
}
