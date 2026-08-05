// The event timeline: unit-filter chips + a grid of banner-art event cards with nested
// card/area child badges. Clicking an indexed card scopes the Ask tab to that event.
import { css } from 'styled-system/css';
import { Stack } from 'styled-system/jsx';
import { useStore } from '~/lib/store';
import { UnitFilters } from '~/components/timeline/UnitFilters';
import { EventCard } from '~/components/timeline/EventCard';

export function TimelineTab() {
  const { events, loading, activeUnit } = useStore();

  if (loading) return <p className={css({ color: 'fg.muted' })}>Loading timeline…</p>;
  if (!events.length) return <p className={css({ color: 'fg.muted' })}>No events yet — run <code>indexer fetch</code> to populate the timeline.</p>;

  const shown = activeUnit === 'all' ? events : events.filter((e) => e.unit === activeUnit);
  const indexed = shown.filter((e) => e.indexed).length;

  return (
    <Stack gap="3">
      <UnitFilters />
      <div className={css({ display: 'flex', gap: '4', fontSize: 'xs', color: 'fg.muted' })}>
        <span><span className={css({ display: 'inline-block', w: '2', h: '2', rounded: 'full', mr: '1' })} style={{ background: '#4ade80' }} />queryable in chat ({indexed})</span>
        <span><span className={css({ display: 'inline-block', w: '2', h: '2', rounded: 'full', mr: '1', borderWidth: '1px', borderColor: 'currentColor' })} />indexing pending ({shown.length - indexed})</span>
      </div>
      {shown.length === 0 ? (
        <p className={css({ color: 'fg.muted' })}>No events for this filter.</p>
      ) : (
        <div className={css({ display: 'grid', gridTemplateColumns: { base: '1fr', md: 'repeat(2, 1fr)', xl: 'repeat(3, 1fr)' }, gap: '3' })}>
          {shown.map((e) => <EventCard key={e.event_id} e={e} />)}
        </div>
      )}
    </Stack>
  );
}
