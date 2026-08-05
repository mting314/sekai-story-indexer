// Sekai.best / local asset URLs.
//
// Song jackets live on the sekai.best CDN. We route them through the sekai-story-indexer
// `/api/img` proxy (same-origin under the /setlist iframe) so they load even when the
// browser can't reach the CDN directly (restricted networks) — the proxy is host-allowlisted
// to storage.sekai.best. Character + unit icons are the PNGs the host app already ships under
// /static (the CDN has no unit-logo / game-character-icon endpoints), so we link those directly.

const CDN = 'https://storage.sekai.best/sekai-jp-assets';

// Prefix for the host app's static + proxy routes. Empty = same origin (the iframe case).
// Override for standalone dev against a running story-indexer, e.g. PUBLIC_HOST=http://localhost:8000.
const HOST = (import.meta.env?.PUBLIC_HOST as string | undefined) ?? '';

const proxied = (u: string) => `${HOST}/api/img?u=${encodeURIComponent(u)}`;

/** Song jacket URL (via the image proxy). Undefined when the song has no assetbundle. */
export function jacketUrl(assetbundleName?: string): string | undefined {
  if (!assetbundleName) return undefined;
  return proxied(`${CDN}/music/jacket/${assetbundleName}/${assetbundleName}.webp`);
}

/** Unit logo/symbol PNG (shipped by the host app under /static/units). */
export const unitIcon = (unit: string): string => `${HOST}/static/units/${unit}.png`;

/** Game-character icon PNG (shipped by the host app under /static/chara, keyed by id). */
export const charaIcon = (id: number | string): string => `${HOST}/static/chara/${id}.png`;
