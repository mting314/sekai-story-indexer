// Sekai Story Indexer — minimal vanilla-JS front end.
// Timeline reads /api/events; chat posts /api/query. No build step.

const state = {
  events: [], units: [], activeUnit: "all", scopeEventId: null, history: [],
  view: "timeline", summaries: null, meta: { characters: {}, units: {} }, entityRe: null,
  sessionId: null,
  hier: null, // cached /api/hierarchical-summaries tree
  commands: [], // /api/commands catalog for the slash-command menu
  cmd: { open: false, items: [], active: 0 },
  inputHistory: [], histIdx: 0, // terminal-style ↑/↓ recall of submitted inputs
};

// Route external (sekai.best) art through the server image proxy so it loads even
// when the browser can't reach the CDN directly. Local /static paths pass through.
function proxied(u) {
  return u && /^https?:\/\//.test(u) ? "/api/img?u=" + encodeURIComponent(u) : (u || "");
}

// Stable per-chat session id so the server can keep conversation focus state.
function ensureSessionId() {
  if (state.sessionId) return state.sessionId;
  let sid = null;
  try { sid = localStorage.getItem("sekai_session_id"); } catch (e) { /* ignore */ }
  if (!sid) {
    sid = "s-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { localStorage.setItem("sekai_session_id", sid); } catch (e) { /* ignore */ }
  }
  state.sessionId = sid;
  return sid;
}

async function boot() {
  const [units, events, meta] = await Promise.all([
    fetch("/api/units").then((r) => r.json()),
    fetch("/api/events").then((r) => r.json()),
    fetch("/static/meta.json").then((r) => r.json()).catch(() => ({ characters: {}, units: {} })),
  ]);
  state.units = units;
  state.events = events;
  state.meta = meta;
  state.commands = await fetch("/api/commands").then((r) => r.json()).catch(() => []);
  wireCommandMenu();
  buildEntityIndex();
  document.getElementById("tab-timeline").onclick = () => { state.view = "timeline"; renderCurrentView(); };
  document.getElementById("tab-summaries").onclick = () => { state.view = "summaries"; renderCurrentView(); };
  renderFilters();
  renderTimeline();
  if (!events.length) {
    addMessage(
      "system",
      "No events_index.json found yet. Run `indexer fetch` to populate the timeline."
    );
  }
}

function renderFilters() {
  const el = document.getElementById("unit-filters");
  const counts = {};
  for (const e of state.events) counts[e.unit] = (counts[e.unit] || 0) + 1;
  const chips = [{ slug: "all", name: `All (${state.events.length})` }].concat(
    state.units
      .filter((u) => counts[u.slug])
      .map((u) => ({ slug: u.slug, name: `${u.name} (${counts[u.slug]})` }))
  );
  el.innerHTML = "";
  for (const c of chips) {
    const b = document.createElement("button");
    b.className = "chip" + (state.activeUnit === c.slug ? " active" : "");
    b.textContent = c.name;
    b.onclick = () => {
      state.activeUnit = c.slug;
      renderFilters();
      renderCurrentView();
    };
    el.appendChild(b);
  }
}

function renderCurrentView() {
  const tl = document.getElementById("timeline");
  const sm = document.getElementById("summaries");
  const isSummaries = state.view === "summaries";
  tl.classList.toggle("hidden", isSummaries);
  sm.classList.toggle("hidden", !isSummaries);
  document.getElementById("tab-timeline").classList.toggle("active", !isSummaries);
  document.getElementById("tab-summaries").classList.toggle("active", isSummaries);
  if (isSummaries) renderSummaries();
  else renderTimeline();
}

// Summaries tab: the hierarchical event -> episode tree (from the full-engine
// cache). The redundant per-episode "part" tier is collapsed; each event carries
// the rich album header, each episode links to its raw transcript.
async function renderSummaries() {
  await renderHierarchical(document.getElementById("summaries"));
}

function eventsByArc() {
  const m = {};
  for (const e of state.events || []) if (e.arc_slug) m[e.arc_slug] = e;
  return m;
}

const arcOfEvent = (nodeId) => String(nodeId).replace(/^event:/, "");
// Human episode label from a file slug — "Episode N" when numbered; otherwise a
// de-slugged title. Never surfaces the raw slug (no-slugs-in-UI rule).
const episodeLabel = (slug) => {
  const m = /^(\d+)/.exec(String(slug || ""));
  if (m) return `Episode ${parseInt(m[1], 10)}`;
  const words = String(slug || "").replace(/[-_]+/g, " ").trim();
  return words ? words.replace(/\b\w/g, (c) => c.toUpperCase()) : "Episode";
};

async function renderHierarchical(el) {
  if (state.hier === null) {
    el.innerHTML = '<p class="empty">Loading…</p>';
    state.hier = await fetch("/api/hierarchical-summaries").then((r) => r.json()).catch(() => null);
  }
  const data = state.hier;
  const roots = (data && data.roots) || [];
  const evByArc = eventsByArc();
  const visible = roots.filter((rid) => {
    if (state.activeUnit === "all") return true;
    const ev = evByArc[arcOfEvent(rid)];
    return ev && ev.unit === state.activeUnit;
  });
  if (!visible.length) {
    el.innerHTML =
      '<p class="empty">No hierarchical summaries yet — run ' +
      "<code>indexer ingest --summaries hierarchical</code> to populate " +
      "<code>summaries_cache.json</code>.</p>";
    return;
  }
  el.innerHTML = "";
  const list = document.createElement("div");
  list.className = "sum-list";
  for (const rid of visible) list.appendChild(hierEventCard(rid, data, evByArc));
  el.appendChild(list);
}

// One event = a collapsible album card (rich header) -> event summary + episodes.
function hierEventCard(nodeId, data, evByArc) {
  const node = data.nodes[nodeId];
  const arc = arcOfEvent(nodeId);
  const ev = evByArc[arc] || { name: node.title, unit: "mixed" };
  const u = state.meta.units[ev.unit] || {};
  const card = document.createElement("div");
  card.className = "sum-card";
  card.style.setProperty("--unit-color", u.color || "#888");

  const sym = u.symbol
    ? `<img class="usym" src="${u.symbol}" alt="" onerror="this.style.display='none'">`
    : "";
  const nick = ev.nickname ? `<span class="nick">${escapeHtml(ev.nickname)}</span>` : "";
  const key = ev.is_key_story ? '<span class="keytag" title="Key story">★</span>' : "";
  const head = document.createElement("button");
  head.type = "button";
  head.className = "sum-head";
  head.innerHTML =
    `${sym}<span class="sum-date">${fmtDate(ev.started_at)}</span>${nick}` +
    `<span class="sum-name">${escapeHtml(ev.name || node.title)}</span>${key}` +
    `<span class="chev">▸</span>`;

  const body = document.createElement("div");
  body.className = "sum-body";
  const inner = document.createElement("div");
  inner.className = "sum-inner";
  body.appendChild(inner);

  head.addEventListener("click", () => {
    const open = card.classList.toggle("open");
    head.setAttribute("aria-expanded", String(open));
    if (open && !inner.dataset.filled) {
      inner.dataset.filled = "1";
      inner.appendChild(eventHero(ev, node));
      const summary = node.summaryId ? data.summaries[node.summaryId] : null;
      if (summary) inner.appendChild(hierSummary(summary));
      const eps = (node.children || [])
        .map((id) => data.nodes[id])
        .filter((n) => n && n.kind === "episode");
      if (eps.length) {
        const wrap = document.createElement("div");
        wrap.className = "hier-episodes";
        for (const ep of eps) wrap.appendChild(hierEpisode(ep, data, arc));
        inner.appendChild(wrap);
      }
    }
  });
  card.appendChild(head);
  card.appendChild(body);
  return card;
}

