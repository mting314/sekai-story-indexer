// Sekai Story Indexer — minimal vanilla-JS front end.
// Timeline reads /api/events; chat posts /api/query. No build step.

const state = {
  events: [], units: [], activeUnit: "all", scopeEventId: null, history: [],
  view: "timeline", summaries: null, meta: { characters: {}, units: {} }, entityRe: null,
  sessionId: null,
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

async function renderSummaries() {
  const el = document.getElementById("summaries");
  if (state.summaries === null) {
    el.innerHTML = '<p class="empty">Loading…</p>';
    [state.summaries, state.unitSummaries] = await Promise.all([
      fetch("/api/summaries").then((r) => r.json()),
      fetch("/api/unit-summaries").then((r) => r.json()).catch(() => ({})),
    ]);
  }
  const rows = state.summaries.filter(
    (s) => state.activeUnit === "all" || s.unit === state.activeUnit
  );
  if (!rows.length) {
    el.innerHTML =
      '<p class="empty">No event summaries yet — run the summarizer (see README).</p>';
    return;
  }
  el.innerHTML = "";
  const list = document.createElement("div");
  list.className = "sum-list";
  // Tier-3 unit overview at the top when a single unit is selected.
  const us = (state.unitSummaries || {})[state.activeUnit];
  if (state.activeUnit !== "all" && us && us.summary) list.appendChild(unitOverviewCard(us));
  for (const s of rows) list.appendChild(summaryCard(s));
  el.appendChild(list);
}

function unitOverviewCard(us) {
  const u = state.meta.units[state.activeUnit] || {};
  const card = document.createElement("div");
  card.className = "unit-overview";
  card.style.setProperty("--unit-color", u.color || "#888");
  const sym = u.symbol
    ? `<img class="usym" src="${u.symbol}" alt="" onerror="this.style.display='none'">`
    : "";
  const head = `<div class="uo-head">${sym}<span style="color:${u.color}">${escapeHtml(u.name || "")}</span> — story so far</div>`;
  const body = document.createElement("div");
  body.className = "answer-text";
  body.innerHTML = renderMarkdown(us.summary);
  card.innerHTML = head;
  card.appendChild(body);
  return card;
}

// Collapsed event card: unit symbol + accent color, date, nickname, name.
// Click to expand -> focus character, duration, focus song, decorated summary.
function summaryCard(s) {
  const u = state.meta.units[s.unit] || {};
  const card = document.createElement("div");
  card.className = "sum-card";
  card.style.setProperty("--unit-color", u.color || "#888");

  const sym = u.symbol
    ? `<img class="usym" src="${u.symbol}" alt="" onerror="this.style.display='none'">`
    : "";
  const nick = s.nickname ? `<span class="nick">${escapeHtml(s.nickname)}</span>` : "";
  const key = s.is_key_story ? '<span class="keytag" title="Key story">★</span>' : "";

  const head = document.createElement("button");
  head.type = "button";
  head.className = "sum-head";
  head.innerHTML =
    `${sym}<span class="sum-date">${fmtDate(s.started_at)}</span>${nick}` +
    `<span class="sum-name">${escapeHtml(s.name)}</span>${key}` +
    `<span class="chev">▸</span>`;

  // Animated expand/collapse: the body is a grid that transitions 0fr -> 1fr;
  // the inner wrapper clips overflow so height animates smoothly.
  const body = document.createElement("div");
  body.className = "sum-body";
  const inner = document.createElement("div");
  inner.className = "sum-inner";
  body.appendChild(inner);

  head.addEventListener("click", () => {
    const open = card.classList.toggle("open");
    head.setAttribute("aria-expanded", String(open));
    if (open && !inner.dataset.filled) {
      inner.appendChild(summaryBody(s));
      inner.dataset.filled = "1";
    }
  });

  card.appendChild(head);
  card.appendChild(body);
  return card;
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

// Album-hero header: large art on the left, topline info + per-region periods on
// the right; the decorated summary prose below.
function summaryBody(s) {
  const frag = document.createDocumentFragment();
  const u = state.meta.units[s.unit] || {};
  const fc = state.meta.characters[s.focus_character_id];

  const hero = document.createElement("div");
  hero.className = "sum-hero";

  // Album art with a graceful fallback chain: song jacket -> event logo -> unit
  // symbol. If the jacket fails to load (network/proxy hiccup), swap to the next
  // source instead of blanking the art. `data-fallbacks` is a "|"-joined queue the
  // onerror handler pops from; only after all fail do we mark the hero no-art.
  const artSources = [s.jacket_url, s.logo_url, u.symbol].filter(Boolean).map(proxied);
  const artHtml = artSources.length
    ? `<img class="sum-art" src="${artSources[0]}" alt="" ` +
      `data-fallbacks="${escapeHtml(artSources.slice(1).join("|"))}" ` +
      `onerror="var f=(this.dataset.fallbacks||'').split('|').filter(Boolean);` +
      `if(f.length){this.src=f.shift();this.dataset.fallbacks=f.join('|');}` +
      `else{this.closest('.sum-hero').classList.add('no-art');}">`
    : "";

  const rows = [`<div class="hero-title">${escapeHtml(s.name)}</div>`];
  const badges = [];
  if (u.name) {
    badges.push(
      `<span class="hero-unit" style="color:${u.color}">` +
        (u.symbol ? `<img src="${u.symbol}" alt="" onerror="this.style.display='none'">` : "") +
        `${escapeHtml(u.name)}</span>`
    );
  }
  if (s.is_key_story) badges.push('<span class="hero-key">★ key story</span>');
  if (badges.length) rows.push(`<div class="hero-row">${badges.join("")}</div>`);
  if (fc) {
    rows.push(
      `<div class="hero-row"><span class="hero-focus"><img src="${fc.icon}" alt="" ` +
        `onerror="this.style.display='none'"><b style="color:${fc.color}">${escapeHtml(fc.en)}</b>` +
        `</span><span class="hero-label">focus</span></div>`
    );
  }

  // Album art + song title stacked together on the left.
  const songHtml = s.song_title
    ? `<div class="sum-song">🎵 ${escapeHtml(s.song_title)}</div>`
    : "";
  const artCol = artHtml || songHtml ? `<div class="sum-artcol">${artHtml}${songHtml}</div>` : "";

  hero.innerHTML =
    artCol + `<div class="sum-info">${rows.join("")}${regionPeriods(s.regions)}</div>`;
  frag.appendChild(hero);

  const text = document.createElement("div");
  text.className = "answer-text";
  text.innerHTML = renderMarkdown(s.summary);
  decorateNames(text, new Set(s.characters || []));
  frag.appendChild(text);

  // Tier-1: lazy-loaded per-episode summaries.
  if (s.episode_count > 0) {
    const wrap = document.createElement("div");
    wrap.className = "episodes";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ep-toggle";
    btn.textContent = `▸ Episode breakdown (${s.episode_count})`;
    const list = document.createElement("div");
    list.className = "ep-list hidden";
    btn.addEventListener("click", async () => {
      const nowHidden = list.classList.toggle("hidden");
      btn.textContent = `${nowHidden ? "▸" : "▾"} Episode breakdown (${s.episode_count})`;
      if (!list.dataset.filled) {
        list.dataset.filled = "1";
        list.innerHTML = '<p class="empty">Loading…</p>';
        const eps = await fetch(`/api/episodes?arc=${encodeURIComponent(s.arc_id)}`)
          .then((r) => r.json())
          .catch(() => []);
        list.innerHTML = "";
        for (const ep of eps) {
          const d = document.createElement("div");
          d.className = "ep-item";
          const md = document.createElement("div");
          md.className = "answer-text";
          md.innerHTML = renderMarkdown(ep.summary);
          d.innerHTML = `<div class="ep-key">Episode ${escapeHtml(ep.episode)}</div>`;
          d.appendChild(md);
          list.appendChild(d);
        }
      }
    });
    wrap.appendChild(btn);
    wrap.appendChild(list);
    frag.appendChild(wrap);
  }
  return frag;
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
    el.appendChild(card);
  }
  if (!rows.length) el.innerHTML = '<p class="empty">No events for this filter.</p>';
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
        q.onclick = () => openExcerpt(cite);
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
        openExcerpt(c);
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
      if (c) openExcerpt(c);
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
    if (h) {
      const lvl = Math.min(Math.max(h[1].length, 2), 4); // ## -> h2, kept 2..4
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
    }
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
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
  // Highlight the quoted line within the full excerpt.
  const esc = (s) =>
    (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  let body = esc(cite.excerpt || cite.quote || "");
  if (cite.quote) {
    const q = esc(cite.quote);
    body = body.split(q).join(`<mark>${q}</mark>`);
  }
  const el = document.getElementById("sb-body");
  el.innerHTML = body;
  sb.classList.remove("hidden");
  const m = el.querySelector("mark");
  if (m) m.scrollIntoView({ block: "center" });
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
      } else if (evt.type === "done") {
        done = evt;
      }
    }
  }
  return done || { answer: text };
}

document.getElementById("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = document.getElementById("question");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  addMessage("user", q);
  const pending = addMessage("assistant", "");
  showThinking(pending);
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
