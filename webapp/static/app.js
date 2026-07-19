// Sekai Story Indexer — minimal vanilla-JS front end.
// Timeline reads /api/events; chat posts /api/query. No build step.

const state = { events: [], units: [], activeUnit: "all", scopeEventId: null, history: [] };

async function boot() {
  const [units, events] = await Promise.all([
    fetch("/api/units").then((r) => r.json()),
    fetch("/api/events").then((r) => r.json()),
  ]);
  state.units = units;
  state.events = events;
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
      renderTimeline();
    };
    el.appendChild(b);
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
