import { useState } from 'react';
import { css } from 'styled-system/css';
import { Markdown } from '~/components/Markdown';
import { useSidebar } from '~/components/Sidebar';
import { episodeLabel } from '~/lib/format';
import type { Hierarchy, HierNode } from '~/types/hier';

// One episode under an event: a collapsible summary (if any) + a transcript link that opens
// the shared sidebar. `arc` is the parent event's arc_slug; the episode file slug is node.label.
export function EpisodeRow({ node, arc, hier }: { node: HierNode; arc: string; hier: Hierarchy }) {
  const [open, setOpen] = useState(false);
  const { openTranscript } = useSidebar();
  const summary = node.summaryId ? hier.summaries[node.summaryId] : undefined;
  const label = episodeLabel(node.label);

  return (
    <div className={css({ borderTopWidth: '1px', borderColor: 'border.subtle', py: '1.5' })}>
      <div className={css({ display: 'flex', alignItems: 'center', gap: '2' })}>
        {summary ? (
          <button onClick={() => setOpen((o) => !o)} className={css({ flex: '1', textAlign: 'left', cursor: 'pointer', fontSize: 'sm', display: 'flex', gap: '2', alignItems: 'center', _hover: { color: 'accent.text' } })}>
            <span className={css({ color: 'fg.subtle' })}>{open ? '▾' : '▸'}</span> {label}
          </button>
        ) : (
          <span className={css({ flex: '1', fontSize: 'sm', color: 'fg.muted' })}>{label}</span>
        )}
        <button
          onClick={() => openTranscript(arc, node.label, label)}
          className={css({ fontSize: 'xs', color: 'accent.text', cursor: 'pointer', whiteSpace: 'nowrap', _hover: { textDecoration: 'underline' } })}
        >📄 transcript</button>
      </div>
      {open && summary && (
        <div className={css({ pl: '4', pt: '2' })}>
          {summary.sectionOrder.map((label2) => (
            <div key={label2} className={css({ mb: '2' })}>
              <div className={css({ fontSize: 'xs', fontWeight: 'bold', color: 'fg.muted', textTransform: 'uppercase', letterSpacing: '0.04em', mb: '1' })}>{label2}</div>
              <Markdown text={summary.sections[label2] ?? ''} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
