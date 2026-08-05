import { useEffect, useState } from 'react';
import { css } from 'styled-system/css';
import { Box, Flex, HStack, Stack } from 'styled-system/jsx';
import { FaLink, FaFloppyDisk, FaFolderOpen, FaTrash, FaFileCirclePlus } from 'react-icons/fa6';
import { SetlistBuilder } from '~/components/SetlistBuilder';
import { EMPTY_STATE, decodeHash, shareUrl, type SetlistState } from '~/lib/share';
import { listSlots, saveSlot, loadSlot, deleteSlot, type SavedSlot } from '~/lib/storage';

const cardCls = css({ bg: 'bg.default', borderWidth: '1px', borderColor: 'border.default', rounded: 'xl', p: { base: '3', md: '5' }, boxShadow: 'sm' });
const btnCls = css({ display: 'inline-flex', alignItems: 'center', gap: '1.5', px: '3', py: '1.5', rounded: 'lg', fontSize: 'sm', fontWeight: 'medium', cursor: 'pointer', borderWidth: '1px', borderColor: 'border.default', bg: 'bg.subtle', _hover: { bg: 'bg.emphasized' } });

// The Setlist surface — a native tab in the unified app (formerly the standalone setlist page /
// iframe). Title input, localStorage save-slots, and an lz-string share URL.
export function SetlistTab() {
  const [state, setState] = useState<SetlistState>(EMPTY_STATE);
  const [slots, setSlots] = useState<SavedSlot[]>([]);
  const [slotName, setSlotName] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const fromHash = decodeHash(window.location.hash);
    if (fromHash) {
      setState(fromHash);
      setSlotName(fromHash.title);
    }
    setSlots(listSlots());
  }, []);

  const update = (partial: Partial<SetlistState>) => setState((s) => ({ ...s, ...partial }));
  const onBuilderChange = (songs: string[], encore: number[], ordered: boolean) => update({ songs, encore, ordered });
  const refreshSlots = () => setSlots(listSlots());

  const doSave = () => {
    const name = (slotName || state.title || 'Untitled').trim();
    saveSlot(name, { ...state, title: state.title || name }, Date.now());
    refreshSlots();
  };
  const doLoad = (name: string) => {
    const loaded = loadSlot(name);
    if (loaded) { setState(loaded); setSlotName(loaded.title || name); }
  };
  const doDelete = (name: string) => { deleteSlot(name); refreshSlots(); };
  const doNew = () => {
    setState(EMPTY_STATE);
    setSlotName('');
    history.replaceState(null, '', window.location.pathname + window.location.search);
  };
  const doShare = async () => {
    const u = shareUrl(state);
    history.replaceState(null, '', '#' + u.split('#')[1]);
    try {
      await navigator.clipboard.writeText(u);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked — hash is in the URL bar regardless */ }
  };

  return (
    <Stack gap="4">
      <Box className={cardCls}>
        <Stack gap="3">
          <input
            placeholder="Setlist title (e.g. My Dream Sekai Live 2026)"
            value={state.title}
            onChange={(e) => update({ title: e.target.value })}
            className={css({ w: 'full', px: '3', py: '2.5', rounded: 'lg', bg: 'bg.subtle', borderWidth: '1px', borderColor: 'border.default', fontSize: 'lg', fontWeight: 'bold', _focus: { borderColor: 'accent.default', outline: 'none' } })}
          />
          <Flex gap="2" flexWrap="wrap" align="center">
            <button className={btnCls} onClick={doNew}><FaFileCirclePlus size={13} /> New</button>
            <HStack gap="1">
              <input
                placeholder="Slot name"
                value={slotName}
                onChange={(e) => setSlotName(e.target.value)}
                className={css({ px: '2.5', py: '1.5', rounded: 'lg', bg: 'bg.subtle', borderWidth: '1px', borderColor: 'border.default', fontSize: 'sm', w: '44', _focus: { borderColor: 'accent.default', outline: 'none' } })}
              />
              <button className={btnCls} onClick={doSave}><FaFloppyDisk size={13} /> Save</button>
            </HStack>
            <button className={btnCls} onClick={doShare}>
              <FaLink size={13} /> {copied ? 'Link copied!' : 'Share link'}
            </button>
          </Flex>

          {slots.length > 0 && (
            <HStack gap="1.5" flexWrap="wrap">
              <span className={css({ fontSize: 'xs', color: 'fg.subtle', display: 'inline-flex', alignItems: 'center', gap: '1' })}><FaFolderOpen size={12} /> Saved:</span>
              {slots.map((s) => (
                <HStack key={s.name} gap="0" className={css({ rounded: 'full', borderWidth: '1px', borderColor: 'border.default', overflow: 'hidden' })}>
                  <button onClick={() => doLoad(s.name)} className={css({ px: '2.5', py: '1', fontSize: 'xs', cursor: 'pointer', _hover: { bg: 'bg.subtle' } })}>{s.name}</button>
                  <button aria-label={`Delete ${s.name}`} onClick={() => doDelete(s.name)} className={css({ px: '1.5', py: '1', color: 'fg.subtle', cursor: 'pointer', _hover: { bg: 'bg.subtle', color: 'red.500' } })}><FaTrash size={10} /></button>
                </HStack>
              ))}
            </HStack>
          )}
        </Stack>
      </Box>

      <Box className={cardCls}>
        <SetlistBuilder
          songs={state.songs}
          encore={state.encore}
          ordered={state.ordered}
          onChange={onBuilderChange}
        />
      </Box>
    </Stack>
  );
}
