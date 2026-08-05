// Character/unit name highlighting, ported (simplified) from the vanilla decorateNames.
// Builds an entity index from meta (full names + given names ≥3 chars + unit names/aliases),
// then a single case-insensitive TreeWalker pass wraps matches in colored spans, skipping
// text already inside <a>, .ent, or <code>. Idempotent.
//
// (The vanilla app had a second pass licensing short given names like "An" per-answer; that
// refinement is intentionally omitted here to avoid mis-coloring the article "an".)
import type { Meta } from '~/types/api';

export interface EntityIndex {
  re: RegExp | null;
  map: Record<string, { color?: string; icon?: string; kind: 'char' | 'unit' }>;
}

const UNIT_ALIASES: Record<string, string[]> = {
  leo_need: ['Leo/need', 'Leoneed'],
  more_more_jump: ['MORE MORE JUMP!', 'MORE MORE JUMP', 'MMJ'],
  vivid_bad_squad: ['Vivid BAD SQUAD', 'VBS'],
  wonderlands_showtime: ['Wonderlands×Showtime', 'Wonderlands x Showtime', 'WxS'],
  nightcord: ['Nightcord at 25:00', '25-ji, Nightcord de.', 'Nightcord'],
  virtual_singer: ['Virtual Singer', 'VIRTUAL SINGER']
};

const escRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function buildEntityIndex(meta: Meta): EntityIndex {
  const map: EntityIndex['map'] = {};
  const names: string[] = [];
  const add = (name: string, v: { color?: string; icon?: string; kind: 'char' | 'unit' }) => {
    const key = name.toLowerCase();
    if (!name || map[key]) return;
    map[key] = v;
    names.push(name);
  };

  for (const [id, c] of Object.entries(meta.characters)) {
    void id;
    const color = c.color; const icon = c.icon;
    if (c.en) {
      add(c.en, { color, icon, kind: 'char' });
      const given = c.en.split(/\s+/)[0];
      if (given && given.length >= 3) add(given, { color, icon, kind: 'char' });
    }
    if (c.jp) add(c.jp, { color, icon, kind: 'char' });
  }
  for (const [slug, u] of Object.entries(meta.units)) {
    const v = { color: u.color, icon: u.symbol, kind: 'unit' as const };
    add(u.name, v);
    for (const a of UNIT_ALIASES[slug] ?? []) add(a, v);
  }

  names.sort((a, b) => b.length - a.length); // longest-first
  const re = names.length
    ? new RegExp(`(?<![A-Za-z0-9])(${names.map(escRe).join('|')})(?![A-Za-z0-9])`, 'gi')
    : null;
  return { re, map };
}

const SKIP = new Set(['A', 'CODE']);

// Wrap entity mentions in `root` with colored spans. Runs over text nodes only.
export function decorateHtml(root: HTMLElement, idx: EntityIndex): void {
  if (!idx.re) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      let el = node.parentElement;
      while (el && el !== root) {
        if (SKIP.has(el.tagName) || el.classList.contains('ent')) return NodeFilter.FILTER_REJECT;
        el = el.parentElement;
      }
      // Reset before every test: `re` is a shared /g/ regex, so a prior .test() left lastIndex
      // mid-string, which would make this test resume at the wrong offset and drop matches.
      idx.re!.lastIndex = 0;
      return idx.re!.test(node.nodeValue ?? '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });
  const targets: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) targets.push(n as Text);
  for (const textNode of targets) {
    const text = textNode.nodeValue ?? '';
    idx.re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (let m = idx.re.exec(text); m; m = idx.re.exec(text)) {
      const ent = idx.map[m[1].toLowerCase()];
      if (!ent) continue;
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const span = document.createElement('span');
      span.className = 'ent';
      if (ent.color) span.style.color = ent.color;
      if (ent.icon) {
        const img = document.createElement('img');
        img.className = 'ent-ic'; img.src = ent.icon; img.alt = '';
        img.onerror = () => { img.style.display = 'none'; };
        span.appendChild(img);
      }
      span.appendChild(document.createTextNode(m[1]));
      frag.appendChild(span);
      last = m.index + m[1].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    textNode.parentNode?.replaceChild(frag, textNode);
  }
}
