import { useState } from 'react';
import { css } from 'styled-system/css';
import { proxied } from '~/lib/api';
import { jacketUrl } from '~/lib/assets';
import { fmtDate } from '~/lib/format';
import { useStore, useMetaHelpers } from '~/lib/store';
import { useSongById } from '~/hooks/useData';
import { SectionTabs } from '~/components/SectionTabs';
import { useSidebar } from '~/components/Sidebar';
import { EpisodeRow } from '~/components/summaries/EpisodeRow';
import { arcOf, type Hierarchy, type HierNode } from '~/types/hier';

// A collapsible event "album" card. Collapsed: a compact header. Expanded: a hero (the event
// song's jacket + unit/focus/date/song) followed by the event-tier summary as tabbed sections,
// then the episode list. Content is lazy (built only when open).
export function EventSummaryCard({ node, hier }: { node: HierNode; hier: Hierarchy }) {
  const [open, setOpen] = useState(false);
  const { events } = useStore();
  const { unitColor, unitName, unitSymbol, charName } = useMetaHelpers();
  const songById = useSongById();
  const { openTranscript } = useSidebar();

  const arc = arcOf(node.id);
  const ev = events.find((e) => e.arc_slug === arc);
  const unit = ev?.unit;
  const color = unit ? unitColor(unit) : '#8a8a8a';
  const summary = node.summaryId ? hier.summaries[node.summaryId] : undefined;
  const episodes = (node.children ?? [])
    .map((cid) => hier.nodes[cid])
    .filter((n): n is HierNode => !!n && n.kind === 'episode');

  // "Episode N" (from the Episode Index section) → the episode node, by the slug's leading number.
  const epByNum = new Map<number, HierNode>();
  for (const ep of episodes) {
    const n = parseInt(ep.label, 10);
    if (!Number.isNaN(n)) epByNum.set(n, ep);
  }
  const onEpisodeClick = (n: number) => {
    const ep = epByNum.get(n);
    if (ep) openTranscript(arc, ep.label, `Episode ${n}`);
  };

  // Hero art: the event song's jacket first (the commissioned/theme song), then event art.
  const song = ev?.song_id != null ? songById.get(String(ev.song_id)) : undefined;
  const heroArt = jacketUrl(song?.assetbundleName)
    || proxied(ev?.jacket_url || ev?.logo_url || (unit ? unitSymbol(unit) : undefined));
  const thumb = proxied(ev?.jacket_url || ev?.logo_url || (unit ? unitSymbol(unit) : undefined));
  const focus = charName(ev?.focus_character_id) ?? ev?.focus_character;

  return (
    <div className={css({ rounded: 'xl', overflow: 'hidden', borderWidth: '1px', borderColor: 'border.default', borderLeftWidth: '4px', bg: 'bg.default' })} style={{ borderLeftColor: color }}>
      <button onClick={() => setOpen((o) => !o)} className={css({ w: 'full', textAlign: 'left', cursor: 'pointer', p: '3', display: 'flex', alignItems: 'center', gap: '3', _hover: { bg: 'bg.subtle' } })}>
        {thumb && <img src={thumb} alt="" className={css({ w: '10', h: '10', rounded: 'md', objectFit: 'cover', flexShrink: '0' })} onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />}
        <div className={css({ flex: '1', minW: '0' })}>
          <div className={css({ fontWeight: 'bold', fontSize: 'sm', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' })}>{ev?.name ?? node.title}</div>
          <div className={css({ fontSize: 'xs', color: 'fg.muted', display: 'flex', gap: '2', flexWrap: 'wrap' })}>
            {ev?.started_at && <span>{fmtDate(ev.started_at)}</span>}
            {unit && <span style={{ color }}>{unitName(unit)}</span>}
            {ev?.nickname && <span className={css({ color: 'accent.text', fontWeight: 'bold' })}>{ev.nickname}</span>}
            {ev?.is_key_story && <span className={css({ color: 'yellow.400' })}>★</span>}
          </div>
        </div>
        <span className={css({ color: 'fg.subtle' })}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className={css({ p: '3', pt: '0' })}>
          {/* Hero */}
          <div className={css({ display: 'flex', gap: '3', mb: '3', flexDirection: { base: 'column', sm: 'row' } })}>
            {heroArt && (
              <img src={heroArt} alt="" className={css({ w: { base: 'full', sm: '40' }, h: { base: '40', sm: '40' }, rounded: 'lg', objectFit: 'cover', flexShrink: '0' })}
                onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
            )}
            <div className={css({ flex: '1', minW: '0' })}>
              <div className={css({ fontSize: 'lg', fontWeight: 'extrabold' })}>{ev?.name ?? node.title}</div>
              <div className={css({ display: 'flex', flexWrap: 'wrap', gap: '2', mt: '1', fontSize: 'sm', color: 'fg.muted', alignItems: 'center' })}>
                {unit && (
                  <span className={css({ display: 'inline-flex', alignItems: 'center', gap: '1', px: '2', py: '0.5', rounded: 'full', fontWeight: 'bold' })} style={{ color, borderColor: color, borderWidth: 1 }}>
                    {unitSymbol(unit) && <img src={proxied(unitSymbol(unit))} alt="" className={css({ w: '4', h: '4', objectFit: 'contain' })} />}
                    {unitName(unit)}
                  </span>
                )}
                {ev?.is_key_story && <span className={css({ color: 'yellow.400', fontWeight: 'bold' })}>★ key story</span>}
              </div>
              {focus && <div className={css({ mt: '1.5', fontSize: 'sm' })}>★ Focus: <strong>{focus}</strong></div>}
              {ev?.song_title && <div className={css({ mt: '0.5', fontSize: 'sm', color: 'fg.muted' })}>🎵 {ev.song_title}</div>}
              {ev?.started_at && <div className={css({ mt: '0.5', fontSize: 'xs', color: 'fg.subtle' })}>{fmtDate(ev.started_at)}</div>}
            </div>
          </div>

          {/* Summary sections as tabs */}
          {summary && summary.sectionOrder.length > 0 && (
            <SectionTabs order={summary.sectionOrder} sections={summary.sections} onEpisodeClick={onEpisodeClick} />
          )}

          {/* Episodes */}
          {episodes.length > 0 && (
            <div className={css({ mt: '3' })}>
              <div className={css({ fontSize: 'xs', fontWeight: 'bold', color: 'fg.muted', mb: '1', textTransform: 'uppercase', letterSpacing: '0.04em' })}>Episodes</div>
              {episodes.map((epNode) => <EpisodeRow key={epNode.id} node={epNode} arc={arc} hier={hier} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
