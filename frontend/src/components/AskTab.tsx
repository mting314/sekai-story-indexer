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

  useEffect(() => { getCommands().then(setCommands).catch(() => setCommands([])); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }); }, [messages]);

  // Consume a question pushed from another tab (Timeline click, quick-action bridge).
  useEffect(() => {
    if (store.pendingQuestion) {
      const q = store.consumePending();
      if (q) submit(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.pendingQuestion]);

  const buildHistory = () =>
    messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({ role: m.role, text: m.result?.answer ?? m.text ?? '' }))
      .slice(-6);

  const updateMsg = (id: number, fn: (m: Msg) => Msg) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  async function submit(raw: string) {
    const q = raw.trim();
    if (!q || busy) return;
    setBusy(true);
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

    try {
      let res: QueryResult;
      if (q.startsWith('/')) {
        res = await command(q, body.unit);
      } else {
        res = await queryStream(body, (evt) => {
          if (evt.type === 'delta') updateMsg(asstId, (m) => ({ ...m, text: (m.text ?? '') + evt.text }));
        }).catch(() => query(body));
      }
      updateMsg(asstId, (m) => ({ ...m, result: res, streaming: false, text: res.answer }));
      // Server focus chip (manual scope takes precedence).
      if (!store.scope) {
        if ('focus' in res) setFocusChip(res.focus ?? null);
      }
    } catch (e) {
      updateMsg(asstId, (m) => ({ ...m, streaming: false, text: `⚠ ${String(e)}` }));
    } finally {
      setBusy(false);
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
    if (taRef.current) { taRef.current.style.height = 'auto'; taRef.current.style.height = Math.min(taRef.current.scrollHeight, 160) + 'px'; }
  };

  const applyCommand = (c: SlashCommand) => {
    setInput(`/${c.command}${c.args ? ' ' : ' '}`);
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
    // terminal-style history recall
    const ta = taRef.current;
    if (e.key === 'ArrowUp' && ta && ta.selectionStart === 0 && histIdx.current > 0) {
      e.preventDefault(); histIdx.current -= 1; setInput(inputHistory.current[histIdx.current] ?? '');
    } else if (e.key === 'ArrowDown' && ta && ta.selectionStart === (input.length) && histIdx.current < inputHistory.current.length) {
      e.preventDefault(); histIdx.current += 1; setInput(inputHistory.current[histIdx.current] ?? '');
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