// One episode: "Episode N" (collapsible summary) + a link to its raw transcript.
// The redundant single "part" child is collapsed into the episode summary.
function hierEpisode(node, data, arc) {
  const label = episodeLabel(node.episodeName);
  // Event-only pipeline produces no per-episode summary; legacy caches might carry
  // an episode-tier (or single part) summary — support both.
  let summary = node.summaryId ? data.summaries[node.summaryId] : null;
  if (!summary) {
    const part = (node.children || [])
      .map((id) => data.nodes[id])
      .find((n) => n && n.kind === "part" && n.summaryId);
    if (part) summary = data.summaries[part.summaryId];
  }

  const wrap = document.createElement("div");
  wrap.className = "hier-ep";
  const head = document.createElement("div");
  head.className = "hier-ep-head";
  const link = document.createElement("a");
  link.href = "#";
  link.className = "hier-ep-transcript";
  link.textContent = "📄 transcript";
  link.onclick = (e) => {
    e.preventDefault();
    openTranscript(arc, node.episodeName, label);
  };

  if (!summary) {
    // No episode summary → just the label + transcript link (no empty expander).
    const name = document.createElement("span");
    name.className = "hier-ep-name";
    name.textContent = label;
    head.appendChild(name);
    head.appendChild(link);
    wrap.appendChild(head);
    return wrap;
  }

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "hier-ep-toggle";
  toggle.innerHTML = `<span class="hier-chev">▸</span>${escapeHtml(label)}`;
  head.appendChild(toggle);
  head.appendChild(link);
  wrap.appendChild(head);
  const body = document.createElement("div");
  body.className = "hier-ep-body hidden";
  wrap.appendChild(body);
  toggle.addEventListener("click", () => {
    const nowHidden = body.classList.toggle("hidden");
    toggle.querySelector(".hier-chev").textContent = nowHidden ? "▸" : "▾";
    if (!body.dataset.filled) {
      body.dataset.filled = "1";
      body.appendChild(hierSummary(summary));
    }
  });
  return wrap;
}

// Summary sections: markdown + character coloring/icons (decorateNames), since the
// hierarchical summaries carry no inline {char_id} tags.
function hierSummary(summary) {
  const box = document.createElement("div");
  box.className = "hier-summary";
  for (const label of summary.sectionOrder || []) {
    const h = document.createElement("div");
    h.className = "hier-section-label";
    h.textContent = label;
    box.appendChild(h);
    const c = document.createElement("div");
    c.className = "answer-text";
    c.innerHTML = renderMarkdown(summary.sections[label] || "");
    decorateNames(c, new Set());
    box.appendChild(c);
  }
  return box;
}

// Album-hero header (art + unit + focus char + song), salvaged from the former
// event-summary card so the hierarchical view keeps the rich context.
function eventHero(ev, node) {
  const u = state.meta.units[ev.unit] || {};
  const fc = state.meta.characters[ev.focus_character_id];
  const hero = document.createElement("div");
  hero.className = "sum-hero";
  const artSources = [ev.jacket_url, ev.logo_url, u.symbol].filter(Boolean).map(proxied);
  const artHtml = artSources.length
    ? `<img class="sum-art" src="${artSources[0]}" alt="" ` +
      `data-fallbacks="${escapeHtml(artSources.slice(1).join("|"))}" ` +
      `onerror="var f=(this.dataset.fallbacks||'').split('|').filter(Boolean);` +
      `if(f.length){this.src=f.shift();this.dataset.fallbacks=f.join('|');}` +
      `else{this.closest('.sum-hero').classList.add('no-art');}">`
    : "";
  const rows = [`<div class="hero-title">${escapeHtml(ev.name || (node && node.title) || "")}</div>`];
  const badges = [];
  if (u.name) {
    badges.push(
      `<span class="hero-unit" style="color:${u.color}">` +
        (u.symbol ? `<img src="${u.symbol}" alt="" onerror="this.style.display='none'">` : "") +
        `${escapeHtml(u.name)}</span>`
    );
  }
  if (ev.is_key_story) badges.push('<span class="hero-key">★ key story</span>');
  if (badges.length) rows.push(`<div class="hero-row">${badges.join("")}</div>`);
  if (fc) {
    rows.push(
      `<div class="hero-row"><span class="hero-focus"><img src="${fc.icon}" alt="" ` +
        `onerror="this.style.display='none'"><b style="color:${fc.color}">${escapeHtml(fc.en)}</b>` +
        `</span><span class="hero-label">focus</span></div>`
    );
  }
  const songHtml = ev.song_title ? `<div class="sum-song">🎵 ${escapeHtml(ev.song_title)}</div>` : "";
  const artCol = artHtml || songHtml ? `<div class="sum-artcol">${artHtml}${songHtml}</div>` : "";
  hero.innerHTML = artCol + `<div class="sum-info">${rows.join("")}${regionPeriods(ev.regions)}</div>`;
  return hero;
}

// Open the right sidebar with an episode's raw transcript (fetched on demand).
async function openTranscript(arc, episodeSlug, label, highlight, enQuote) {
  const sb = document.getElementById("sidebar");
  document.getElementById("sb-title").textContent = label;
  document.getElementById("sb-sub").textContent = "raw transcript";
  const el = document.getElementById("sb-body");
  el.innerHTML = '<p class="empty">Loading…</p>';
  sb.classList.remove("hidden");
  const data = await fetch(
    `/api/episode-raw?arc=${encodeURIComponent(arc)}&episode=${encodeURIComponent(episodeSlug)}`
  ).then((r) => r.json()).catch(() => null);
  if (!data || !data.text) {
    el.innerHTML = '<p class="empty">Transcript unavailable.</p>';
    return;
  }
  document.getElementById("sb-title").textContent = data.title || label;
  // Highlight a specific source line by bracketing it with sentinels BEFORE
  // markdown-escaping, then swapping them for <mark> in the rendered HTML (so the
  // match survives escaping and the citation's JP line matches the JP transcript).
  let text = data.text;
  if (highlight && text.includes(highlight)) {
    text = text.replace(highlight, () => `⁦HL⁦${highlight}⁦LH⁦`); // fn replacer: no $-pattern interpretation
  }
  let html = renderMarkdown(text)
    .replaceAll("⁦HL⁦", "<mark>")
    .replaceAll("⁦LH⁦", "</mark>");
  el.innerHTML = `<div class="answer-text">${html}</div>`;
  decorateNames(el, new Set());
  // Show the verbatim official English line above the JP transcript, when the
  // scene is localized (the transcript itself stays JP, with the source highlighted).
  if (enQuote) {
    const banner = document.createElement("div");
    banner.className = "en-quote";
    banner.innerHTML = `<span class="en-quote-label">Official EN</span>${escapeHtml(enQuote)}`;
    el.prepend(banner);
  }
  const mark = el.querySelector("mark");
  if (mark) mark.scrollIntoView({ block: "center" });
  else el.scrollTop = 0;
}

