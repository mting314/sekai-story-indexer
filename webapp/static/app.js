// Sekai Story Indexer — minimal vanilla-JS front end.
// Timeline reads /api/events; chat posts /api/query. No build step.

const state = { events: [], units: [], activeUnit: "all", scopeEventId: null };

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
      }),
    }).then((r) => r.json());
    pending.textContent = res.answer || `⚠ ${res.error || "no answer"}`;
  } catch (err) {
    pending.textContent = `⚠ ${err}`;
  }
});

boot();
