import vikeReact from 'vike-react/config';
import Layout from '../layouts/Layout';

// Fully static: prerender the single page to dist/client/index.html so the FastAPI app
// can serve it as a plain static bundle (no Node server). ssr keeps first paint styled.
export default {
  prerender: true,
  ssr: true,
  Layout,
  title: 'Project Sekai — Story Indexer',
  extends: [vikeReact]
};