// A citation click: for a raw-scene citation (has episode slug + source line), open
// the full episode transcript with that line highlighted; otherwise fall back to
// the excerpt view (e.g. event-summary citations).
function openCitation(cite) {
  // Derived (prose-free public) backend: the citation carries sekai.best coords but
  // no prose. Fetch the scene LIVE and highlight the exact line for the question.
  if (cite && cite.source && cite.episode && cite.arc_id) {
    openLiveScene(cite);
    return;
  }
  if (cite && cite.episode && cite.arc_id) {
    // Prefer the exact cited line; else fall back to the retrieved scene's first
    // content line so the transcript at least scrolls to the right region (the
    // extractive picker can't match an English question to Japanese lines).
    let hl = cite.quote;
    if (!hl && cite.excerpt) {
      hl = (cite.excerpt.split("\n").find((l) => l.trim() && !l.trim().startsWith("#")) || "").trim();
    }
    openTranscript(cite.arc_id, cite.episode, cite.label || "Transcript", hl || "", cite.quote_en || "");
  } else {
    openExcerpt(cite);
  }
}

// Fetch a scene LIVE from sekai.best (prose-free public deploy) and render it in
// the sidebar with the exact matching line highlighted. Prose is never stored here
// — it's fetched transiently for display.
async function openLiveScene(cite) {
  const sb = document.getElementById("sidebar");
  document.getElementById("sb-title").textContent = cite.label || "Scene";
  document.getElementById("sb-sub").textContent = "fetched live from sekai.best";
  const el = document.getElementById("sb-body");
  el.innerHTML = '<p class="empty">Loading from sekai.best…</p>';
  sb.classList.remove("hidden");
  const url =
    `/api/scene?arc=${encodeURIComponent(cite.arc_id)}&episode=${encodeURIComponent(cite.episode)}` +
    `&q=${encodeURIComponent(state.lastQuery || "")}`;
  const data = await fetch(url).then((r) => r.json()).catch(() => null);
  if (!data || !data.text) {
    el.innerHTML = '<p class="empty">Couldn\'t load this scene from sekai.best.</p>';
    return;
  }
  let text = data.text;
  if (data.quote && text.includes(data.quote)) {
    text = text.replace(data.quote, () => `⁦HL⁦${data.quote}⁦LH⁦`);
  }
  el.innerHTML =
    '<div class="answer-text">' +
    renderMarkdown(text).replaceAll("⁦HL⁦", "<mark>").replaceAll("⁦LH⁦", "</mark>") +
    "</div>";
  decorateNames(el, new Set());
  const mark = el.querySelector("mark");
  if (mark) mark.scrollIntoView({ block: "center" });
  else el.scrollTop = 0;
}


// Per-server release windows, in display order, with region flag + label.
const _REGIONS = [
  ["jp", "🇯🇵", "JP"],
  ["en", "🌏", "EN"],
  ["tw", "🇹🇼", "TW"],
  ["kr", "🇰🇷", "KR"],
];

function regionPeriods(regions) {
  regions = regions || {};
  const rows = _REGIONS.filter(([k]) => regions[k] && regions[k].start).map(
    ([k, flag, label]) => {
      const r = regions[k];
      const range = `${fmtDateLong(r.start)} → ${fmtDateLong(r.end)}`;
      return (
        `<div class="region-row"><span class="flag">${flag}</span>` +
        `<span class="rlabel">${label}</span><span class="rperiod">${range}</span></div>`
      );
    }
  );
  if (!rows.length) return "";
  return `<div class="region-block"><div class="region-head">Event period</div>${rows.join("")}</div>`;
}


function fmtDateLong(ms) {
  if (!ms) return "?";
  return new Date(ms).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function durationLabel(start, end) {
  if (!start) return "";
  if (!end) return fmtDate(start);
  const days = Math.round((end - start) / 86400000);
  return `${fmtDate(start)} → ${fmtDate(end)}${days > 0 ? ` · ${days}d` : ""}`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}

// Build a name -> {color, icon, kind} index + one regex over all character
// (full + given) and unit names, longest-first. Used to color-code + icon-tag
// entity mentions inside summary text (fandom-wiki style).
function buildEntityIndex() {
  const entries = [];
  // Short given names (e.g. "An") collide with English words, so we don't put
  // them in the always-on pass. Instead we color them only in summaries whose
  // text also contains the character's FULL name ("An Shiraishi") — the full
  // mention "licenses" the short form. Case-sensitive, so the article "an" is
  // never matched. See decorateNames() pass 2.
  state.shortGiven = [];
  state.givenById = {}; // gameCharacterId -> {given, ent} for roster-licensed coloring
  for (const [cid, c] of Object.entries(state.meta.characters || {})) {
    const ent = { kind: "char", color: c.color, icon: c.icon };
    entries.push([c.en, ent]);
    const given = c.en.split(" ")[0];
    if (given && given !== c.en) {
      state.givenById[cid] = { given, ent };
      if (given.length >= 3) entries.push([given, ent]);
      else state.shortGiven.push({ given, full: c.en, ent });
    }
  }
  const aliases = {
    leo_need: ["Leo/need"],
    more_more_jump: ["MORE MORE JUMP!", "MORE MORE JUMP", "MoreMoreJump"],
    vivid_bad_squad: ["Vivid BAD SQUAD", "Vivid Bad Squad", "VBS"],
    wonderlands_showtime: [
      "Wonderlands×Showtime",
      "Wonderlands x Showtime",
      "Wonderlands Showtime",
    ],
    nightcord: ["Nightcord at 25:00", "25-ji, Nightcord de.", "Nightcord"],
    virtual_singer: ["Virtual Singer", "VIRTUAL SINGER"],
  };
  for (const [slug, u] of Object.entries(state.meta.units || {})) {
    const ent = { kind: "unit", color: u.color, icon: u.symbol };
    for (const a of aliases[slug] || [u.name]) entries.push([a, ent]);
  }
  entries.sort((a, b) => b[0].length - a[0].length);
  state.entityMap = {};
  for (const [n, e] of entries) {
    const k = n.toLowerCase();
    if (!(k in state.entityMap)) state.entityMap[k] = e;
  }
  for (const sg of state.shortGiven) state.entityMap[sg.given.toLowerCase()] = sg.ent;
  const alts = entries.map(([n]) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  state.entityRe = alts.length
    ? new RegExp("(?<![A-Za-z0-9])(" + alts.join("|") + ")(?![A-Za-z0-9])", "gi")
    : null;
}

const _esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Walk text nodes only (never tag attributes/citations) and wrap regex matches
// with a colored span + inline icon. Idempotent: skips already-decorated spans.
function _decoratePass(root, re) {
  if (!re) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement && node.parentElement.closest("a,.ent,code")) continue;
    re.lastIndex = 0;
    if (re.test(node.nodeValue)) targets.push(node);
  }
  for (const tn of targets) {
    const txt = tn.nodeValue;
    const frag = document.createDocumentFragment();
    re.lastIndex = 0;
    let last = 0;
    let m;
    while ((m = re.exec(txt))) {
      const ent = state.entityMap[m[1].toLowerCase()];
      if (!ent) continue;
      if (m.index > last) frag.appendChild(document.createTextNode(txt.slice(last, m.index)));
      const span = document.createElement("span");
      span.className = "ent " + ent.kind;
      span.style.color = ent.color;
      if (ent.icon) {
        const img = document.createElement("img");
        img.className = "ent-ic";
        img.src = ent.icon;
        img.alt = "";
        img.onerror = function () {
          this.style.display = "none";
        };
        span.appendChild(img);
      }
      span.appendChild(document.createTextNode(m[1]));
      frag.appendChild(span);
      last = m.index + m[1].length;
    }
    if (last < txt.length) frag.appendChild(document.createTextNode(txt.slice(last)));
    tn.parentNode.replaceChild(frag, tn);
  }
}

