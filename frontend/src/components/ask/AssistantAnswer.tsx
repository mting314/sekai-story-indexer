import { css } from 'styled-system/css';
import { Stack } from 'styled-system/jsx';
import { Markdown } from '~/components/Markdown';
import { SectionTabs } from '~/components/SectionTabs';
import { useStore } from '~/lib/store';
import type { QueryResult } from '~/types/api';

const BACKEND_LABEL: Record<string, string> = {
  derived: 'derived · summaries + live quotes (no LLM)',
  full: 'full RAG · Gemini',
  summary: 'pre-computed event summary',
  local: 'local · AI-synthesized'
};

// Rich render of a finished answer (ported from renderAssistant): optional event header,
// notice banner, tabbed sections OR answer_parts (text + quotes), sources, backend note.
// `onCite(ref)` opens the citation in the shared Sidebar.
export function AssistantAnswer({ res, onCite }: { res: QueryResult; onCite: (ref: number) => void }) {
  const { events } = useStore();

  const arc = res.focus?.arcs?.[0] ?? res.scope?.arc_id ?? res.citations?.[0]?.arc_id;
  const ev = arc ? events.find((e) => e.arc_slug === arc) : undefined;
  const parts = res.answer_parts?.length ? res.answer_parts : [{ type: 'text' as const, text: res.answer }];
  const quotes = parts.filter((p) => p.type === 'quote');
  const texts = parts.filter((p) => p.type === 'text');
  const hasTabs = !!res.section_order?.length && !!res.sections;

  return (
    <Stack gap="2">
      {ev && <div className={css({ fontSize: 'sm', fontWeight: 'bold', color: 'accent.text' })}>{ev.name}{ev.nickname ? ` · ${ev.nickname}` : ''}</div>}

      {res.notice && (
        <div className={css({ p: '2', rounded: 'md', bg: 'yellow.400/15', color: 'yellow.300', fontSize: 'xs' })}>{res.notice}</div>
      )}

      {hasTabs ? (
        <SectionTabs order={res.section_order!} sections={res.sections!} onCite={onCite} />
      ) : (
        texts.map((p, i) => <Markdown key={i} text={p.text} onCite={onCite} />)
      )}

      {quotes.length > 0 && (
        <Stack gap="1">
          <div className={css({ fontSize: 'xs', fontWeight: 'bold', color: 'fg.muted', textTransform: 'uppercase', letterSpacing: '0.04em' })}>
            {res.generated ? 'Supporting quotes' : 'Retrieved excerpts'}
          </div>
          {quotes.map((q, i) => (
            <blockquote key={i}
              onClick={() => q.ref != null && onCite(q.ref)}
              className={css({ borderLeftWidth: '3px', borderColor: 'accent.default', pl: '2', py: '1', fontSize: 'sm', color: 'fg.default', cursor: q.ref != null ? 'pointer' : 'default', bg: 'bg.subtle', rounded: 'sm' })}>
              {q.text_en || q.text}{q.ref != null && <span className={css({ color: 'accent.text', fontWeight: 600 })}> [{q.ref}]</span>}
            </blockquote>
          ))}
        </Stack>
      )}

      {!!res.citations?.length && (
        <div className={css({ fontSize: 'xs', color: 'fg.muted' })}>
          Sources:{' '}
          {res.citations.map((c) => (
            <a key={c.ref} href="#" onClick={(e) => { e.preventDefault(); onCite(c.ref); }}
              className={css({ color: 'accent.text', mr: '2', cursor: 'pointer', _hover: { textDecoration: 'underline' } })}>
              [{c.ref}] {c.label ?? c.arc_id}
            </a>
          ))}
        </div>
      )}

      {res.backend && res.backend !== 'command' && (
        <div className={css({ fontSize: '2xs', color: 'fg.subtle' })}>via {BACKEND_LABEL[res.backend] ?? res.backend}</div>
      )}
    </Stack>
  );
}
