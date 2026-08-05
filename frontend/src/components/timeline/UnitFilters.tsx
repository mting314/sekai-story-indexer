import { css } from 'styled-system/css';
import { HStack } from 'styled-system/jsx';
import { useStore, useMetaHelpers } from '~/lib/store';

// Unit filter chips: "All (n)" + one per unit that has events, with counts. Mirrors the
// vanilla renderFilters.
export function UnitFilters() {
  const { events, units, activeUnit, setActiveUnit } = useStore();
  const { unitColor } = useMetaHelpers();

  const counts: Record<string, number> = {};
  for (const e of events) counts[e.unit] = (counts[e.unit] ?? 0) + 1;

  const chips = [{ slug: 'all', name: `All (${events.length})`, color: 'var(--colors-accent-default)' }].concat(
    units.filter((u) => counts[u.slug]).map((u) => ({ slug: u.slug, name: `${u.name} (${counts[u.slug]})`, color: unitColor(u.slug) }))
  );

  return (
    <HStack gap="1.5" flexWrap="wrap">
      {chips.map((c) => {
        const active = activeUnit === c.slug;
        return (
          <button
            key={c.slug}
            onClick={() => setActiveUnit(c.slug)}
            className={css({ px: '2.5', py: '1', rounded: 'full', fontSize: 'xs', fontWeight: 'semibold', cursor: 'pointer', borderWidth: '1px', whiteSpace: 'nowrap' })}
            style={{ borderColor: c.color, background: active ? c.color : 'transparent', color: active ? '#0b0d12' : undefined }}
          >
            {c.name}
          </button>
        );
      })}
    </HStack>
  );
}
