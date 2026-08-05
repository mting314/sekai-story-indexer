import { useEffect, useState } from 'react';
import { css } from 'styled-system/css';
import { Stack } from 'styled-system/jsx';
import { Markdown } from '~/components/Markdown';

// Tabbed sections (Overview / Key Events / Episode Index / …) — one nav button per section,
// one markdown pane shown at a time. Shared by the Ask answer cards and the Summaries hero.
export function SectionTabs({ order, sections, onCite }: { order: string[]; sections: Record<string, string>; onCite?: (ref: number) => void }) {
  const [active, setActive] = useState(order[0]);
  // Keep the active tab valid if the section set changes (different event expanded).
  useEffect(() => { if (!order.includes(active)) setActive(order[0]); }, [order, active]);

  return (
    <Stack gap="2">
      <div className={css({ display: 'flex', gap: '1', flexWrap: 'wrap' })}>
        {order.map((label) => (
          <button key={label} onClick={() => setActive(label)}
            className={css({ px: '2.5', py: '1', rounded: 'md', fontSize: 'xs', fontWeight: 'semibold', cursor: 'pointer', color: active === label ? 'accent.text' : 'fg.muted', bg: active === label ? 'accent.subtle' : 'transparent', _hover: { color: 'accent.text' } })}>
            {label}
          </button>
        ))}
      </div>
      <Markdown text={sections[active] ?? ''} onCite={onCite} />
    </Stack>
  );
}
