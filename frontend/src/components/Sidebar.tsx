// Shared excerpt / transcript viewer (ported from the vanilla sidebar). Provides open* actions
// via context and renders a right-hand panel with visual-novel chat bubbles (speaker colors from
// the entity map) or a marked excerpt. Used by Summaries (transcript links) and Ask (citations).
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { css } from 'styled-system/css';
import { getEpisodeRaw, getScene } from '~/lib/api';
import { buildEntityIndex } from '~/lib/decorate';
import { findQuoteRow, transcriptRows } from '~/lib/quote-anchor';
import { useStore } from '~/lib/store';
import type { Citation } from '~/types/api';

interface SidebarApi {
  openTranscript: (arc: string, episode: string, label: string, highlight?: string, enQuote?: string, window?: string[]) => void;
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
  // Surrounding turns from a turn-attributed citation. Used only as a fallback
  // anchor when the quote itself can't be matched (typically cross-lingual).
  window?: string[];
  banner?: string; // "Official EN" etc.
  mode: 'vn' | 'excerpt';
}

const EMPTY: PanelState = { open: false, title: '', sub: '', loading: false, text: '', mode: 'vn' };

const regionLabel = (r?: string | null) =>
  r === 'en' ? 'English (official)' : r === 'jp' ? 'Japanese (source)' : 'transcript';

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [s, setS] = useState<PanelState>(EMPTY);
  // Monotonic request id: a slow fetch only applies its result if it's still the latest open.
  const reqRef = useRef(0);

  const api = useMemo<SidebarApi>(() => {
    const openTranscript: SidebarApi['openTranscript'] = (arc, episode, label, highlight, enQuote, window) => {
      const id = ++reqRef.current;
      setS({ ...EMPTY, open: true, title: label, sub: '', loading: true });
      getEpisodeRaw(arc, episode).then((d) => {
        if (id !== reqRef.current) return; // superseded by a newer open
        const hi = d.region === 'en' ? (enQuote ?? highlight) : highlight;
        setS({
          open: true, loading: false, title: d.title || label, sub: regionLabel(d.region),
          text: d.text, highlight: hi, window, mode: 'vn',
          banner: enQuote && d.region !== 'en' ? 'Official EN quote shown separately' : undefined
        });
      }).catch(() => { if (id === reqRef.current) setS((p) => ({ ...p, loading: false, text: 'Failed to load transcript.' })); });
    };

    const openCitation: SidebarApi['openCitation'] = (c, lastQuery) => {
      // derived/prose-free backend: fetch the live scene; else load the on-disk transcript.
      if (c.source && c.episode && c.arc_id) {
        const id = ++reqRef.current;
        setS({ ...EMPTY, open: true, title: c.label || c.arc_id, sub: 'live from sekai.best', loading: true });
        getScene(c.arc_id, c.episode, lastQuery ?? '').then((d) => {
          if (id !== reqRef.current) return; // superseded
          setS({ open: true, loading: false, title: d.title || c.label || c.arc_id, sub: 'live from sekai.best', text: d.text, highlight: d.quote, window: c.window, mode: 'vn' });
        }).catch(() => { if (id === reqRef.current) setS((p) => ({ ...p, loading: false, text: 'Failed to load scene.' })); });
        return;
      }
      if (c.episode && c.arc_id) {
        const hi = c.quote || (c.excerpt ? c.excerpt.split('\n').find((l) => l && !l.startsWith('#')) : undefined);
        openTranscript(c.arc_id, c.episode, c.label || c.arc_id, hi, c.quote_en, c.window);
        return;
      }
      // fallback: raw excerpt with the quote marked
      reqRef.current++; // invalidate any in-flight fetch
      setS({ open: true, loading: false, title: c.label || c.arc_id, sub: c.episode_title || '', text: c.excerpt || '', highlight: c.quote, window: c.window, mode: 'excerpt' });
    };

    return { openTranscript, openCitation, close: () => { reqRef.current++; setS((p) => ({ ...p, open: false })); } };
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
          <Excerpt text={s.text} highlight={hi} window={s.window} />
        ) : (
          <VNTranscript text={s.text} highlight={hi} window={s.window} speakerMeta={speakerMeta} />
        )}
      </div>
    </aside>
  );
}

/**
 * Scroll the anchored row into view once the transcript has painted.
 *
 * An episode is ~48 turns (up to 324), so without this the cited line is almost
 * always below the fold. Runs on index/text change rather than on open, because
 * the text arrives from an async fetch after the panel is already showing.
 */
function useScrollToAnchor(index: number, text: string) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (index < 0 || !ref.current) return;
    // rAF: let the rows lay out before measuring, or scrollIntoView lands short.
    const id = requestAnimationFrame(() =>
      ref.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    );
    return () => cancelAnimationFrame(id);
  }, [index, text]);
  return ref;
}

function NotLocated() {
  return (
    <div className={css({ mb: '2', p: '1.5', rounded: 'md', bg: 'bg.subtle', color: 'fg.muted', fontSize: 'xs' })}>
      Couldn’t locate the quoted line in this transcript — showing it from the top.
    </div>
  );
}

function Excerpt({ text, highlight, window: win }: { text: string; highlight?: string; window?: string[] }) {
  const lines = text.split('\n');
  const hitIndex = highlight ? findQuoteRow(lines, highlight, win) : -1;
  const ref = useScrollToAnchor(hitIndex, text);
  return (
    <>
      {highlight && hitIndex < 0 && <NotLocated />}
      <pre className={css({ whiteSpace: 'pre-wrap', fontSize: 'sm', lineHeight: '1.6' })}>
        {lines.map((line, i) => (
          <div key={i} ref={i === hitIndex ? ref : undefined}
            className={i === hitIndex ? css({ bg: 'yellow.400/30', rounded: 'sm' }) : undefined}>{line}</div>
        ))}
      </pre>
    </>
  );
}

function VNTranscript({ text, highlight, window: win, speakerMeta }: { text: string; highlight?: string; window?: string[]; speakerMeta: (n: string) => { color?: string; icon?: string } }) {
  const rows = transcriptRows(text);
  // Tiered match (exact -> normalised -> substring -> window); -1 when unlocatable.
  const hitIndex = highlight ? findQuoteRow(rows, highlight, win) : -1;
  const ref = useScrollToAnchor(hitIndex, text);
  return (
    <div className={css({ display: 'flex', flexDirection: 'column', gap: '2' })}>
      {highlight && hitIndex < 0 && <NotLocated />}
      {rows.map((line, i) => {
        const m = /^([^:：]{1,24})[:：]\s*(.*)$/.exec(line);
        const isHit = i === hitIndex;
        if (!m) {
          // narration can be the cited line too, so it takes the ref as well
          return <div key={i} ref={isHit ? ref : undefined}
            className={css({ textAlign: 'center', fontStyle: 'italic', color: 'fg.muted', fontSize: 'sm', bg: isHit ? 'yellow.400/25' : undefined, rounded: 'sm' })}>{line}</div>;
        }
        const [, speaker, body] = m;
        const meta = speakerMeta(speaker);
        return (
          <div key={i} ref={isHit ? ref : undefined}
            className={css({ p: '2', rounded: 'lg', bg: isHit ? 'yellow.400/25' : 'bg.subtle', borderLeftWidth: '3px' })}
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