function decorateNames(root, roster) {
  // Pass 1: full names, given names (>=3 chars) and unit names, case-insensitive.
  _decoratePass(root, state.entityRe);
  // Pass 2: given names licensed for THIS summary, matched case-sensitively so the
  // article "an" is never touched. A given name is licensed when either:
  //   * the model tagged that character present (roster, Option 3 — authoritative), or
  //   * the character's full name appears in the text (Option 2 fallback).
  const text = root.textContent || "";
  const lower = text.toLowerCase();
  const givens = new Map(); // given -> ent (dedup)
  for (const sg of state.shortGiven || []) {
    if (lower.includes(sg.full.toLowerCase())) givens.set(sg.given, sg.ent);
  }
  if (roster && roster.size) {
    for (const id of roster) {
      const g = state.givenById[String(id)];
      if (g) givens.set(g.given, g.ent);
    }
  }
  if (givens.size) {
    const alts = [...givens.keys()].map(_esc).join("|");
    const re = new RegExp("(?<![A-Za-z0-9])(" + alts + ")(?![A-Za-z0-9])", "g");
    _decoratePass(root, re);
  }
}

function fmtDate(ms) {
  if (!ms) return "";
  return new Date(ms).toISOString().slice(0, 10);
}

function renderTimeline() {
  const el = document.getElementById("timeline");
  const rows = state.events.filter(
    (e) => state.activeUnit === "all" || e.unit === state.activeUnit
  );
  const nIndexed = rows.filter((e) => e.indexed).length;
  el.innerHTML = "";

  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML =
    `<span class="dot indexed"></span> queryable in chat (${nIndexed}) ` +
    `&nbsp;·&nbsp; <span class="dot pending"></span> on timeline, indexing pending (${rows.length - nIndexed})`;
  el.appendChild(legend);

  // Cards live in an inner wrapper so the rubber-band overscroll (see
  // enableWheelInertia) can translate them past the edges without moving the
  // sticky legend.
  const inner = document.createElement("div");
  inner.className = "tl-inner";
  el.appendChild(inner);

  for (const e of rows) {
    const card = document.createElement("div");
    card.className =
      "event-card" + (e.is_key_story ? " key" : "") + (e.indexed ? " indexed" : " pending");
    const logo = e.logo_url
      ? `<img class="logo" loading="lazy" src="${proxied(e.logo_url)}" alt="" onerror="this.style.display='none'">`
      : "";
    const nick = e.nickname ? `<span class="nick">${e.nickname}</span>` : "";
    const song = e.song_title ? `<div class="song">🎵 ${e.song_title}</div>` : "";
    const fcInfo = state.meta.characters[e.focus_character_id];
    const focusName = (fcInfo && fcInfo.en) || e.focus_character;
    const focus = focusName ? `<div class="focus">★ ${focusName}</div>` : "";
    const status = e.indexed
      ? '<span class="status-dot indexed" title="Queryable in chat now"></span>'
      : '<span class="status-dot pending" title="On the timeline; chat-answerable after the next ingest"></span>';
    // Banner art fills the card as the visual anchor (Sekai in-game style): the
    // wide event-story banner (title + character) first, then the home banner,
    // then the song jacket. A left-to-right scrim keeps the text legible.
    const art = e.story_banner_url || e.banner_url || e.jacket_url || "";
    const artHtml = art
      ? `<img class="art-bg" loading="lazy" src="${proxied(art)}" alt="" ` +
        `onerror="this.closest('.event-card').classList.add('no-art'); this.remove()">` +
        `<div class="scrim"></div>`
      : "";
    card.innerHTML = `
      ${artHtml}
      <div class="card-content">
        ${logo}
        <div class="meta">
          <div class="top">${status}<span class="date">${fmtDate(e.started_at)}</span>${nick}
            ${e.is_key_story ? '<span class="key-badge">key</span>' : ""}</div>
          <div class="name">${e.name}</div>
          ${focus}${song}
        </div>
      </div>`;
    card.onclick = () => setScope(e);
    inner.appendChild(card);
  }
  if (!rows.length) el.innerHTML = '<p class="empty">No events for this filter.</p>';
  enableWheelInertia(el);
  if (el._resetOverscroll) el._resetOverscroll();
}

