// Small formatting helpers (ported from the vanilla app).

export const fmtDate = (ms?: number): string =>
  ms ? new Date(ms).toISOString().slice(0, 10) : '';

export const fmtDateLong = (ms?: number): string =>
  ms ? new Date(ms).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '';

// Human episode label from a file slug — "Episode N" when numbered, else de-slugged title
// (never the raw slug).
export const episodeLabel = (slug: string): string => {
  const m = /^(\d+)/.exec(slug || '');
  if (m) return `Episode ${parseInt(m[1], 10)}`;
  const words = (slug || '').replace(/[-_]+/g, ' ').trim();
  return words ? words.replace(/\b\w/g, (c) => c.toUpperCase()) : 'Episode';
};
