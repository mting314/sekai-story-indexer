// Shared excerpt / transcript viewer (ported from the vanilla sidebar). Provides open* actions
// via context and renders a right-hand panel with visual-novel chat bubbles (speaker colors from
// the entity map) or a marked excerpt. Used by Summaries (transcript links) and Ask (citations).
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { css } from 'styled-system/css';
import { getEpisodeRaw, getScene } from '~/lib/api';
import { buildEntityIndex } from '~/lib/decorate';
import { useStore } from '~/lib/store';
import type { Citation } from '~/types/api';

interface SidebarApi {
  openTranscript: (arc: string, episode: string, label: string, highlight?: string, enQuote?: string) => void;
  openCitation: (c: Citation, lastQuery?: string) => void;
  close: () => void;
}

const Ctx = createContext<SidebarApi | null>(null);
export const useSidebar = () => {
  const s = useContext(Ctx);
  if (!s) throw new Error('useSidebar outside SidebarProvider');
  return s;
};

interface PanelState {
  open: boolean;
  title: string;
  sub: string;
  loading: boolean;
  text: string;
  highlight?: string;
  banner?: string; // "Official EN" etc.
  mode: 'vn' | 'excerpt';
}

const EMPTY: PanelState = { open: false, title: '', sub: '', loading: false, text: '', mode: 'vn' };

const regionLabel = (r?: string | null) =>
  r === 'en' ? 'English (official)' : r === 'jp' ? 'Japanese (source)' : 'transcript';

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [s, setS] = useState<PanelState>(EMPTY);

  const api = useMemo<SidebarApi>(() => {
    const openTranscript: SidebarApi['openTranscript'] = (arc, episode, label, highlight, enQuote) => {
      setS({ ...EMPTY, open: true, title: label, sub: '', loading: true });
      getEpisodeRaw(arc, episode).then((d) => {
        const hi = d.region === 'en' ? (enQuote ?? highlight) : highlight;
        setS({
          open: true, loading: false, title: d.title || label, sub: regionLabel(d.region),
          text: d.text, highlight: hi, mode: 'vn',
          banner: enQuote && d.region !== 'en' ? 'Official EN quote shown separately' : undefined
        });
      }).catch(() => setS((p) => ({ ...p, loading: false, text: 'Failed to load transcript.' })));
    };

    const openCitation: SidebarApi['openCitation'] = (c, lastQuery) => {
      // derived/prose-free backend: fetch the live scene; else load the on-disk transcript.
      if (c.source && c.episode && c.arc_id) {
        setS({ ...EMPTY, open: true, title: c.label || c.arc_id, sub: 'live from sekai.best', loading: true });
        getScene(c.arc_id, c.episode, lastQuery ?? '').then((d) => {
          setS({ open: true, loading: false, title: d.title || c.label || c.arc_id, sub: 'live from sekai.best', text: d.text, highlight: d.quote, mode: 'vn' });
        }).catch(() => setS((p) => ({ ...p, loading: false, text: 'Failed to load scene.' })));
        return;
      }
      if (c.episode && c.arc_id) {
        const hi = c.quote || (c.excerpt ? c.excerpt.split('\n').find((l) => l && !l.startsWith('#')) : undefined);
        openTranscript(c.arc_id, c.episode, c.label || c.arc_id, hi, c.quote_en);
        return;
      }
      // fallback: raw excerpt with the quote marked
      setS({ open: true, loading: false, title: c.label || c.arc_id, sub: c.episode_title || '', text: c.excerpt || '', highlight: c.quote, mode: 'excerpt' });
    };

    return { openTranscript, openCitation, close: () => setS((p) => ({ ...p, open: false })) };
  }, []);

  return (
    <Ctx.Provider value={api}>
      {children}
      <SidebarPanel s={s} onClose={api.close} />
    </Ctx.Provider>
  );
}

