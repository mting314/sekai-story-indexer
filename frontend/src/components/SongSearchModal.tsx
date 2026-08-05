// Song picker: free-text search + unit chips (the six units plus "Other" for songs with no
// owning unit). A plain overlay dialog (no Ark dependency) to keep the surface small.
import { useMemo, useState } from 'react';
import { css } from 'styled-system/css';
import { Box, Flex, HStack, Stack } from 'styled-system/jsx';
import { FaXmark, FaPlus } from 'react-icons/fa6';
import { useSongs, useUnits, songName, songSubName, isCommissioned } from '~/hooks/useData';
import { unitIcon } from '~/lib/assets';
import { filterSongs, EMPTY_SONG_FILTERS, type SongFilters, type SongKind, type UnitFilter } from '~/lib/song-filter';
import { SongJacket } from '~/components/SongJacket';

const MAX_RESULTS = 200;
const KINDS: { id: SongKind; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'commissioned', label: 'Commissioned' },
  { id: 'cover', label: 'Covers' }
];

export function SongSearchModal({
  open, onClose, onPick
}: {
  open: boolean;
  onClose: () => void;
  onPick: (id: string) => void;
}) {
  const [filters, setFilters] = useState<SongFilters>(EMPTY_SONG_FILTERS);
  const songs = useSongs();
  const units = useUnits();

  const results = useMemo(() => (open ? filterSongs(songs, filters) : []), [open, songs, filters]);

  if (!open) return null;

  const toggleUnit = (u: UnitFilter) =>
    setFilters((f) => ({
      ...f,
      units: f.units.includes(u) ? f.units.filter((x) => x !== u) : [...f.units, u]
    }));

  return (
    <Box
      onClick={onClose}
      className={css({ position: 'fixed', inset: '0', zIndex: '50', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', p: { base: '2', md: '8' }, bg: 'rgba(0,0,0,0.55)' })}
    >
      <Box
        onClick={(e) => e.stopPropagation()}
        className={css({ w: 'full', maxW: '3xl', maxH: '86vh', display: 'flex', flexDirection: 'column', bg: 'bg.default', rounded: 'xl', borderWidth: '1px', borderColor: 'border.default', boxShadow: 'lg', overflow: 'hidden' })}
      >
        <Flex align="center" justify="space-between" className={css({ p: '3', borderBottomWidth: '1px', borderColor: 'border.subtle' })}>
          <strong className={css({ fontSize: 'sm' })}>Add songs</strong>
          <button aria-label="Close" onClick={onClose} className={css({ cursor: 'pointer', color: 'fg.muted', _hover: { color: 'fg.default' } })}>
            <FaXmark size={16} />
          </button>
        </Flex>

        <Stack gap="3" className={css({ p: '3', borderBottomWidth: '1px', borderColor: 'border.subtle' })}>
          <input
            autoFocus
            placeholder="Search by title or reading…"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
            className={css({ w: 'full', px: '3', py: '2', rounded: 'lg', bg: 'bg.subtle', borderWidth: '1px', borderColor: 'border.default', fontSize: 'sm', _focus: { borderColor: 'accent.default', outline: 'none' } })}
          />
          <HStack gap="1.5" flexWrap="wrap">
            {units.map((u) => {
              const active = filters.units.includes(u.id as UnitFilter);
              return (
                <button
                  key={u.id}
                  onClick={() => toggleUnit(u.id as UnitFilter)}
                  className={css({ display: 'inline-flex', alignItems: 'center', gap: '1.5', px: '2.5', py: '1', rounded: 'full', fontSize: 'xs', fontWeight: 'semibold', cursor: 'pointer', borderWidth: '1px' })}
                  style={{
                    borderColor: u.color,
                    background: active ? u.color : 'transparent',
                    color: active ? '#111' : undefined
                  }}
                >
                  {u.id !== 'other' && <img src={unitIcon(u.id)} alt="" width={16} height={16} style={{ objectFit: 'contain' }} />}
                  {u.name}
                </button>
              );
            })}
          </HStack>
          <HStack gap="1">
            {KINDS.map((k) => (
              <button
                key={k.id}
                onClick={() => setFilters((f) => ({ ...f, kind: k.id }))}
                className={css({ px: '2.5', py: '1', rounded: 'md', fontSize: 'xs', fontWeight: 'semibold', cursor: 'pointer', bg: filters.kind === k.id ? 'accent.subtle' : 'bg.subtle', color: filters.kind === k.id ? 'accent.text' : 'fg.muted', borderWidth: '1px', borderColor: filters.kind === k.id ? 'accent.default' : 'border.default' })}
              >
                {k.label}
              </button>
            ))}
          </HStack>
        </Stack>

        <Box className={css({ flex: '1', overflowY: 'auto', p: '2' })}>
          {results.length === 0 ? (
            <div className={css({ p: '6', textAlign: 'center', color: 'fg.muted', fontSize: 'sm' })}>
              No songs match. (Run <code>bun run fetch-songs</code> if the catalog looks empty.)
            </div>
          ) : (
            <Stack gap="1">
              {results.slice(0, MAX_RESULTS).map((s) => (
                <Flex key={s.id} align="center" gap="2.5" className={css({ p: '1.5', rounded: 'lg', _hover: { bg: 'bg.subtle' } })}>
                  <SongJacket id={s.id} size={40} />
                  <Stack gap="0" className={css({ flex: '1', minW: '0' })}>
                    <HStack gap="1.5" className={css({ minW: '0' })}>
                      <span className={css({ fontSize: 'sm', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' })}>{songName(s.id)}</span>
                      <KindBadge commissioned={isCommissioned(s.id)} />
                    </HStack>
                    {(songSubName(s.id) ?? s.pronunciation) && (
                      <span className={css({ fontSize: 'xs', color: 'fg.subtle', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' })}>{songSubName(s.id) ?? s.pronunciation}</span>
                    )}
                  </Stack>
                  <button
                    aria-label={`Add ${songName(s.id)}`}
                    onClick={() => onPick(s.id)}
                    className={css({ display: 'inline-flex', alignItems: 'center', gap: '1', px: '2.5', py: '1', rounded: 'md', fontSize: 'xs', fontWeight: 'semibold', cursor: 'pointer', bg: 'accent.default', color: 'accent.fg', _hover: { bg: 'accent.emphasized' } })}
                  >
                    <FaPlus size={11} /> Add
                  </button>
                </Flex>
              ))}
              {results.length > MAX_RESULTS && (
                <div className={css({ p: '3', textAlign: 'center', color: 'fg.subtle', fontSize: 'xs' })}>
                  Showing first {MAX_RESULTS} of {results.length} — refine your search.
                </div>
              )}
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  );
}

// Small pill distinguishing commissioned (written for Project Sekai) from cover songs.
export function KindBadge({ commissioned }: { commissioned: boolean }) {
  return (
    <span
      title={commissioned ? 'Commissioned — written for Project Sekai' : 'Cover of an existing song'}
      className={css({ flexShrink: '0', px: '1.5', py: '0.5', rounded: 'sm', fontSize: '2xs', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.03em' })}
      style={commissioned ? { background: 'var(--colors-accent-subtle)', color: 'var(--colors-accent-text)' } : { background: 'rgba(255,255,255,0.08)', color: 'var(--colors-fg-muted)' }}
    >
      {commissioned ? 'Commissioned' : 'Cover'}
    </span>
  );
}
