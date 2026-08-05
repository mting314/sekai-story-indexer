import { useEffect, useMemo, useRef } from 'react';
import { renderMarkdown } from '~/lib/markdown';
import { buildEntityIndex, decorateHtml } from '~/lib/decorate';
import { useStore } from '~/lib/store';

// Renders the safe markdown subset to HTML, then post-processes in a layout effect:
// character/unit name decoration + wiring [n] citation links to onCite.
export function Markdown({ text, onCite, className = 'answer-text' }: { text: string; onCite?: (ref: number) => void; className?: string }) {
  const { meta } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const idx = useMemo(() => buildEntityIndex(meta), [meta]);
  const html = useMemo(() => renderMarkdown(text, meta), [text, meta]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    decorateHtml(el, idx);
    if (onCite) {
      el.querySelectorAll('a.cite').forEach((a) => {
        (a as HTMLElement).onclick = (e) => {
          e.preventDefault();
          const r = Number((a as HTMLElement).dataset.ref);
          if (!Number.isNaN(r)) onCite(r);
        };
      });
    }
  }, [html, idx, onCite]);

  return <div ref={ref} className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}
