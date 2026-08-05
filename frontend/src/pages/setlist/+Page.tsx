// The Setlist builder as its own top-level page (route /setlist) — it's a distinct tool from the
// story indexer, so it lives on its own page rather than a tab. Needs none of the indexer's
// providers (catalog/scope/sidebar); it reads the bundled song catalog directly.
import { SetlistTab } from '~/components/setlist/SetlistTab';

export default function Page() {
  return <SetlistTab />;
}