// Extra momentum for wheel + trackpad scrolling (more inertia / less friction
// than the browser's native scroll) plus iOS-style rubber-band overscroll at the
// top/bottom. Wheel-only on purpose — no pointer capture — so card clicks are
// untouched.
//
// Two states, both driven by one rAF loop:
//   • in-bounds: each wheel tick is an impulse into `velocity` (px/frame) that a
//     loop integrates and decays by FRICTION, so the list keeps gliding after
//     input stops.
//   • past an edge: momentum/push spills into `over` (px past the edge) with
//     diminishing resistance, and the `.tl-inner` wrapper is translated by it so
//     you see the content pull past the edge. A spring pulls `over` back to 0
//     with a force proportional to the distance pushed — the snap-back.
//
// Tuning knobs: FRICTION (↑ = longer glide), WHEEL_GAIN (↑ = stronger push),
// RUBBER (↑ = looser/further overscroll), SPRING (↑ = snappier snap-back).
function enableWheelInertia(el) {
  if (el._wheelInertia) return;
  el._wheelInertia = true;

  const reduce =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) {
    el._resetOverscroll = () => {};
    return; // let native scroll handle it
  }

  const FRICTION = 0.96; // per-frame velocity decay — higher = more inertia
  const WHEEL_GAIN = 0.14; // fraction of a wheel delta added to velocity
  const MAX_V = 90; // cap so a hard flick can't teleport
  const RUBBER = 0.35; // resistance converting wheel motion into overscroll
  const DRAG_RUBBER = 0.5; // resistance when click-dragging past an edge
  const SPRING = 0.16; // per-frame spring-back of the overscroll offset
  const MAX_OVER = 140; // hard cap on how far you can push past an edge
  const DRAG_THRESHOLD = 4; // px before a press becomes a drag (vs a click)
  const maxScroll = () => Math.max(0, el.scrollHeight - el.clientHeight);

  let velocity = 0;
  let over = 0; // signed px past an edge: >0 past bottom, <0 past top
  let inner = null;
  let raf = 0;

  function applyTransform() {
    if (inner) inner.style.transform = over ? `translateY(${(-over).toFixed(2)}px)` : "";
  }

  function frame() {
    if (over !== 0) {
      // Overscrolled: spring `over` back toward 0 (restoring force ∝ distance)
      // and let any leftover momentum bleed off.
      over *= 1 - SPRING;
      if (Math.abs(over) < 0.3) over = 0;
      velocity *= 0.8;
      applyTransform();
      raf = over !== 0 ? requestAnimationFrame(frame) : 0;
      return;
    }
    velocity *= FRICTION;
    const next = el.scrollTop + velocity;
    const max = maxScroll();
    if (next < 0) {
      over = Math.max(-MAX_OVER, over + next * RUBBER); // spill past the top
      el.scrollTop = 0;
      velocity = 0;
      applyTransform();
    } else if (next > max) {
      over = Math.min(MAX_OVER, over + (next - max) * RUBBER); // spill past bottom
      el.scrollTop = max;
      velocity = 0;
      applyTransform();
    } else {
      el.scrollTop = next;
    }
    if (Math.abs(velocity) > 0.4 || over !== 0) raf = requestAnimationFrame(frame);
    else {
      velocity = 0;
      raf = 0;
    }
  }

  el.addEventListener(
    "wheel",
    (e) => {
      if (e.ctrlKey) return; // let pinch-zoom through
      e.preventDefault();
      let d = e.deltaY;
      if (e.deltaMode === 1) d *= 16; // lines → px
      else if (e.deltaMode === 2) d *= el.clientHeight; // pages → px
      const max = maxScroll();
      const atTop = el.scrollTop <= 0;
      const atBottom = el.scrollTop >= max - 1;
      if (over !== 0 || (atTop && d < 0) || (atBottom && d > 0)) {
        // Push directly into overscroll, with resistance that stiffens the
        // further you go, so it feels harder to push the more you push.
        const resist = RUBBER / (1 + Math.abs(over) / 80);
        over = Math.max(-MAX_OVER, Math.min(MAX_OVER, over + d * resist));
        velocity = 0;
        applyTransform();
      } else {
        velocity = Math.max(-MAX_V, Math.min(MAX_V, velocity + d * WHEEL_GAIN));
      }
      if (!raf) raf = requestAnimationFrame(frame);
    },
    { passive: false }
  );

  // ---- click-and-drag to scroll (mouse) ----
  // Capture is deferred until the press actually moves past DRAG_THRESHOLD, so a
  // plain click still reaches the card's onclick (setScope). Dragging past an
  // edge feeds the same resisted-overscroll + spring-back as the wheel; a flick
  // on release hands its velocity to the momentum loop.
  let dragArmed = false;
  let dragging = false;
  let moved = false;
  let startY = 0;
  let startScroll = 0;
  let samples = []; // recent {t, y} for release-velocity estimation

  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse" || e.button !== 0) return; // touch keeps native
    dragArmed = true;
    dragging = false;
    moved = false;
    startY = e.clientY;
    startScroll = el.scrollTop;
    samples = [{ t: e.timeStamp, y: e.clientY }];
    // Stop any in-flight momentum/spring so the grab feels immediate.
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
    velocity = 0;
    over = 0;
    applyTransform();
  });

  el.addEventListener("pointermove", (e) => {
    if (!dragArmed) return;
    const totalDy = e.clientY - startY;
    if (!dragging) {
      if (Math.abs(totalDy) <= DRAG_THRESHOLD) return; // still might be a click
      dragging = true;
      moved = true;
      el.classList.add("dragging");
      try { el.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    }
    const rawPos = startScroll - totalDy; // unclamped desired scroll offset
    const max = maxScroll();
    if (rawPos < 0) {
      over = Math.max(-MAX_OVER, rawPos * DRAG_RUBBER); // resisted pull past top
      el.scrollTop = 0;
    } else if (rawPos > max) {
      over = Math.min(MAX_OVER, (rawPos - max) * DRAG_RUBBER); // past bottom
      el.scrollTop = max;
    } else {
      over = 0;
      el.scrollTop = rawPos;
    }
    applyTransform();
    samples.push({ t: e.timeStamp, y: e.clientY });
    while (samples.length > 6) samples.shift();
  });

  function endDrag(e) {
    if (!dragArmed) return;
    dragArmed = false;
    if (!dragging) return; // was a click — leave it for card.onclick
    dragging = false;
    el.classList.remove("dragging");
    try { el.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    // Fling velocity from the last ~90ms of movement (scrollTop moves opposite
    // to the pointer, hence the negative sign).
    const end = samples[samples.length - 1];
    let first = samples[0];
    for (const s of samples) {
      if (end.t - s.t <= 90) { first = s; break; }
    }
    const dt = end.t - first.t;
    const resting = e.timeStamp - end.t > 90;
    const vpf = dt > 0 && !resting ? (-(end.y - first.y) / dt) * 16 : 0;
    velocity = Math.max(-MAX_V, Math.min(MAX_V, vpf));
    if (!raf) raf = requestAnimationFrame(frame); // momentum + spring-back
  }
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", endDrag);

  // Swallow the click that ends a real drag so it doesn't also open a scope.
  el.addEventListener(
    "click",
    (e) => {
      if (moved) {
        e.stopPropagation();
        e.preventDefault();
        moved = false;
      }
    },
    true
  );

  // Called after each render: re-capture the fresh inner wrapper and clear state
  // (innerHTML was rebuilt, so any prior transform/offset is gone).
  function reset() {
    velocity = 0;
    over = 0;
    dragArmed = false;
    dragging = false;
    moved = false;
    el.classList.remove("dragging");
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
    inner = el.querySelector(".tl-inner");
    if (inner) inner.style.transform = "";
  }
  el._resetOverscroll = reset;
  reset();
}

function setScope(e) {
  if (!e.indexed) {
    addMessage(
      "system",
      `“${e.name}” isn't indexed yet — it's on the timeline but won't be chat-answerable until the next ingest.`
    );
    return;
  }
  state.scopeEventId = e.event_id;
  const u = (state.meta.units || {})[e.unit] || {};
  const color = u.color || "var(--accent)";
  const hint = document.getElementById("scope-hint");
  hint.classList.remove("hidden");
  hint.style.setProperty("--scope-color", color);
  const sub = [e.nickname, u.name].filter(Boolean).join(" · ") || "asking about this event";
  hint.innerHTML =
    `<div class="reply-body">` +
    `<div class="reply-title" style="color:${color}">${escapeHtml(e.name)}</div>` +
    `<div class="reply-sub">${escapeHtml(sub)}</div></div>` +
    `<button class="reply-close" id="clear-scope" title="Clear" aria-label="Clear scope">×</button>`;
  document.getElementById("clear-scope").onclick = (ev) => {
    ev.preventDefault();
    state.scopeEventId = null;
    hint.classList.add("hidden");
  };
  document.getElementById("question").focus();
}

function addMessage(role, text) {
  const box = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

// Animated "…" typing indicator shown while the model thinks (before the first
// streamed token) so the bubble isn't blank during generation latency.
function showThinking(el) {
  el.classList.add("thinking");
  el.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  document.getElementById("messages").scrollTop = 1e9;
}

function clearThinking(el) {
  el.classList.remove("thinking");
  el.textContent = "";
}

// ChatGPT-style faded "thinking" trace: the routing steps the backend took, with
// an animated ellipsis on the active (last) step. Cleared when the answer starts.
function routingSteps(meta) {
  const steps = ["Reading your question"];
  const label = (meta.focus && meta.focus.label) || (meta.scope && meta.scope.label);
  const scopeArc = meta.scope && meta.scope.arc_id;
  if (label) steps.push(`Focusing on ${label}`);
  else if (scopeArc) steps.push(`Focusing on ${scopeArc}`);
  else if (state.activeUnit && state.activeUnit !== "all") {
    steps.push(`Scoped to ${(state.meta.units[state.activeUnit] || {}).name || state.activeUnit}`);
  }
  const act =
    meta.intent === "summarize" ? "Summarizing the event"
    : meta.intent === "count" ? "Counting dialogue"
    : "Searching the story";
  steps.push(meta.backend === "derived" || meta.backend === "full" ? `${act}` : act);
  if (meta.backend) steps.push("Writing the answer");
  return steps;
}

function showRouting(el, steps) {
  el.classList.add("thinking");
  const dots = '<span class="typing"><span></span><span></span><span></span></span>';
  const rows = steps.map((s, i) => {
    const active = i === steps.length - 1;
    return `<div class="routing-step${active ? " active" : " done"}">` +
      `${active ? "" : "✓ "}${escapeHtml(s)}${active ? " " + dots : ""}</div>`;
  });
  el.innerHTML = `<div class="routing">${rows.join("")}</div>`;
  document.getElementById("messages").scrollTop = 1e9;
}

// Render a rich assistant answer: text runs + clickable quote blocks that open
// the excerpt sidebar, plus a compact source list.
function renderAssistant(container, res) {
  container.classList.remove("thinking");
  container.textContent = "";
  const byRef = {};
  for (const c of res.citations || []) byRef[c.ref] = c;

  const parts = res.answer_parts || [{ type: "text", text: res.answer || "" }];
  const quotes = parts.filter((p) => p.type === "quote");
  const texts = parts.filter((p) => p.type !== "quote");

  // Natural-language answer (or extractive lead-in) — rendered as markdown.
  for (const p of texts) {
    const t = document.createElement("div");
    t.className = "answer-text";
    t.innerHTML = renderMarkdown(p.text);
    // color-code + icon character/unit names (inline {char_id} tags handled in
    // renderMarkdown; this catches plain-text names via the roster heuristic).
    decorateNames(t, new Set(res.characters || []));
    container.appendChild(t);
  }

  // Supporting quotes, collapsed under a small heading.
  if (quotes.length) {
    const h = document.createElement("div");
    h.className = "quotes-head";
    h.textContent = res.generated ? "Supporting quotes" : "";
    if (h.textContent) container.appendChild(h);
    for (const p of quotes) {
      const q = document.createElement("blockquote");
      q.className = "quote";
      q.textContent = p.text;
      const cite = byRef[p.ref];
      if (cite) {
        q.title = `${cite.label} — click for the full scene`;
        q.onclick = () => openCitation(cite);
        const tag = document.createElement("span");
        tag.className = "quote-ref";
        tag.textContent = ` [${p.ref}]`;
        q.appendChild(tag);
      }
      container.appendChild(q);
    }
  }

  if (res.citations && res.citations.length) {
    const sources = document.createElement("div");
    sources.className = "sources";
    sources.appendChild(document.createTextNode("Sources: "));
    for (const c of res.citations) {
      const a = document.createElement("a");
      a.href = "#";
      a.className = "source-link";
      a.textContent = `[${c.ref}] ${c.label}`;
      a.onclick = (e) => {
        e.preventDefault();
        openCitation(c);
      };
      sources.appendChild(a);
    }
    container.appendChild(sources);
  }

  // Wire inline [n] citations in the answer to open + highlight their excerpt.
  container.querySelectorAll("a.cite").forEach((a) => {
    a.onclick = (e) => {
      e.preventDefault();
      const c = byRef[a.dataset.ref];
      if (c) openCitation(c);
    };
  });
}

// Minimal, safe markdown -> HTML (escape first, then a limited subset).
// Inline character tag Name{char_id=N} -> colored name + chibi icon.
function tagSpan(name, id) {
  const c = (state.meta.characters || {})[String(id)];
  if (!c) return name;
  const icon = c.icon
    ? `<img class="ent-ic" src="${c.icon}" alt="" onerror="this.style.display='none'">`
    : "";
  return `<span class="ent char" style="color:${c.color}">${icon}${name}</span>`;
}

// Fixed summary-section labels (from the summarizer) — rendered as styled
// subheadings when they appear as a bare "Label:" line, so summaries in the chat
// get the same visual hierarchy as the Summaries tab.
const SECTION_LABELS = new Set([
  "Overview", "Key Events", "Character Developments", "Continuity Facts",
  "Important Terms", "Episode Index", "Character Trajectories", "Unit / Club State",
  "Part Index", "Episode Arc", "Relationship / Unit Developments",
]);

function renderMarkdown(src) {
  const esc = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => {
    s = esc(s);
    // Backslash-escaped markdown punctuation (e.g. "Cheerful\\*Days") -> an
    // HTML entity, so it renders literally and the emphasis regexes below
    // never treat it as a "*".
    s = s.replace(/\\([*_`~[\]\\])/g, (_m, c) => `&#${c.charCodeAt(0)};`);
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      // Emphasis only at word boundaries: opener after start/space/paren, closer
      // before end/space/punct. Stops a stray "*" (e.g. "Cheerful*Days") from
      // italicizing the rest of the line.
      // Emphasis at word boundaries only: opener not preceded by a word char or
      // "*", closer followed by end or any non-word/non-"*" char (so an em-dash,
      // bracket, or quote closes it). A stray "*" inside a word (e.g.
      // "Cheerful*Days") never opens emphasis.
      .replace(
        /(?<![\w*])\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*(?=[^\w*]|$)/g,
        "<strong>$1</strong>"
      )
      .replace(
        /(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?=[^\w*]|$)/g,
        "<em>$1</em>"
      )
      // inline character tags from the summarizer: Name{char_id=N} -> colored name
      // + chibi icon (deterministic, exact span). Orphan tags are stripped.
      .replace(
        /([A-Z][A-Za-z'’.\-/]*(?:\s+[A-Z][A-Za-z'’.\-/]*){0,3})\s*\{char_id=(\d+)\}/g,
        (_m, name, id) => tagSpan(name, id)
      )
      .replace(/\{char_id=\d+\}/g, "")
      // clickable numbered citations: [1] / [2][3]  (also inside `code`/brackets)
      .replace(/\[(\d+)\]/g, '<a href="#" class="cite" data-ref="$1">[$1]</a>');
  };
  const out = [];
  let list = false;
  for (const raw of (src || "").split("\n")) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) {
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    if (list) { out.push("</ul>"); list = false; }
    const sectionLabel = line.replace(/\s*:\s*$/, "");
    if (h) {
      const lvl = Math.min(h[1].length + 1, 4); // # -> h2, ## -> h3, ###+ -> h4
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
    } else if (SECTION_LABELS.has(sectionLabel)) {
      out.push(`<div class="md-section">${escapeHtml(sectionLabel)}</div>`);
    } else if (line.trim()) {
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (list) out.push("</ul>");
  return out.join("");
}

function openExcerpt(cite) {
  const sb = document.getElementById("sidebar");
  document.getElementById("sb-title").textContent = cite.label || cite.arc_id;
  const bits = [];
  if (cite.plot_weight && cite.plot_weight !== "unrated") bits.push(cite.plot_weight);
  if (cite.scene_index != null) bits.push(`scene ${cite.scene_index}`);
  document.getElementById("sb-sub").textContent = bits.join(" · ");
  const esc = (s) =>
    (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const el = document.getElementById("sb-body");
  if (cite.quote) {
    // Raw-scene excerpt: escape + highlight the quoted line (kept pre-wrap).
    let body = esc(cite.excerpt || cite.quote || "");
    const q = esc(cite.quote);
    body = body.split(q).join(`<mark>${q}</mark>`);
    el.innerHTML = body;
    sb.classList.remove("hidden");
    const m = el.querySelector("mark");
    if (m) m.scrollIntoView({ block: "center" });
    return;
  }
  // Summary / prose excerpt: render markdown with the same styling as the chat.
  el.innerHTML = `<div class="answer-text">${renderMarkdown(cite.excerpt || "")}</div>`;
  decorateNames(el, new Set());
  sb.classList.remove("hidden");
}

function closeExcerpt() {
  document.getElementById("sidebar").classList.add("hidden");
}

// Show what the conversation is currently "about" (server-inferred focus), unless
// the user has a manually-clicked timeline scope. Lets the user clear it.
function showFocusChip(focus) {
  if (state.scopeEventId) return; // manual scope chip takes precedence
  const hint = document.getElementById("scope-hint");
  if (!hint) return;
  const label = focus && focus.label;
  if (!label) return;

  const u = (state.meta.units || {})[focus.unit] || {};
  const ch = (state.meta.characters || {})[focus.character_id];
  const color = u.color || (ch && ch.color) || "";
  const icoStyle =
    "width:18px;height:18px;border-radius:50%;vertical-align:middle;margin-right:6px;object-fit:cover";
  // unit symbol normally; a mixed event (or a unit with no symbol) shows the
  // focus character's icon instead.
  let icon = "";
  if (focus.unit && focus.unit !== "mixed" && u.symbol) {
    icon = `<img src="${u.symbol}" alt="" style="${icoStyle}" onerror="this.style.display='none'">`;
  } else if (ch && ch.icon) {
    icon = `<img src="${ch.icon}" alt="" style="${icoStyle}" onerror="this.style.display='none'">`;
  }
  const nick = focus.nickname
    ? ` <span style="opacity:.7">[${escapeHtml(focus.nickname)}]</span>`
    : "";
  const sub = (u.name ? escapeHtml(u.name) + " · " : "") + escapeHtml(label);
  if (color) hint.style.setProperty("--scope-color", color);
  else hint.style.removeProperty("--scope-color");
  hint.innerHTML =
    `<div class="reply-body"><div class="reply-title"${color ? ` style="color:${color}"` : ""}>` +
    `${icon}In focus${nick}</div>` +
    `<div class="reply-sub">${sub}</div></div>` +
    `<button class="reply-close" id="clear-focus" title="Clear focus" aria-label="Clear focus">×</button>`;
  hint.classList.remove("hidden");
  const btn = document.getElementById("clear-focus");
  if (btn) btn.onclick = () => {
    // abandon server-side focus by rotating the session id, and hide the chip
    try { localStorage.removeItem("sekai_session_id"); } catch (e) { /* ignore */ }
    state.sessionId = null;
    ensureSessionId();
    hint.classList.add("hidden");
    hint.innerHTML = "";
  };
}

// Consume the SSE stream: append `delta` text as it arrives, finalize on `done`.
async function streamAnswer(q, pending) {
  const resp = await fetch("/api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: q,
      unit: state.activeUnit === "all" ? null : state.activeUnit,
      event_id: state.scopeEventId,
      history: state.history.slice(-6),
      session_id: ensureSessionId(),
    }),
  });
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let text = "";
  let done = null;
  let firstDelta = true;
  showThinking(pending); // animated "…" until the first token arrives
  for (;;) {
    const { value, done: streamDone } = await reader.read();
    if (streamDone) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() || ""; // keep the incomplete tail
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line.slice(6)); } catch (e) { continue; }
      if (evt.type === "delta") {
        if (firstDelta) { clearThinking(pending); firstDelta = false; }
        text += evt.text || "";
        pending.textContent = text; // progressive plain-text render
        document.getElementById("messages").scrollTop = 1e9;
      } else if (evt.type === "meta") {
        if (evt.focus) showFocusChip(evt.focus);
        if (firstDelta) showRouting(pending, routingSteps(evt)); // faded thinking trace
      } else if (evt.type === "done") {
        done = evt;
      }
    }
  }
  return done || { answer: text };
}

