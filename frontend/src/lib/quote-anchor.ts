// Locating the quoted line inside a transcript, so the sidebar can scroll to it.
//
// A "scene" in this corpus is a whole episode — ~48 dialogue turns, up to 324 — so
// the line a citation refers to is normally far below the fold. Finding it is the
// hard part, not the scrolling: the quote and the transcript frequently disagree in
// ways that defeat string equality.
//
//   * The quote can be official-EN while the fetched transcript is Japanese (or the
//     reverse, when the EN CDN has the scene but the cached quote came from JP).
//   * Quotes are stored with the speaker prefix ("Honami: ..."), transcripts render
//     it separately, and the two don't always agree on which form was captured.
//   * Width (full- vs half-width punctuation), interior spacing and trailing
//     ellipses drift between the master DB, the asset CDN and our own extraction.
//
// So matching is tiered, strictest first, and turn-attributed citations can supply
// their conversational `window` as a positional fallback — those come from turn
// retrieval, which knows the anchor turn rather than guessing from text.

/** Renderable transcript rows: drop blanks and scene delimiters, keep order. */
export function transcriptRows(text: string): string[] {
  return text.split('\n').filter((l) => l.trim() && l.trim() !== '---');
}

/** Strip a leading "Speaker:" / "Speaker：" label, if present. */
function stripSpeaker(line: string): string {
  const m = /^[^:：]{1,24}[:：]\s*(.*)$/.exec(line.trim());
  return m ? m[1] : line.trim();
}

/**
 * Comparison key: speaker-free, width-normalised, whitespace- and punctuation-free.
 * Aggressive on purpose — the alternative is a silent miss, and these strings are
 * long enough that over-normalising doesn't realistically collide.
 */
function norm(line: string): string {
  return stripSpeaker(line)
    .normalize('NFKC')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[.,!?…‥。、！？"'“”'‘「」『』()（）]/g, '');
}

/** Shortest normalised length worth substring-matching; below this it's noise. */
const MIN_SUBSTRING = 8;

function findOne(rows: string[], quote: string): number {
  const raw = quote.trim();
  if (!raw) return -1;

  // 1. exact, as rendered
  const exact = rows.findIndex((l) => l.trim() === raw);
  if (exact >= 0) return exact;

  const target = norm(raw);
  if (!target) return -1;

  // 2. normalised equality — survives speaker prefixes, width and punctuation drift
  const normalised = rows.findIndex((l) => norm(l) === target);
  if (normalised >= 0) return normalised;

  // 3. containment either way — the quote may be a clause of a longer line, or
  //    carry trailing context the transcript line doesn't have
  if (target.length >= MIN_SUBSTRING) {
    const partial = rows.findIndex((l) => {
      const n = norm(l);
      return n.length >= MIN_SUBSTRING && (n.includes(target) || target.includes(n));
    });
    if (partial >= 0) return partial;
  }
  return -1;
}

/**
 * Index of the row to scroll to, or -1 when the quote can't be located.
 *
 * `window` is the surrounding turns carried by turn-attributed citations; it's
 * tried only after the quote itself fails, and lands the reader in the right
 * exchange even when the exact line is unmatchable (typically a cross-lingual
 * citation). Callers should surface -1 rather than silently showing an untinted
 * transcript.
 */
export function findQuoteRow(rows: string[], quote?: string, window?: string[]): number {
  if (quote) {
    const hit = findOne(rows, quote);
    if (hit >= 0) return hit;
  }
  for (const line of window ?? []) {
    const hit = findOne(rows, line);
    if (hit >= 0) return hit;
  }
  return -1;
}
