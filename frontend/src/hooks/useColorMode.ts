// Light/dark color mode. The class is applied pre-paint by the inline script in +Head
// (no flash); this hook reads/flips it and persists the choice.
import { useEffect, useState } from 'react';

export type ColorMode = 'light' | 'dark';

const read = (): ColorMode =>
  typeof document !== 'undefined' && document.documentElement.classList.contains('dark') ? 'dark' : 'light';

export function useColorMode() {
  const [mode, setMode] = useState<ColorMode>('light');
  useEffect(() => setMode(read()), []);

  const toggle = () => {
    const next: ColorMode = read() === 'dark' ? 'light' : 'dark';
    const el = document.documentElement;
    el.classList.toggle('dark', next === 'dark');
    el.classList.toggle('light', next === 'light');
    try {
      localStorage.setItem('color-mode', next);
    } catch {
      /* ignore */
    }
    setMode(next);
  };

  return { mode, toggle };
}