// --- Slash-command autocomplete menu (Claude-Code style) --------------------
function _cmdMenu() {
  return document.getElementById("cmd-menu");
}

function updateCommandMenu() {
  const v = document.getElementById("question").value;
  // Only while typing the command word: leading "/", letters, no space/args yet.
  const m = /^\/([a-z]*)$/i.exec(v);
  if (!m) return closeCommandMenu();
  const prefix = m[1].toLowerCase();
  const items = (state.commands || []).filter((c) => c.command.startsWith(prefix));
  if (!items.length) return closeCommandMenu();
  state.cmd = { open: true, items, active: 0 };
  renderCommandMenu();
}

function renderCommandMenu() {
  const el = _cmdMenu();
  el.innerHTML = "";
  state.cmd.items.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "cmd-item" + (i === state.cmd.active ? " active" : "");
    row.setAttribute("role", "option");
    row.innerHTML =
      `<span class="cmd-name">/${escapeHtml(c.command)}</span>` +
      (c.args ? ` <span class="cmd-args">${escapeHtml(c.args)}</span>` : "") +
      `<span class="cmd-desc">${escapeHtml(c.desc)}</span>`;
    // mousedown (not click) so the input doesn't blur before we apply.
    row.onmousedown = (e) => { e.preventDefault(); applyCommand(c); };
    el.appendChild(row);
  });
  el.classList.remove("hidden");
}

