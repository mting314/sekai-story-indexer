import React from 'react';
import '~/index.css';
import { FaSun, FaMoon } from 'react-icons/fa6';
import { css } from 'styled-system/css';
import { Box, Container, Flex, HStack, Stack } from 'styled-system/jsx';
import { useColorMode } from '~/hooks/useColorMode';

// Minimal app chrome — no auth, no nav (single page). Just the color-mode toggle,
// a header, and the page body. The heavy Love Live shell (auth/market/toaster) is
// intentionally dropped; this app has one job: build a setlist.
export default function Layout({ children }: { children: React.ReactNode }) {
  const { mode, toggle } = useColorMode();
  return (
    <Box position="relative" minH="100vh" w="full" bg="bg.default" color="fg.default">
      <Container maxW="120rem" px={{ base: '2', md: '4' }} pt="4" pb="10" position="relative" zIndex="2">
        <Stack gap="6">
          <Flex align="center" justify="space-between" w="full" gap="3">
            <HStack gap="2" alignItems="center" minW="0">
              <span className={css({ fontSize: 'lg', fontWeight: 'extrabold', color: 'accent.default', whiteSpace: 'nowrap' })}>
                Project Sekai — Story Indexer
              </span>
            </HStack>
            <button
              aria-label="Toggle color mode"
              onClick={toggle}
              className={css({
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                w: '9', h: '9', rounded: 'md', cursor: 'pointer', color: 'fg.muted',
                _hover: { bg: 'bg.subtle', color: 'accent.text' }
              })}
            >
              {mode === 'dark' ? <FaMoon size={16} /> : <FaSun size={16} />}
            </button>
          </Flex>
          {children}
        </Stack>
      </Container>
    </Box>
  );
}
