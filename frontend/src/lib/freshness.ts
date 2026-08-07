// Timeline freshness badges — the UI face of the two-tier ingestion pipeline.
//
// Tier 1 (keyless: fetch + index) makes an event queryable; Tier 2 (LLM summaries)
// lands later, sometimes days later while a backlog drains. Without a badge the
// gap reads as "the app is broken for this event", so we say which tier it's in:
//
//   NEW              — first seen by an ingest run within the freshness window
//   Summary pending  — transcript indexed and searchable, LLM summary not written yet
//
// Pure + data-only (no JSX) so it's unit-testable and the card stays presentational.
import type { EventRow } from '~/types/api';

export type BadgeTone = 'new' | 'pending';

export interface Badge {
  tone: BadgeTone;
  label: string;
  title: string;
}

export function eventBadges(e: EventRow): Badge[] {
  const badges: Badge[] = [];
  if (e.is_new) {
    badges.push({
      tone: 'new',
      label: 'NEW',
      title: 'Newly ingested — added by a recent ingest run'
    });
  }
  // Only meaningful once the transcript is actually there; an unfetched event is
  // already marked "indexing pending" by the card's status dot.
  if (e.summary_status === 'pending' && e.indexed) {
    badges.push({
      tone: 'pending',
      label: 'Summary pending',
      title: 'Transcript indexed and searchable — the LLM summary hasn’t been generated yet'
    });
  }
  return badges;
}