function closeCommandMenu() {
  state.cmd = { open: false, items: [], active: 0 };
  _cmdMenu().classList.add("hidden");
}

function applyCommand(c) {
  const input = document.getElementById("question");
  input.value = `/${c.command}` + (c.args ? " " : ""); // leave a space to type <event>
  closeCommandMenu();
  input.focus();
}

// Grow the composer textarea with its content, up to the CSS max-height.
function autoGrowInput() {
  const el = document.getElementById("question");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

function wireCommandMenu() {
  const input = document.getElementById("question");
  input.addEventListener("input", () => { updateCommandMenu(); autoGrowInput(); });
  input.addEventListener("keydown", (e) => {
    if (state.cmd.open) {
      const n = state.cmd.items.length;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        state.cmd.active = (state.cmd.active + 1) % n;
        renderCommandMenu();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        state.cmd.active = (state.cmd.active - 1 + n) % n;
        renderCommandMenu();
      } else if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault(); // complete instead of submitting
        applyCommand(state.cmd.items[state.cmd.active]);
      } else if (e.key === "Escape") {
        e.preventDefault();
        closeCommandMenu();
      }
      return;
    }
    // Enter submits; Shift+Enter inserts a newline (textarea word-wraps).
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      document.getElementById("ask-form").requestSubmit();
      return;
    }
    // Terminal-style history recall — only at the text boundaries so ↑/↓ still
    // move the caret within multi-line input.
    const hist = state.inputHistory;
    const atStart = input.selectionStart === 0 && input.selectionEnd === 0;
    const atEnd =
      input.selectionStart === input.value.length && input.selectionEnd === input.value.length;
    if (e.key === "ArrowUp" && atStart && hist.length) {
      e.preventDefault();
      state.histIdx = Math.max(0, state.histIdx - 1);
      input.value = hist[state.histIdx];
      autoGrowInput();
      input.setSelectionRange(input.value.length, input.value.length);
    } else if (e.key === "ArrowDown" && atEnd && state.histIdx < hist.length) {
      e.preventDefault();
      state.histIdx = Math.min(hist.length, state.histIdx + 1);
      input.value = state.histIdx === hist.length ? "" : hist[state.histIdx];
      autoGrowInput();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  });
  input.addEventListener("blur", () => setTimeout(closeCommandMenu, 120));
}

