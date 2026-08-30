import { describe, expect, test } from 'bun:test';
import { findQuoteRow, transcriptRows } from './quote-anchor';

const EPISODE = [
  'Rin: Ichi! Whatcha lookin\' at? Is that your schedule?',
  'Ichika: Yeah, I just got word about my next work shifts, so I\'m writing them in.',
  'Rin: Whoa! Between practice and work, your schedule\'s packed!',
  'Ichika: Oh, that\'s when they sell yakisoba buns at school.',
];

describe('transcriptRows', () => {
  test('drops blanks and scene delimiters, preserves order', () => {
    expect(transcriptRows('a\n\n---\nb\n   \nc')).toEqual(['a', 'b', 'c']);
  });
});

describe('findQuoteRow', () => {
  test('exact line match', () => {
    expect(findQuoteRow(EPISODE, EPISODE[2])).toBe(2);
  });

  test('quote carries the speaker prefix, transcript row does not', () => {
    const rows = ['Yeah, I just got word about my next work shifts, so I\'m writing them in.'];
    expect(findQuoteRow(rows, 'Ichika: Yeah, I just got word about my next work shifts, so I\'m writing them in.')).toBe(0);
  });

  test('punctuation and width drift still matches', () => {
    const rows = ['穂波: 弟もいるから、慣れてるだけだよ'];
    expect(findQuoteRow(rows, '穂波：弟もいるから、慣れてるだけだよ…')).toBe(0);
  });

  test('interior spacing drift still matches', () => {
    const rows = ['Honami: But the brother said he won\'t be returning, so there\'s no guarantee.'];
    expect(findQuoteRow(rows, 'But  the brother  said he won\'t be returning, so there\'s no guarantee')).toBe(0);
  });

  test('quote is a clause of a longer transcript line', () => {
    expect(findQuoteRow(EPISODE, 'they sell yakisoba buns at school')).toBe(3);
  });

  test('a short fragment does not match on substring alone', () => {
    // "at" would otherwise appear in several rows — below MIN_SUBSTRING, so ignored
    expect(findQuoteRow(EPISODE, 'at')).toBe(-1);
  });

  test('unmatchable quote returns -1 rather than guessing', () => {
    expect(findQuoteRow(EPISODE, 'A line from a completely different episode.')).toBe(-1);
  });

  test('falls back to the conversational window when the quote itself misses', () => {
    // the cross-lingual case: EN quote against a JP transcript, but the window
    // carries lines that do appear
    const jp = ['咲希: おはよう', '穂波: 弟もいるから、慣れてるだけだよ', '志歩: …………'];
    const hit = findQuoteRow(jp, 'I have a younger brother, so I\'m used to it.', [
      'Saki: good morning',
      '穂波: 弟もいるから、慣れてるだけだよ',
    ]);
    expect(hit).toBe(1);
  });

  test('window is only consulted after the quote fails', () => {
    const hit = findQuoteRow(EPISODE, EPISODE[0], [EPISODE[3]]);
    expect(hit).toBe(0);
  });

  test('no quote and no window means no anchor', () => {
    expect(findQuoteRow(EPISODE)).toBe(-1);
    expect(findQuoteRow(EPISODE, '', [])).toBe(-1);
  });
});
