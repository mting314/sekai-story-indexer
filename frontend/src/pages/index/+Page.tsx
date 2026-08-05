import { useEffect, useState } from 'react';
import { css } from 'styled-system/css';
import { Box, HStack, Stack } from 'styled-system/jsx';
import { FaComments, FaTableList, FaBookOpen, FaMusic } from 'react-icons/fa6';
import { AppProvider, useStore, type TabId } from '~/lib/store';
import { SidebarProvider } from '~/components/Sidebar';
import { AskTab } from '~/components/AskTab';
import { TimelineTab } from '~/components/TimelineTab';
import { SummariesTab } from '~/components/SummariesTab';
import { SetlistTab } from '~/components/setlist/SetlistTab';

const TABS: { id: TabId; label: string; icon: React.ComponentType<{ size?: number }> }[] = [
  { id: 'ask', label: 'Ask', icon: FaComments },
  { id: 'timeline', label: 'Timeline', icon: FaTableList },
  { id: 'summaries', label: 'Summaries', icon: FaBookOpen },
  { id: 'setlist', label: 'Setlist', icon: FaMusic }
];

// The unified app shell. Ask/Timeline/Summaries/Setlist tabs share the AppProvider store
// (catalog + scope + tab). Deep-linked setlists (…#s=…) open on the Setlist tab (handled in
// the provider).
export default function Page() {
  return (
    <AppProvider>
      <SidebarProvider>
        <Shell />
      </SidebarProvider>
    </AppProvider>
  );
}

const PANES: Record<TabId, React.ComponentType> = {
  ask: AskTab, timeline: TimelineTab, summaries: SummariesTab, setlist: SetlistTab
};

function Shell() {
  const { tab, setTab } = useStore();
  // Keep-alive: mount a tab on first visit, then keep it mounted (hidden when inactive) so its
  // state survives tab switches — notably the Ask conversation, and Timeline/Summaries scroll
  // + expand state. Avoids re-fetching and the "chat lost on tab switch" regression.
  const [visited, setVisited] = useState<Set<TabId>>(() => new Set<TabId>([tab]));
  useEffect(() => {
    setVisited((v) => (v.has(tab) ? v : new Set(v).add(tab)));
  }, [tab]);

  return (
    <Stack gap="4">
      <HStack gap="1" className={css({ borderBottomWidth: '1px', borderColor: 'border.default', overflowX: 'auto' })}>
        {TABS.map(({ id, label, icon: Icon }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              aria-current={active ? 'page' : undefined}
              className={css({
                display: 'inline-flex', alignItems: 'center', gap: '2', px: '3.5', py: '2.5',
                fontSize: 'sm', fontWeight: active ? 'bold' : 'medium', cursor: 'pointer',
                whiteSpace: 'nowrap', color: active ? 'accent.text' : 'fg.muted',
                borderBottomWidth: '2px', borderColor: active ? 'accent.default' : 'transparent',
                _hover: { color: 'accent.text' }
              })}
            >
              <Icon size={14} /> {label}
            </button>
          );
        })}
      </HStack>

      <Box>
        {TABS.map(({ id }) => {
          if (!visited.has(id)) return null;
          const Pane = PANES[id];
          return <div key={id} style={{ display: tab === id ? undefined : 'none' }}><Pane /></div>;
        })}
      </Box>
    </Stack>
  );
}