// Chat slash commands (/summarize, /lines, /help, …) — posted to /api/command,
// rendered like a normal answer. Focus chip updates for /scope and /clear.
async function runSlashCommand(q, pending) {
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command: q,
        session_id: ensureSessionId(),
        unit: state.activeUnit === "all" ? null : state.activeUnit,
      }),
    }).then((r) => r.json());
    renderAssistant(pending, res);
    if ("focus" in res) {
      if (res.focus) showFocusChip(res.focus);
      else document.getElementById("scope-hint").classList.add("hidden");
    }
    // Keep command turns in the conversation history so follow-ups have context
    // (e.g. asking about a line from a /summarize). The command line reads fine as
    // a user turn; the response is the assistant turn.
    state.history.push({ role: "user", text: q });
    state.history.push({ role: "assistant", text: res.answer || "" });
  } catch (err) {
    pending.textContent = `⚠ ${err}`;
  }
}

document.getElementById("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = document.getElementById("question");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  autoGrowInput(); // collapse the textarea back to one row
  if (state.inputHistory[state.inputHistory.length - 1] !== q) state.inputHistory.push(q);
  state.histIdx = state.inputHistory.length; // reset cursor to "current" (past end)
  state.lastQuery = q; // for derived-backend live-scene line highlighting
  addMessage("user", q);
  const pending = addMessage("assistant", "");
  showThinking(pending);
  if (q.startsWith("/")) {
    await runSlashCommand(q, pending);
    document.getElementById("messages").scrollTop = 1e9;
    return;
  }
  try {
    let res;
    try {
      res = await streamAnswer(q, pending);
    } catch (streamErr) {
      // SSE can be blocked/buffered by proxies — fall back to the JSON endpoint.
      showThinking(pending);
      res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          unit: state.activeUnit === "all" ? null : state.activeUnit,
          event_id: state.scopeEventId,
          history: state.history.slice(-6),
          session_id: ensureSessionId(),
        }),
      }).then((r) => r.json());
    }
    if (res.error && !res.answer) {
      pending.textContent = `⚠ ${res.error}`;
    } else {
      renderAssistant(pending, res); // swap progressive text for the rich view
      // Streaming auto-scrolled to the bottom; for a long answer that leaves the
      // start scrolled out of view (looks "cut off"). Re-anchor so the question +
      // the beginning of the answer are visible, and the reader scrolls DOWN.
      (pending.previousElementSibling || pending).scrollIntoView({ block: "start" });
      if (res.focus) showFocusChip(res.focus);
      state.history.push({ role: "user", text: q });
      state.history.push({ role: "assistant", text: res.answer || "" });
    }
  } catch (err) {
    pending.textContent = `⚠ ${err}`;
  }
  document.getElementById("messages").scrollTop = 1e9;
});

document.addEventListener("click", (e) => {
  if (e.target && e.target.id === "sb-close") closeExcerpt();
});

boot();
