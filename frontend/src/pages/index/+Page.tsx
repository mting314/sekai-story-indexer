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

type TabMeta = { id: TabId; label: string; icon: React.ComponentType<{ size?: number }> };
const ALL_TABS: TabMeta[] = [
  { id: 'ask', label: 'Ask', icon: FaComments },
  { id: 'timeline', label: 'Timeline', icon: FaTableList },
  { id: 'summaries', label: 'Summaries', icon: FaBookOpen },
  { id: 'setlist', label: 'Setlist', icon: FaMusic }
];
const CONTENT_TABS = ALL_TABS.filter((t) => t.id !== 'ask'); // right-pane tabs on desktop
const CONTENT_IDS = CONTENT_TABS.map((t) => t.id);

export default function Page() {
  return (
    <AppProvider>
      <SidebarProvider>
        <Shell />
      </SidebarProvider>
    </AppProvider>
  );
}

// Responsive 2-pane shell. Desktop (lg+): Ask chat pinned on the left, Timeline/Summaries/Setlist
// as tabs on the right. Mobile: a single column with the full 4-tab bar (Ask included). All panes
// are keep-alive (mounted once, hidden with display:none) so state — the Ask conversation,
// Timeline/Summaries scroll+expand — survives switching.
function Shell() {
  const { tab, setTab } = useStore();
  // `tab` is the global/mobile active surface. On desktop, Ask is always visible and the right
  // pane shows the active content tab (the last of timeline/summaries/setlist that was selected).
  const [lastContent, setLastContent] = useState<TabId>('timeline');
  useEffect(() => { if (CONTENT_IDS.includes(tab)) setLastContent(tab); }, [tab]);
  const rightTab = CONTENT_IDS.includes(tab) ? tab : lastContent;

  // Keep-alive bookkeeping: Ask is always mounted; content panes mount on first appearance.
  const [visited, setVisited] = useState<Set<TabId>>(() => new Set<TabId>(['ask', rightTab]));
  useEffect(() => { setVisited((v) => (v.has(rightTab) ? v : new Set(v).add(rightTab))); }, [rightTab]);

  const tabBtn = (t: TabMeta, active: boolean) => (
    <button
      key={t.id}
      onClick={() => setTab(t.id)}
      aria-current={active ? 'page' : undefined}
      className={css({
        display: 'inline-flex', alignItems: 'center', gap: '2', px: '3.5', py: '2.5',
        fontSize: 'sm', fontWeight: active ? 'bold' : 'medium', cursor: 'pointer', whiteSpace: 'nowrap',
        color: active ? 'accent.text' : 'fg.muted', borderBottomWidth: '2px',
        borderColor: active ? 'accent.default' : 'transparent', _hover: { color: 'accent.text' }
      })}
    >
      <t.icon size={14} /> {t.label}
    </button>
  );

  return (
    <Stack gap="3">
      {/* Mobile-only tab bar (all four). Hidden on desktop where the 2-pane layout takes over. */}
      <HStack gap="1" className={css({ hideFrom: 'lg', borderBottomWidth: '1px', borderColor: 'border.default', overflowX: 'auto' })}>
        {ALL_TABS.map((t) => tabBtn(t, tab === t.id))}
      </HStack>

      <Box className={css({ display: 'grid', gap: '4', gridTemplateColumns: { base: '1fr', lg: 'minmax(0, 400px) minmax(0, 1fr)' }, alignItems: 'start' })}>
        {/* Left: Ask — always visible on desktop; on mobile only when the Ask tab is active. */}
        <Box className={css({ display: { base: tab === 'ask' ? 'block' : 'none', lg: 'block' }, lg: { position: 'sticky', top: '4' } })}>
          <AskTab />
        </Box>

        {/* Right: content tabs. On mobile hidden while Ask is active. */}
        <Box className={css({ display: { base: tab === 'ask' ? 'none' : 'block', lg: 'block' }, minW: '0' })}>
          <HStack gap="1" className={css({ hideBelow: 'lg', borderBottomWidth: '1px', borderColor: 'border.default', overflowX: 'auto', mb: '3' })}>
            {CONTENT_TABS.map((t) => tabBtn(t, rightTab === t.id))}
          </HStack>
          {CONTENT_TABS.map(({ id }) => {
            if (!visited.has(id)) return null;
            const Pane = PANES[id];
            return <div key={id} style={{ display: rightTab === id ? undefined : 'none' }}><Pane /></div>;
          })}
        </Box>
      </Box>
    </Stack>
  );
}

const PANES: Record<TabId, React.ComponentType> = {
  ask: AskTab, timeline: TimelineTab, summaries: SummariesTab, setlist: SetlistTab
};
