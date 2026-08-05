// Safe markdown subset ported from the vanilla app (webapp/static/app.js renderMarkdown).
// Escapes first, then applies: `code`, **strong**, *em*, {char_id=N} character tags,
// [n] citations → a.cite[data-ref], #..#### headings, - / * bullets, and SECTION_LABELS
// bare "Label:" subheadings. Returns an HTML string for dangerouslySetInnerHTML.
import type { Meta } from '~/types/api';

const SECTION_LABELS = new Set([
  'Overview', 'Key Events', 'Character Developments', 'Continuity Facts', 'Important Terms',
  'Episode Index', 'Character Trajectories', 'Unit / Club State', 'Part Index', 'Episode Arc',
  'Relationship / Unit Developments'
]);

export const escapeHtml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function charTag(name: string, id: string, meta: Meta): string {
  const c = meta.characters[id];
  const color = c?.color ?? 'var(--colors-accent-default)';
  const icon = c?.icon;
  const img = icon ? `<img class="ent-ic" src="${escapeHtml(icon)}" alt="" onerror="this.style.display='none'">` : '';
  return `<span class="ent" style="color:${color}">${img}${name}</span>`;
}

function inline(s: string, meta: Meta): string {
  s = s.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>');
  s = s.replace(/([^\s{]+)\{char_id=(\d+)\}/g, (_m, name: string, id: string) => charTag(name, id, meta));
  s = s.replace(/\{char_id=\d+\}/g, '');
  s = s.replace(/\[(\d+)\]/g, (_m, n: string) => `<a href="#" class="cite" data-ref="${n}">[${n}]</a>`);
  return s;
}

export function renderMarkdown(md: string, meta: Meta): string {
  const lines = escapeHtml(md ?? '').split('\n');
  const out: string[] = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) { closeList(); continue; }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      closeList();
      const level = Math.min(h[1].length + 1, 5); // # -> h2 ... #### -> h5
      out.push(`<h${level}>${inline(h[2], meta)}</h${level}>`);
      continue;
    }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(bullet[1], meta)}</li>`);
      continue;
    }
    closeList();
    const sec = /^([A-Z][^:]{1,40}):\s*$/.exec(line);
    if (sec && SECTION_LABELS.has(sec[1])) {
      out.push(`<div class="md-section">${escapeHtml(sec[1])}</div>`);
      continue;
    }
    out.push(`<p>${inline(line, meta)}</p>`);
  }
  closeList();
  return out.join('');
}
