import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import vike from 'vike/plugin';
import tsconfigPaths from 'vite-tsconfig-paths';

// Served at / by the sekai-story-indexer FastAPI app (as a static bundle that calls the same
// /api/* endpoints). `base` makes Vike emit asset URLs under that prefix. Override with
// PUBLIC_BASE if you ever mount it elsewhere.
const base = process.env.PUBLIC_BASE ?? '/';

export default defineConfig({
  base,
  plugins: [tsconfigPaths(), react(), vike()],
  resolve: {
    alias: { '~': new URL('./src/', import.meta.url).pathname }
  }
});
