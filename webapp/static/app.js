// Sekai Story Indexer — minimal vanilla-JS front end.
// Timeline reads /api/events; chat posts /api/query. No build step.

const state = {
  events: [], units: [], activeUnit: "all", scopeEventId: null, history: [],
  view: "timeline", summaries: null, meta: { characters: {}, units: {} }, entityRe: null,
};

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
    state.summaries = await fetch("/api/summaries").then((r) => r.json());
  }
  const rows = state.summaries.filter(
    (s) => state.activeUnit === "all" || s.unit === state.activeUnit
  );
  if (!rows.length) {
    el.innerHTML =
      '<p class="empty">No event summaries yet — run <code>indexer ingest --summaries event</code>.</p>';
    return;
  }
  el.innerHTML = "";
  const list = document.createElement("div");
  list.className = "sum-list";
  for (const s of rows) list.appendChild(summaryCard(s));
  el.appendChild(list);
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

  const body = document.createElement("div");
  body.className = "sum-body hidden";

  head.addEventListener("click", () => {
    const collapsed = body.classList.toggle("hidden");
    head.classList.toggle("open", !collapsed);
    if (!body.dataset.filled) {
      body.appendChild(summaryBody(s));
      body.dataset.filled = "1";
    }
  });

  card.appendChild(head);
  card.appendChild(body);
  return card;
}

function summaryBody(s) {
  const frag = document.createDocumentFragment();

  const chips = [];
  const fc = state.meta.characters[s.focus_character_id];
  if (fc) {
    chips.push(
      `<span class="sum-chip focus"><img class="cic" src="${fc.icon}" alt="" ` +
        `onerror="this.style.display='none'"><b style="color:${fc.color}">` +
        `${escapeHtml(fc.en)}</b></span>`
    );
  }
  const dur = durationLabel(s.started_at, s.ended_at);
  if (dur) chips.push(`<span class="sum-chip">🗓 ${dur}</span>`);
  if (s.song_title) chips.push(`<span class="sum-chip">🎵 ${escapeHtml(s.song_title)}</span>`);
  if (chips.length) {
    const meta = document.createElement("div");
    meta.className = "sum-meta";
    meta.innerHTML = chips.join("");
    frag.appendChild(meta);
  }

  const text = document.createElement("div");
  text.className = "answer-text";
  text.innerHTML = renderMarkdown(s.summary);
  decorateNames(text);
  frag.appendChild(text);
  return frag;
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
  for (const c of Object.values(state.meta.characters || {})) {
    const ent = { kind: "char", color: c.color, icon: c.icon };
    entries.push([c.en, ent]);
    const given = c.en.split(" ")[0];
    if (given && given.length >= 3 && given !== c.en) entries.push([given, ent]);
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
  const alts = entries.map(([n]) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  state.entityRe = alts.length
    ? new RegExp("(?<![A-Za-z0-9])(" + alts.join("|") + ")(?![A-Za-z0-9])", "gi")
    : null;
}

// Walk text nodes only (never tag attributes/citations) and wrap entity
// mentions with a colored span + inline icon.
function decorateNames(root) {
  if (!state.entityRe) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement && node.parentElement.closest("a,.ent,code")) continue;
    state.entityRe.lastIndex = 0;
    if (state.entityRe.test(node.nodeValue)) targets.push(node);
  }
  for (const tn of targets) {
    const txt = tn.nodeValue;
    const frag = document.createDocumentFragment();
    state.entityRe.lastIndex = 0;
    let last = 0;
    let m;
    while ((m = state.entityRe.exec(txt))) {
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
      ? `<img class="logo" loading="lazy" src="${e.logo_url}" alt="" onerror="this.style.display='none'">`
      : "";
    const nick = e.nickname ? `<span class="nick">${e.nickname}</span>` : "";
    const song = e.song_title ? `<div class="song">🎵 ${e.song_title}</div>` : "";
    const focus = e.focus_character ? `<div class="focus">★ ${e.focus_character}</div>` : "";
    const status = e.indexed
      ? '<span class="status-dot indexed" title="Queryable in chat now"></span>'
      : '<span class="status-dot pending" title="On the timeline; chat-answerable after the next ingest"></span>';
    card.innerHTML = `
      ${logo}
      <div class="meta">
        <div class="top">${status}<span class="date">${fmtDate(e.started_at)}</span>${nick}
          ${e.is_key_story ? '<span class="key-badge">key</span>' : ""}</div>
        <div class="name">${e.name}</div>
        ${focus}${song}
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
  const hint = document.getElementById("scope-hint");
  hint.classList.remove("hidden");
  hint.innerHTML = `Scoped to <b>${e.name}</b>${e.nickname ? ` (${e.nickname})` : ""} ·
    <a href="#" id="clear-scope">clear</a>`;
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

// Render a rich assistant answer: text runs + clickable quote blocks that open
// the excerpt sidebar, plus a compact source list.
function renderAssistant(container, res) {
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
function renderMarkdown(src) {
  const esc = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) =>
    esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
      // clickable numbered citations: [1] / [2][3]  (also inside `code`/brackets)
      .replace(/\[(\d+)\]/g, '<a href="#" class="cite" data-ref="$1">[$1]</a>');
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
    if (h) out.push(`<h4>${inline(h[2])}</h4>`);
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

document.getElementById("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = document.getElementById("question");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  addMessage("user", q);
  const pending = addMessage("assistant", "…");
  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: q,
        unit: state.activeUnit === "all" ? null : state.activeUnit,
        event_id: state.scopeEventId,
        history: state.history.slice(-6), // conversation memory
      }),
    }).then((r) => r.json());
    if (res.error && !res.answer) {
      pending.textContent = `⚠ ${res.error}`;
    } else {
      renderAssistant(pending, res);
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
