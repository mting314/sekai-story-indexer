import { useState } from 'react';
import { css } from 'styled-system/css';
import { proxied } from '~/lib/api';
import { fmtDate } from '~/lib/format';
import { useStore, useMetaHelpers } from '~/lib/store';
import { Markdown } from '~/components/Markdown';
import { EpisodeRow } from '~/components/summaries/EpisodeRow';
import { arcOf, type Hierarchy, type HierNode } from '~/types/hier';

// A collapsible event "album" card: header (unit color, art, title, nickname, key, focus, date)
// + on first open, the event-tier summary and its episode rows. Content is lazy (only when open).
export function EventSummaryCard({ node, hier }: { node: HierNode; hier: Hierarchy }) {
  const [open, setOpen] = useState(false);
  const { events } = useStore();
  const { unitColor, unitName, unitSymbol, charName } = useMetaHelpers();

  const arc = arcOf(node.id);
  const ev = events.find((e) => e.arc_slug === arc);
  const unit = ev?.unit;
  const color = unit ? unitColor(unit) : '#8a8a8a';
  const summary = node.summaryId ? hier.summaries[node.summaryId] : undefined;
  const episodes = (node.children ?? [])
    .map((cid) => hier.nodes[cid])
    .filter((n): n is HierNode => !!n && n.kind === 'episode');

  const art = proxied(ev?.jacket_url || ev?.logo_url || (unit ? unitSymbol(unit) : undefined));
  const focus = charName(ev?.focus_character_id) ?? ev?.focus_character;

  return (
    <div className={css({ rounded: 'xl', overflow: 'hidden', borderWidth: '1px', borderColor: 'border.default', borderLeftWidth: '4px', bg: 'bg.default' })} style={{ borderLeftColor: color }}>
      <button onClick={() => setOpen((o) => !o)} className={css({ w: 'full', textAlign: 'left', cursor: 'pointer', p: '3', display: 'flex', alignItems: 'center', gap: '3', _hover: { bg: 'bg.subtle' } })}>
        {art && <img src={art} alt="" className={css({ w: '10', h: '10', rounded: 'md', objectFit: 'cover', flexShrink: '0' })} onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />}
        <div className={css({ flex: '1', minW: '0' })}>
          <div className={css({ fontWeight: 'bold', fontSize: 'sm', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' })}>{ev?.name ?? node.title}</div>
          <div className={css({ fontSize: 'xs', color: 'fg.muted', display: 'flex', gap: '2', flexWrap: 'wrap' })}>
            {ev?.started_at && <span>{fmtDate(ev.started_at)}</span>}
            {unit && <span style={{ color }}>{unitName(unit)}</span>}
            {ev?.nickname && <span className={css({ color: 'accent.text', fontWeight: 'bold' })}>{ev.nickname}</span>}
            {ev?.is_key_story && <span className={css({ color: 'yellow.400' })}>★</span>}
            {focus && <span>★ {focus}</span>}
          </div>
        </div>
        <span className={css({ color: 'fg.subtle' })}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className={css({ p: '3', pt: '0' })}>
          {summary && summary.sectionOrder.map((label) => (
            <div key={label} className={css({ mb: '3' })}>
              <div className={css({ fontSize: 'xs', fontWeight: 'bold', color: 'fg.muted', textTransform: 'uppercase', letterSpacing: '0.04em', mb: '1' })}>{label}</div>
              <Markdown text={summary.sections[label] ?? ''} />
            </div>
          ))}
          {episodes.length > 0 && (
            <div className={css({ mt: '2' })}>
              <div className={css({ fontSize: 'xs', fontWeight: 'bold', color: 'fg.muted', mb: '1' })}>Episodes</div>
              {episodes.map((epNode) => <EpisodeRow key={epNode.id} node={epNode} arc={arc} hier={hier} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
