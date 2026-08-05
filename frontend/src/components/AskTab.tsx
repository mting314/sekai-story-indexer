// The Ask/chat surface (ported from the vanilla chat). Streaming answers via /api/query/stream
// (progressive plain text → rich swap on done), /api/query fallback, slash-command menu,
// terminal-style history recall, quick-actions, and manual-scope / server-focus chips.
import { useEffect, useRef, useState } from 'react';
import { css } from 'styled-system/css';
import { Stack } from 'styled-system/jsx';
import { command, getCommands, query, queryStream, rotateSession } from '~/lib/api';
import { useStore } from '~/lib/store';
import { useSidebar } from '~/components/Sidebar';
import { AssistantAnswer } from '~/components/ask/AssistantAnswer';
import type { Focus, QueryResult, SlashCommand } from '~/types/api';

interface Msg { id: number; role: 'user' | 'assistant' | 'system'; text?: string; result?: QueryResult; streaming?: boolean }

const QUICK_ACTIONS = [
  { label: 'Summarize this event', q: 'Summarize this event.' },
  { label: "What's the conclusion?", q: 'What happens at the end of this event?' },
  { label: 'Who\'s the focus character?', q: 'Who is the focus character of this event, and what is their arc?' },
  { label: 'Song & story', q: 'How does the theme song relate to the story?' }
];

let _id = 0;
const nextId = () => ++_id;

