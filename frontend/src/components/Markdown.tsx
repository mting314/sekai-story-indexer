import { useEffect, useMemo, useRef } from 'react';
import { renderMarkdown } from '~/lib/markdown';
import { buildEntityIndex, decorateHtml } from '~/lib/decorate';
import { useStore } from '~/lib/store';

// Renders the safe markdown subset to HTML, then post-processes in a layout effect:
// leading "Episode N" → link (onEpisodeClick), character/unit name decoration, and [n]
// citation links (onCite).
export function Markdown({ text, onCite, onEpisodeClick, className = 'answer-text' }: {
  text: string;
  onCite?: (ref: number) => void;
  onEpisodeClick?: (n: number) => void;
  className?: string;
}) {
  const { meta } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const idx = useMemo(() => buildEntityIndex(meta), [meta]);
  const html = useMemo(() => renderMarkdown(text, meta), [text, meta]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Linkify a leading "Episode N" in each list item BEFORE name decoration (decorate skips
    // <a>, so the link text won't be re-processed).
    if (onEpisodeClick) linkifyEpisodes(el, onEpisodeClick);
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
  }, [html, idx, onCite, onEpisodeClick]);

  return <div ref={ref} className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}

// Wrap a leading "Episode N" in each <li> in an <a class="ep-link"> that calls onEpisodeClick(N).
function linkifyEpisodes(root: HTMLElement, onEpisodeClick: (n: number) => void) {
  root.querySelectorAll('li').forEach((li) => {
    const walker = document.createTreeWalker(li, NodeFilter.SHOW_TEXT);
    const tn = walker.nextNode() as Text | null;
    if (!tn) return;
    const m = /^(\s*)(Episode\s+(\d+))/.exec(tn.nodeValue ?? '');
    if (!m) return;
    const n = Number(m[3]);
    const a = document.createElement('a');
    a.className = 'ep-link';
    a.href = '#';
    a.textContent = m[2];
    a.onclick = (e) => { e.preventDefault(); onEpisodeClick(n); };
    const frag = document.createDocumentFragment();
    if (m[1]) frag.appendChild(document.createTextNode(m[1]));
    frag.appendChild(a);
    frag.appendChild(document.createTextNode(tn.nodeValue!.slice(m[0].length)));
    tn.parentNode?.replaceChild(frag, tn);
  });
}
