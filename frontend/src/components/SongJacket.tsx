import { useState } from 'react';
import { css } from 'styled-system/css';
import { jacketUrl } from '~/lib/assets';
import { useSongById, colorBarBackground, songColors } from '~/hooks/useData';

// Square song thumbnail: the sekai.best jacket (via proxy), falling back to a
// unit-colored tile when the song has no art / the image fails to load.
export function SongJacket({ id, size = 44 }: { id: string; size?: number }) {
  const [broken, setBroken] = useState(false);
  const song = useSongById().get(id);
  const url = broken ? undefined : jacketUrl(song?.assetbundleName);
  const box = css({ rounded: 'md', flexShrink: '0', objectFit: 'cover', overflow: 'hidden' });
  if (url) {
    return (
      <img
        src={url}
        alt=""
        width={size}
        height={size}
        className={box}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <div
      aria-hidden
      className={box}
      style={{ width: size, height: size, background: colorBarBackground(songColors(id)) }}
    />
  );
}
