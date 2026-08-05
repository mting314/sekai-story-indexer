// Sekai setlist builder — a controlled component. Two modes (mirrors the ll-predictions
// builder, minus cross-series collab guests, which Sekai setlists don't use):
//   • BAG mode   (ordered=false): unordered Main set / ✦ Encore grids, per-song encore toggle.
//   • ORDER mode (ordered=true):  drag-reorder list with a single ✦ Encore divider; songs
//     at/after the divider are the encore (contiguous trailing block).
// All structure lives in the wire shape (songs[] + encore positions); the pure helpers in
// lib/setlist-items own the mode conversions.
import { useState, type ReactNode } from 'react';
import { css } from 'styled-system/css';
import { Box, Flex, HStack, Stack } from 'styled-system/jsx';
import {
  DndContext, closestCenter, PointerSensor, KeyboardSensor, useSensor, useSensors, type DragEndEvent
} from '@dnd-kit/core';
import {
  SortableContext, useSortable, verticalListSortingStrategy, arrayMove, sortableKeyboardCoordinates
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { FaPlus, FaXmark, FaGripVertical, FaStar } from 'react-icons/fa6';
import {
  predictionToItems, itemsToPrediction, enterOrdered, leaveOrdered, dividerToFlags, type SetlistItem
} from '~/lib/setlist-items';
import { songName } from '~/hooks/useData';
import { SongJacket } from '~/components/SongJacket';
import { SongSearchModal } from '~/components/SongSearchModal';

export interface SetlistBuilderProps {
  songs: string[];
  encore: number[];
  ordered: boolean;
  maxSongs?: number;
  onChange: (songs: string[], encore: number[], ordered: boolean) => void;
}

const DIVIDER = '__divider__';
type Token = { kind: 'song'; id: string } | { kind: 'divider' };
const tokenKey = (t: Token) => (t.kind === 'divider' ? DIVIDER : `song:${t.id}`);

export function SetlistBuilder({ songs, encore, ordered, maxSongs = 50, onChange }: SetlistBuilderProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const items = predictionToItems({ songs, guests: {}, encore, ordered });
  const full = items.length >= maxSongs;

  // Serialize items → wire shape and bubble up.
  const emit = (next: SetlistItem[], nextOrdered: boolean) => {
    const wire = itemsToPrediction(next);
    onChange(wire.songs, wire.encore, nextOrdered);
  };

  const setOrdered = (next: boolean) => {
    emit(next ? enterOrdered(items) : leaveOrdered(items), next);
  };

  const addSong = (id: string) => {
    if (full) return;
    emit([...items, { songId: id, guests: [], encore: false }], ordered);
  };
  const removeAt = (i: number) => emit(items.filter((_, idx) => idx !== i), ordered);
  const toggleEncoreAt = (i: number) =>
    emit(items.map((it, idx) => (idx === i ? { ...it, encore: !it.encore } : it)), ordered);

  return (
    <Stack gap="4">
      <HStack justify="space-between" flexWrap="wrap" gap="3">
        <HStack gap="4">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            disabled={full}
            className={css({
              display: 'inline-flex', alignItems: 'center', gap: '2', px: '3.5', py: '2', rounded: 'lg',
              fontWeight: 'semibold', fontSize: 'sm', cursor: 'pointer', bg: 'accent.default', color: 'accent.fg',
              _hover: { bg: 'accent.emphasized' }, _disabled: { opacity: 0.5, cursor: 'not-allowed' }
            })}
          >
            <FaPlus size={13} /> Add songs
          </button>
          <span className={css({ fontSize: 'sm', color: 'fg.muted' })}>
            {items.length}/{maxSongs} songs
          </span>
        </HStack>
        <label className={css({ display: 'inline-flex', alignItems: 'center', gap: '2', fontSize: 'sm', cursor: 'pointer', color: 'fg.muted' })}>
          <input type="checkbox" checked={ordered} onChange={(e) => setOrdered(e.target.checked)} />
          ↕ Predict exact order (drag to reorder)
        </label>
      </HStack>

      {items.length === 0 ? (
        <EmptyHint onAdd={() => setSearchOpen(true)} />
      ) : ordered ? (
        <OrderMode items={items} onReorder={emit} onRemove={removeAt} />
      ) : (
        <BagMode items={items} onRemove={removeAt} onToggleEncore={toggleEncoreAt} />
      )}

      <SongSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} onPick={addSong} />
    </Stack>
  );
}

function EmptyHint({ onAdd }: { onAdd: () => void }) {
  return (
    <Box
      onClick={onAdd}
      className={css({
        border: '2px dashed', borderColor: 'border.default', rounded: 'xl', p: '10', textAlign: 'center',
        color: 'fg.muted', cursor: 'pointer', _hover: { borderColor: 'accent.default', color: 'accent.text' }
      })}
    >
      No songs yet — click <strong>Add songs</strong> to start building your setlist.
    </Box>
  );
}

/* ---------- BAG mode ---------- */