function SidebarPanel({ s, onClose }: { s: PanelState; onClose: () => void }) {
  const { meta } = useStore();
  const entIdx = useMemo(() => buildEntityIndex(meta), [meta]);
  if (!s.open) return null;

  const speakerMeta = (name: string) => entIdx.map[name.trim().toLowerCase()] ?? {};
  const hi = s.highlight?.trim();

  return (
    <aside className={css({
      position: 'fixed', top: '0', right: '0', h: '100vh', w: 'min(440px, 92vw)', zIndex: '40',
      bg: 'bg.default', borderLeftWidth: '1px', borderColor: 'border.default', boxShadow: 'lg',
      display: 'flex', flexDirection: 'column'
    })}>
      <div className={css({ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '2', p: '3', borderBottomWidth: '1px', borderColor: 'border.subtle' })}>
        <div>
          <div className={css({ fontWeight: 'bold', fontSize: 'sm' })}>{s.title}</div>
          {s.sub && <div className={css({ fontSize: 'xs', color: 'fg.muted' })}>{s.sub}</div>}
        </div>
        <button aria-label="Close" onClick={onClose} className={css({ cursor: 'pointer', color: 'fg.muted', fontSize: 'lg', _hover: { color: 'fg.default' } })}>×</button>
      </div>
      <div className={css({ flex: '1', overflowY: 'auto', p: '3' })}>
        {s.banner && <div className={css({ mb: '2', p: '1.5', rounded: 'md', bg: 'accent.subtle', color: 'accent.text', fontSize: 'xs' })}>{s.banner}</div>}
        {s.loading ? (
          <div className={css({ color: 'fg.muted', fontSize: 'sm' })}>Loading…</div>
        ) : s.mode === 'excerpt' ? (
          <pre className={css({ whiteSpace: 'pre-wrap', fontSize: 'sm', lineHeight: '1.6' })}>
            {renderExcerpt(s.text, hi)}
          </pre>
        ) : (
          <VNTranscript text={s.text} highlight={hi} speakerMeta={speakerMeta} />
        )}
      </div>
    </aside>
  );
}

function renderExcerpt(text: string, hi?: string) {
  if (!hi) return text;
  return text.split('\n').map((line, i) => (
    <div key={i} className={line.trim() === hi ? css({ bg: 'yellow.400/30', rounded: 'sm' }) : undefined}>{line}</div>
  ));
}

function VNTranscript({ text, highlight, speakerMeta }: { text: string; highlight?: string; speakerMeta: (n: string) => { color?: string; icon?: string } }) {
  const rows = text.split('\n').filter((l) => l.trim() && l.trim() !== '---');
  return (
    <div className={css({ display: 'flex', flexDirection: 'column', gap: '2' })}>
      {rows.map((line, i) => {
        const m = /^([^:：]{1,24})[:：]\s*(.*)$/.exec(line);
        const isHit = highlight && line.trim() === highlight;
        if (!m) {
          return <div key={i} className={css({ textAlign: 'center', fontStyle: 'italic', color: 'fg.muted', fontSize: 'sm' })}>{line}</div>;
        }
        const [, speaker, body] = m;
        const meta = speakerMeta(speaker);
        return (
          <div key={i} className={css({ p: '2', rounded: 'lg', bg: isHit ? 'yellow.400/25' : 'bg.subtle', borderLeftWidth: '3px' })}
            style={{ borderColor: meta.color ?? 'var(--colors-border-default)' }}>
            <div className={css({ display: 'flex', alignItems: 'center', gap: '1.5', fontSize: 'xs', fontWeight: 'bold', mb: '0.5' })} style={{ color: meta.color }}>
              {meta.icon && (
                <img src={meta.icon} alt="" className={css({ w: '5', h: '5', rounded: 'full', objectFit: 'cover', flexShrink: '0' })}
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
              )}
              {speaker}
            </div>
            <div className={css({ fontSize: 'sm', lineHeight: '1.55' })}>{body}</div>
          </div>
        );
      })}
    </div>
  );
}
