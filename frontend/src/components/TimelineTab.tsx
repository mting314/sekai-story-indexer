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
  // Tier-2 backlog: searchable now, LLM summary still queued (see lib/freshness).
  const awaitingSummary = shown.filter((e) => e.indexed && e.summary_status === 'pending').length;

  return (
    <Stack gap="3">
      <UnitFilters />
      <div className={css({ display: 'flex', gap: '4', fontSize: 'xs', color: 'fg.muted' })}>
        <span><span className={css({ display: 'inline-block', w: '2', h: '2', rounded: 'full', mr: '1' })} style={{ background: '#4ade80' }} />queryable in chat ({indexed})</span>
        <span><span className={css({ display: 'inline-block', w: '2', h: '2', rounded: 'full', mr: '1', borderWidth: '1px', borderColor: 'currentColor' })} />indexing pending ({shown.length - indexed})</span>
        {awaitingSummary > 0 && (
          <span title="Searchable now; the LLM summary pass hasn’t reached these yet">
            <span className={css({ display: 'inline-block', w: '2', h: '2', rounded: 'full', mr: '1' })} style={{ background: '#facc15' }} />
            summary pending ({awaitingSummary})
          </span>
        )}
      </div>
      {shown.length === 0 ? (
        <p className={css({ color: 'fg.muted' })}>No events for this filter.</p>
      ) : (
        <div className={css({
          display: 'flex', flexDirection: 'column', gap: '2',
          // Scroll the card list independently of the page (like the old timeline). The right
          // column is sticky, so on desktop this list gets its own bounded scroll area.
          maxH: { base: 'none', lg: 'calc(100vh - 12rem)' }, overflowY: { base: 'visible', lg: 'auto' },
          pr: { lg: '1' }
        })}>
          {shown.map((e) => <EventCard key={e.event_id} e={e} />)}
        </div>
      )}
    </Stack>
  );
}