export function AskTab() {
  const store = useStore();
  const sidebar = useSidebar();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [menu, setMenu] = useState<{ open: boolean; items: SlashCommand[]; active: number }>({ open: false, items: [], active: 0 });
  const [focusChip, setFocusChip] = useState<Focus | null>(null);
  const inputHistory = useRef<string[]>([]);
  const histIdx = useRef(0);
  const lastQuery = useRef('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false); // synchronous in-flight guard (setBusy is async → not reliable alone)

  useEffect(() => { getCommands().then(setCommands).catch(() => setCommands([])); }, []);
  useEffect(() => () => abortRef.current?.abort(), []); // cancel any in-flight stream on unmount
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }); }, [messages]);

  const buildHistory = () =>
    messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({ role: m.role, text: m.result?.answer ?? m.text ?? '' }))
      .slice(-6);

  const updateMsg = (id: number, fn: (m: Msg) => Msg) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  const autosize = () => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  };

  // Terminal-style history recall: set the input to a prior entry, put the caret at the end,
  // and resize the textarea for multi-line entries. idx === history length clears the input.
  const recall = (idx: number) => {
    histIdx.current = idx;
    setInput(inputHistory.current[idx] ?? '');
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (ta) ta.selectionStart = ta.selectionEnd = ta.value.length;
      autosize();
    });
  };

  async function submit(raw: string) {
    const q = raw.trim();
    if (!q || busyRef.current) return; // ref guard: blocks same-tick double-submit (setBusy is async)
    busyRef.current = true;
    setBusy(true);
    abortRef.current?.abort(); // cancel any prior in-flight stream before starting a new one
    setMenu((s) => ({ ...s, open: false }));
    if (inputHistory.current[inputHistory.current.length - 1] !== q) inputHistory.current.push(q);
    histIdx.current = inputHistory.current.length;
    lastQuery.current = q;

    const asstId = nextId();
    setMessages((ms) => [...ms, { id: nextId(), role: 'user', text: q }, { id: asstId, role: 'assistant', streaming: true, text: '' }]);
    setInput('');
    if (taRef.current) taRef.current.style.height = 'auto';

    const body = {
      question: q,
      unit: store.activeUnit === 'all' ? null : store.activeUnit,
      event_id: store.scope?.event_id ?? null,
      history: buildHistory()
    };

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      let res: QueryResult;
      if (q.startsWith('/')) {
        res = await command(q, body.unit);
      } else {
        try {
          res = await queryStream(body, (evt) => {
            if (evt.type === 'delta') updateMsg(asstId, (m) => ({ ...m, text: (m.text ?? '') + evt.text }));
          }, ac.signal);
        } catch {
          if (ac.signal.aborted) return; // unmounted mid-stream — drop silently
          res = await query(body); // SSE failed (e.g. proxy-buffered) → non-streaming fallback
        }
      }
      if (ac.signal.aborted) return;
      updateMsg(asstId, (m) => ({ ...m, result: res, streaming: false, text: res.answer }));
      // Server focus chip (manual scope takes precedence).
      if (!store.scope && 'focus' in res) setFocusChip(res.focus ?? null);
    } catch (e) {
      if (!ac.signal.aborted) updateMsg(asstId, (m) => ({ ...m, streaming: false, text: `⚠ ${String(e)}` }));
    } finally {
      if (!ac.signal.aborted) { busyRef.current = false; setBusy(false); }
    }
  }

  const onCiteFor = (res?: QueryResult) => (ref: number) => {
    const c = res?.citations?.find((x) => x.ref === ref);
    if (c) sidebar.openCitation(c, lastQuery.current);
  };

  /* ---- slash menu + keyboard ---- */
  const onInputChange = (v: string) => {
    setInput(v);
    const m = /^\/([a-z]*)$/i.exec(v);
    if (m) {
      const items = commands.filter((c) => c.command.startsWith(m[1].toLowerCase()));
      setMenu({ open: items.length > 0, items, active: 0 });
    } else {
      setMenu((s) => ({ ...s, open: false }));
    }
    autosize();
  };

  const applyCommand = (c: SlashCommand) => {
    setInput(`/${c.command} `);
    setMenu((s) => ({ ...s, open: false }));
    taRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menu.open) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMenu((s) => ({ ...s, active: (s.active + 1) % s.items.length })); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMenu((s) => ({ ...s, active: (s.active - 1 + s.items.length) % s.items.length })); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); applyCommand(menu.items[menu.active]); return; }
      if (e.key === 'Escape') { setMenu((s) => ({ ...s, open: false })); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(input); return; }
    // terminal-style history recall — only when the caret is collapsed at the start (↑) / end (↓)
    const ta = taRef.current;
    const atStart = !!ta && ta.selectionStart === 0 && ta.selectionEnd === 0;
    const atEnd = !!ta && ta.selectionStart === input.length && ta.selectionEnd === input.length;
    if (e.key === 'ArrowUp' && atStart && histIdx.current > 0) {
      e.preventDefault(); recall(histIdx.current - 1);
    } else if (e.key === 'ArrowDown' && atEnd && histIdx.current < inputHistory.current.length) {
      e.preventDefault(); recall(histIdx.current + 1);
    }
  };

  const showQuick = !!store.scope || !!focusChip;
  const scopeLabel = store.scope ? (store.scope.name || store.scope.arc_slug) : focusChip?.label;

  const clearScope = () => {
    if (store.scope) { store.setScope(null); }
    else { rotateSession(); setFocusChip(null); }
  };

  return (
    <Stack gap="3">
      <div className={css({ maxH: '58vh', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '3', pr: '1' })}>
        {messages.length === 0 && (
          <p className={css({ color: 'fg.muted', fontSize: 'sm' })}>Ask about the story, or scope to an event from the Timeline. Try a nickname like <code>kasa5</code>, or <code>/help</code>.</p>
        )}
        {messages.map((m) => <MessageBubble key={m.id} m={m} onCite={onCiteFor(m.result)} />)}
        <div ref={endRef} />
      </div>

      {scopeLabel && (
        <div className={css({ display: 'inline-flex', alignItems: 'center', gap: '2', alignSelf: 'flex-start', px: '2.5', py: '1', rounded: 'full', bg: 'accent.subtle', color: 'accent.text', fontSize: 'xs', fontWeight: 'semibold' })}>
          Focused: {scopeLabel}
          <button aria-label="Clear focus" onClick={clearScope} className={css({ cursor: 'pointer', _hover: { color: 'fg.default' } })}>×</button>
        </div>
      )}

      {showQuick && (
        <div className={css({ display: 'flex', gap: '1.5', flexWrap: 'wrap' })}>
          {QUICK_ACTIONS.map((qa) => (
            <button key={qa.label} onClick={() => submit(qa.q)} disabled={busy}
              className={css({ px: '2.5', py: '1', rounded: 'full', fontSize: 'xs', cursor: 'pointer', borderWidth: '1px', borderColor: 'border.default', bg: 'bg.subtle', _hover: { bg: 'bg.emphasized' }, _disabled: { opacity: 0.5 } })}>
              {qa.label}
            </button>
          ))}
        </div>
      )}

      <div className={css({ position: 'relative' })}>
        {menu.open && (
          <div className={css({ position: 'absolute', bottom: '100%', left: '0', mb: '1', w: 'full', bg: 'bg.default', borderWidth: '1px', borderColor: 'border.default', rounded: 'lg', boxShadow: 'lg', overflow: 'hidden', zIndex: '10' })}>
            {menu.items.map((c, i) => (
              <button key={c.command} onMouseDown={(e) => { e.preventDefault(); applyCommand(c); }}
                className={css({ display: 'block', w: 'full', textAlign: 'left', px: '3', py: '1.5', fontSize: 'sm', cursor: 'pointer', bg: i === menu.active ? 'bg.subtle' : 'transparent' })}>
                <span className={css({ color: 'accent.text', fontWeight: 'bold' })}>/{c.command}</span>{' '}
                <span className={css({ color: 'fg.subtle' })}>{c.args}</span>
                <span className={css({ display: 'block', fontSize: 'xs', color: 'fg.muted' })}>{c.desc}</span>
              </button>
            ))}
          </div>
        )}
        <div className={css({ display: 'flex', gap: '2', alignItems: 'flex-end' })}>
          <textarea
            ref={taRef}
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Ask about the story… or type /help for commands"
            className={css({ flex: '1', p: '2.5', rounded: 'lg', bg: 'bg.subtle', borderWidth: '1px', borderColor: 'border.default', fontSize: 'sm', resize: 'none', maxH: '160px', _focus: { borderColor: 'accent.default', outline: 'none' } })}
          />
          <button onClick={() => submit(input)} disabled={busy}
            className={css({ px: '4', py: '2.5', rounded: 'lg', fontWeight: 'semibold', fontSize: 'sm', cursor: 'pointer', bg: 'accent.default', color: 'accent.fg', _disabled: { opacity: 0.5 } })}>
            {busy ? '…' : 'Ask'}
          </button>
        </div>
      </div>
    </Stack>
  );
}

function MessageBubble({ m, onCite }: { m: Msg; onCite: (ref: number) => void }) {
  if (m.role === 'user') {
    return <div className={css({ alignSelf: 'flex-end', maxW: '85%', px: '3', py: '2', rounded: 'lg', bg: 'accent.default', color: 'accent.fg', fontSize: 'sm', whiteSpace: 'pre-wrap' })}>{m.text}</div>;
  }
  if (m.role === 'system') {
    return <div className={css({ alignSelf: 'center', fontSize: 'xs', color: 'fg.muted', fontStyle: 'italic' })}>{m.text}</div>;
  }
  // assistant
  return (
    <div className={css({ alignSelf: 'flex-start', maxW: '95%', px: '3', py: '2', rounded: 'lg', bg: 'bg.subtle', borderWidth: '1px', borderColor: 'border.subtle' })}>
      {m.streaming || !m.result
        ? <div className={css({ fontSize: 'sm', whiteSpace: 'pre-wrap', color: m.text ? 'fg.default' : 'fg.muted' })}>{m.text || 'Thinking…'}</div>
        : <AssistantAnswer res={m.result} onCite={onCite} />}
    </div>
  );
}
