import { useState } from 'react';
import { css } from 'styled-system/css';
import { proxied } from '~/lib/api';
import { fmtDate } from '~/lib/format';
import { useStore, useMetaHelpers } from '~/lib/store';
import type { EventRow } from '~/types/api';

// A banner-forward event tile (ported/refreshed from the vanilla event-card). Click scopes the
// Ask tab to this event (indexed events only — pending ones aren't chat-answerable yet).
export function EventCard({ e }: { e: EventRow }) {
  const { setScope, setTab, eventChildren } = useStore();
  const { charName } = useMetaHelpers();
  const [noArt, setNoArt] = useState(false);

  const art = proxied(e.story_banner_url || e.banner_url || e.jacket_url);
  const focus = charName(e.focus_character_id) ?? e.focus_character;
  const child = eventChildren[e.arc_slug];
  const clickable = e.indexed;

  return (
    <button
      type="button"
      disabled={!clickable}
      title={clickable ? 'Ask about this event' : 'Indexing pending — not chat-answerable yet'}
      onClick={() => { if (clickable) { setScope(e); setTab('ask'); } }}
      className={css({
        position: 'relative', textAlign: 'left', w: 'full', minH: '20', rounded: 'xl', overflow: 'hidden',
        borderWidth: '1px', borderColor: e.is_key_story ? 'accent.default' : 'border.default',
        bg: 'bg.subtle', cursor: clickable ? 'pointer' : 'default', opacity: clickable ? 1 : 0.55,
        transition: 'transform .12s', _hover: clickable ? { transform: 'translateY(-2px)' } : {}
      })}
    >
      {art && !noArt && (
        <img src={art} alt="" onError={() => setNoArt(true)}
          className={css({ position: 'absolute', inset: '0', w: 'full', h: 'full', objectFit: 'cover', opacity: 0.5 })} />
      )}
      <div className={css({ position: 'absolute', inset: '0', bgGradient: 'to-r', gradientFrom: 'bg.default', gradientVia: 'bg.default/55', gradientTo: 'transparent' })} />
      <div className={css({ position: 'relative', p: '3' })}>
        <div className={css({ display: 'flex', alignItems: 'center', gap: '2', fontSize: 'xs', color: 'fg.muted', mb: '1' })}>
          <span className={css({ w: '2', h: '2', rounded: 'full' })} style={{ background: e.indexed ? '#4ade80' : 'transparent', border: e.indexed ? '0' : '1px solid currentColor' }} />
          <span className={css({ fontVariantNumeric: 'tabular-nums' })}>{fmtDate(e.started_at)}</span>
          {e.nickname && <span className={css({ color: 'accent.text', fontWeight: 'bold' })}>{e.nickname}</span>}
          {e.is_key_story && <span className={css({ color: 'yellow.400' })}>★ key</span>}
        </div>
        <div className={css({ fontSize: 'sm', fontWeight: 'bold' })}>{e.name}</div>
        {focus && <div className={css({ fontSize: 'xs', color: 'fg.muted' })}>★ {focus}</div>}
        {e.song_title && <div className={css({ fontSize: 'xs', color: 'fg.muted' })}>🎵 {e.song_title}</div>}
        {child && (child.cards > 0 || child.area_talks > 0) && (
          <div className={css({ mt: '1', display: 'flex', gap: '2', fontSize: '2xs', color: 'fg.subtle' })}>
            {child.cards > 0 && <span title="Card side-stories nested under this event">🎴 {child.cards}</span>}
            {child.area_talks > 0 && <span title="Area conversations nested under this event">🗺 {child.area_talks}</span>}
          </div>
        )}
      </div>
    </button>
  );
}
