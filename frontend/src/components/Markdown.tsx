import { useEffect, useMemo, useRef } from 'react';
import { renderMarkdown } from '~/lib/markdown';
import { buildEntityIndex, decorateHtml } from '~/lib/decorate';
import { useStore } from '~/lib/store';
import { useSidebar } from '~/components/Sidebar';

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
  const { openTranscript } = useSidebar();
  const ref = useRef<HTMLDivElement>(null);
  const idx = useMemo(() => buildEntityIndex(meta), [meta]);
  const html = useMemo(() => renderMarkdown(text, meta), [text, meta]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Linkify a leading "Episode N" in each list item BEFORE name decoration
    if (onEpisodeClick) linkifyEpisodes(el, onEpisodeClick);
    linkifyTranscriptRanges(el, openTranscript);
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
  }, [html, idx, onCite, onEpisodeClick, openTranscript]);

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

// Convert transcript range links like 07_yukkuri-susumo-u.md.en:L46-L59 or 07_yukkuri-susumo-u:L46-L59
// into clickable links that open the transcript drawer in the sidebar.
function linkifyTranscriptRanges(root: HTMLElement, openTranscript: (arc: string, episode: string, label: string) => void) {
  const rangeRegex = /\b((\d{2}_[a-z0-9_-]+(?:\.md(?:\.en)?)?):L\d+(?:-L?\d+)?)\b/gi;
  root.querySelectorAll('p, li, div').forEach((node) => {
    if (node.tagName === 'A' || node.closest('a')) return;
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const textNodes: Text[] = [];
    let curr = walker.nextNode();
    while (curr) {
      textNodes.push(curr as Text);
      curr = walker.nextNode();
    }
    for (const tn of textNodes) {
      const val = tn.nodeValue ?? '';
      let match: RegExpExecArray | null;
      rangeRegex.lastIndex = 0;
      if ((match = rangeRegex.exec(val)) !== null) {
        const fullRef = match[1];
        const epSlug = match[2].replace(/\.md(\.en)?$/i, '');
        const a = document.createElement('a');
        a.className = 'ep-link';
        a.href = '#';
        a.textContent = fullRef;
        a.onclick = (e) => {
          e.preventDefault();
          openTranscript('', epSlug, fullRef);
        };
        const frag = document.createDocumentFragment();
        frag.appendChild(document.createTextNode(val.slice(0, match.index)));
        frag.appendChild(a);
        frag.appendChild(document.createTextNode(val.slice(match.index + fullRef.length)));
        tn.parentNode?.replaceChild(frag, tn);
      }
    }
  });
}