function BagMode({
  items, onRemove, onToggleEncore
}: {
  items: SetlistItem[];
  onRemove: (i: number) => void;
  onToggleEncore: (i: number) => void;
}) {
  const main = items.map((it, i) => ({ it, i })).filter(({ it }) => !it.encore);
  const enc = items.map((it, i) => ({ it, i })).filter(({ it }) => it.encore);
  const section = (title: string, rows: { it: SetlistItem; i: number }[]) => (
    <Stack gap="2">
      <div className={css({ fontSize: 'xs', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'fg.muted' })}>{title}</div>
      {rows.length === 0 ? (
        <div className={css({ fontSize: 'sm', color: 'fg.subtle', fontStyle: 'italic' })}>—</div>
      ) : (
        <Stack gap="1.5">
          {rows.map(({ it, i }) => (
            <SongRow key={`${it.songId}-${i}`} id={it.songId}
              right={
                <HStack gap="1">
                  <IconBtn label={it.encore ? 'Move to main set' : 'Move to encore'} onClick={() => onToggleEncore(i)} active={it.encore}><FaStar size={12} /></IconBtn>
                  <IconBtn label="Remove" onClick={() => onRemove(i)}><FaXmark size={13} /></IconBtn>
                </HStack>
              }
            />
          ))}
        </Stack>
      )}
    </Stack>
  );
  return (
    <Stack gap="5">
      {section('Main set', main)}
      {section('✦ Encore', enc)}
    </Stack>
  );
}

/* ---------- ORDER mode ---------- */

function OrderMode({
  items, onReorder, onRemove
}: {
  items: SetlistItem[];
  onReorder: (next: SetlistItem[], ordered: boolean) => void;
  onRemove: (i: number) => void;
}) {
  // Build a token list: songs, with a single divider before the first encore song (or at the
  // end when there's no encore, so the user can drag songs below it to create one).
  const firstEnc = items.findIndex((it) => it.encore);
  const tokens: Token[] = [];
  items.forEach((it, i) => {
    if (i === firstEnc) tokens.push({ kind: 'divider' });
    tokens.push({ kind: 'song', id: it.songId });
  });
  if (firstEnc === -1) tokens.push({ kind: 'divider' });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const keys = tokens.map(tokenKey);
    const from = keys.indexOf(String(active.id));
    const to = keys.indexOf(String(over.id));
    if (from < 0 || to < 0) return;
    const moved = arrayMove(tokens, from, to);
    const dividerPos = moved.findIndex((t) => t.kind === 'divider');
    const songsBefore = moved.slice(0, dividerPos).filter((t) => t.kind === 'song').length;
    const orderedSongs = moved.filter((t): t is { kind: 'song'; id: string } => t.kind === 'song');
    const newItems: SetlistItem[] = orderedSongs.map((t) => ({ songId: t.id, guests: [], encore: false }));
    onReorder(dividerToFlags(newItems, songsBefore), true);
  };

  // song index within `items`, for removal (position in the current items array).
  const indexOfSong = (() => {
    let n = 0;
    return () => n++;
  })();

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={tokens.map(tokenKey)} strategy={verticalListSortingStrategy}>
        <Stack gap="1.5">
          {tokens.map((t) =>
            t.kind === 'divider' ? (
              <SortableDivider key={DIVIDER} />
            ) : (
              <SortableSong key={`song:${t.id}`} id={t.id} pos={indexOfSong()} onRemove={onRemove} />
            )
          )}
        </Stack>
      </SortableContext>
    </DndContext>
  );
}

function SortableSong({ id, pos, onRemove }: { id: string; pos: number; onRemove: (i: number) => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: `song:${id}` });
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.6 : 1 }}>
      <SongRow
        id={id}
        left={
          <button {...attributes} {...listeners} aria-label="Drag to reorder"
            className={css({ cursor: 'grab', color: 'fg.subtle', px: '1', _hover: { color: 'fg.muted' } })}>
            <FaGripVertical size={13} />
          </button>
        }
        right={<IconBtn label="Remove" onClick={() => onRemove(pos)}><FaXmark size={13} /></IconBtn>}
      />
    </div>
  );
}

function SortableDivider() {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: DIVIDER });
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}>
      <Flex {...attributes} {...listeners} align="center" gap="2"
        className={css({ cursor: 'grab', py: '1.5', px: '2', rounded: 'md', bg: 'accent.subtle', color: 'accent.text', fontWeight: 'bold', fontSize: 'xs', textTransform: 'uppercase', letterSpacing: '0.06em' })}>
        <FaStar size={11} /> Encore — drag songs below this line
      </Flex>
    </div>
  );
}

/* ---------- shared bits ---------- */

function SongRow({ id, left, right }: { id: string; left?: ReactNode; right?: ReactNode }) {
  return (
    <Flex align="center" gap="2.5"
      className={css({ p: '1.5', pr: '2', rounded: 'lg', bg: 'bg.subtle', borderWidth: '1px', borderColor: 'border.subtle' })}>
      {left}
      <SongJacket id={id} />
      <span className={css({ flex: '1', minW: '0', fontSize: 'sm', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' })}>
        {songName(id)}
      </span>
      {right}
    </Flex>
  );
}

function IconBtn({ children, label, onClick, active }: { children: ReactNode; label: string; onClick: () => void; active?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick}
      className={css({
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', w: '7', h: '7', rounded: 'md',
        cursor: 'pointer', color: active ? 'accent.text' : 'fg.subtle',
        _hover: { bg: 'bg.emphasized', color: 'accent.text' }
      })}>
      {children}
    </button>
  );
}
