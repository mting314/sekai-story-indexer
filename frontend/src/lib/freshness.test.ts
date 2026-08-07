import { expect, test, describe } from 'bun:test';
import { eventBadges } from './freshness';
import type { EventRow } from '~/types/api';

const ev = (over: Partial<EventRow> = {}): EventRow =>
  ({ event_id: 1, arc_slug: '0001-x', name: 'Event', unit: 'leo_need', indexed: true, ...over }) as EventRow;

describe('eventBadges', () => {
  test('a complete, settled event gets no badges', () => {
    expect(eventBadges(ev({ summary_status: 'complete' }))).toEqual([]);
  });

  test('is_new yields a NEW badge', () => {
    const badges = eventBadges(ev({ is_new: true, summary_status: 'complete' }));
    expect(badges.map((b) => b.tone)).toEqual(['new']);
  });

  test('indexed-but-unsummarized yields Summary pending', () => {
    const badges = eventBadges(ev({ summary_status: 'pending' }));
    expect(badges.map((b) => b.tone)).toEqual(['pending']);
    expect(badges[0].label).toBe('Summary pending');
  });

  test('a freshly ingested unsummarized event shows both, NEW first', () => {
    const badges = eventBadges(ev({ is_new: true, summary_status: 'pending' }));
    expect(badges.map((b) => b.tone)).toEqual(['new', 'pending']);
  });

  test('no Summary pending for an event with no transcript on disk', () => {
    // summary_status 'none' (not fetched) — the card's status dot already says
    // "indexing pending"; a second badge would be noise.
    expect(eventBadges(ev({ indexed: false, summary_status: 'none' }))).toEqual([]);
  });

  test('pending status without indexed text is still suppressed', () => {
    expect(eventBadges(ev({ indexed: false, summary_status: 'pending' }))).toEqual([]);
  });

  test('an events payload from an older server (no freshness fields) is inert', () => {
    expect(eventBadges(ev())).toEqual([]);
  });
});
