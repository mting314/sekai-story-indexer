import React from 'react';
import '~/index.css';
import { usePageContext } from 'vike-react/usePageContext';
import { FaSun, FaMoon, FaBookOpen, FaMusic } from 'react-icons/fa6';
import { css } from 'styled-system/css';
import { Box, Container, Flex, HStack, Stack } from 'styled-system/jsx';
import { useColorMode } from '~/hooks/useColorMode';

// Top-level nav across the two tools (story indexer + setlist builder), Vike file-based routing.
interface NavItem { href: string; label: string; icon: React.ComponentType<{ size?: number }>; exact?: boolean }
const NAV: NavItem[] = [
  { href: '/', label: 'Story Indexer', icon: FaBookOpen, exact: true },
  { href: '/setlist', label: 'Setlist', icon: FaMusic }
];
const isActive = (href: string, path: string, exact?: boolean) =>
  exact ? path === href : path === href || path.startsWith(`${href}/`);

export default function Layout({ children }: { children: React.ReactNode }) {
  const { mode, toggle } = useColorMode();
  const { urlPathname } = usePageContext();

  return (
    <Box position="relative" minH="100vh" w="full" bg="bg.default" color="fg.default">
      <Container maxW="120rem" px={{ base: '2', md: '4' }} pt="4" pb="10" position="relative" zIndex="2">
        <Stack gap="6">
          <Flex align="center" justify="space-between" w="full" gap="3">
            <HStack gap={{ base: '3', md: '6' }} alignItems="center" minW="0">
              <span className={css({ fontSize: 'md', fontWeight: 'extrabold', color: 'accent.default', whiteSpace: 'nowrap', hideBelow: 'sm' })}>
                🎤 Project Sekai
              </span>
              <HStack gap="1">
                {NAV.map((item) => {
                  const active = isActive(item.href, urlPathname, item.exact);
                  const Icon = item.icon;
                  return (
                    <a
                      key={item.href}
                      href={item.href}
                      aria-current={active ? 'page' : undefined}
                      className={css({
                        display: 'inline-flex', alignItems: 'center', gap: '2', px: '3', py: '1.5', rounded: 'md',
                        fontSize: 'sm', fontWeight: active ? 'bold' : 'medium', whiteSpace: 'nowrap',
                        color: active ? 'accent.text' : 'fg.muted', bg: active ? 'accent.subtle' : 'transparent',
                        _hover: { bg: active ? 'accent.subtle' : 'bg.subtle', color: 'accent.text' }
                      })}
                    >
                      <Icon size={15} /> {item.label}
                    </a>
                  );
                })}
              </HStack>
            </HStack>
            <button
              aria-label="Toggle color mode"
              onClick={toggle}
              className={css({
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                w: '9', h: '9', rounded: 'md', cursor: 'pointer', color: 'fg.muted', flexShrink: '0',
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
