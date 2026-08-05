import { css } from 'styled-system/css';
import { useStore, useMetaHelpers } from '~/lib/store';
import { unitIcon } from '~/lib/assets';

// Unit filter chips: "All (n)" + one per unit that has events, with icon + count. A single
// horizontally-scrollable row (doesn't grow vertically). Mirrors the vanilla renderFilters.
export function UnitFilters() {
  const { events, units, activeUnit, setActiveUnit } = useStore();
  const { unitColor } = useMetaHelpers();

  const counts: Record<string, number> = {};
  for (const e of events) counts[e.unit] = (counts[e.unit] ?? 0) + 1;

  const chips = [{ slug: 'all', name: `All (${events.length})`, color: 'var(--colors-accent-default)' }].concat(
    units.filter((u) => counts[u.slug]).map((u) => ({ slug: u.slug, name: `${u.name} (${counts[u.slug]})`, color: unitColor(u.slug) }))
  );

  return (
    <div className={css({ display: 'flex', gap: '1.5', overflowX: 'auto', flexWrap: 'nowrap', pb: '1', mx: '-1', px: '1' })}>
      {chips.map((c) => {
        const active = activeUnit === c.slug;
        return (
          <button
            key={c.slug}
            onClick={() => setActiveUnit(c.slug)}
            className={css({ display: 'inline-flex', alignItems: 'center', gap: '1.5', px: '2.5', py: '1', rounded: 'full', fontSize: 'xs', fontWeight: 'semibold', cursor: 'pointer', borderWidth: '1px', whiteSpace: 'nowrap', flexShrink: '0' })}
            style={{ borderColor: c.color, background: active ? c.color : 'transparent', color: active ? '#0b0d12' : undefined }}
          >
            {c.slug !== 'all' && (
              <img src={unitIcon(c.slug)} alt="" width={16} height={16} style={{ objectFit: 'contain' }}
                onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
            )}
            {c.name}
          </button>
        );
      })}
    </div>
  );
}
