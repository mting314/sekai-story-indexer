// Vike error page — rendered (inside the normal Layout) for an unmatched route (404) and for
// any uncaught SSR/render error (500), instead of Vike's bare built-in error text. Themed with
// the app tokens; offers the appropriate recovery (go home for 404, reload for 500).
import { usePageContext } from 'vike-react/usePageContext';
import { css } from 'styled-system/css';
import { Box, Center, HStack } from 'styled-system/jsx';

const primaryBtn = css({ display: 'inline-flex', alignItems: 'center', cursor: 'pointer', borderWidth: '0', rounded: 'lg', px: '4', py: '2', fontWeight: 'bold', fontSize: 'sm', bg: 'accent.default', color: 'accent.fg', _hover: { bg: 'accent.emphasized' } });
const ghostBtn = css({ display: 'inline-flex', alignItems: 'center', cursor: 'pointer', borderWidth: '1px', borderColor: 'mypick.border', rounded: 'lg', px: '4', py: '2', fontWeight: 'semibold', fontSize: 'sm', color: 'mypick.text', _hover: { borderColor: 'accent.default' } });

export default function Page() {
  const pageContext = usePageContext() as { is404?: boolean; abortReason?: unknown };
  const is404 = pageContext.is404 === true;
  const reason = typeof pageContext.abortReason === 'string' ? pageContext.abortReason : null;

  return (
    <Center minH="60vh" px="4">
      <Box className={css({ maxW: '30rem', w: 'full', bg: 'bg.default', borderWidth: '1px', borderColor: 'border.default', rounded: 'xl', p: '8', boxShadow: 'sm', textAlign: 'center' })}>
        <Box aria-hidden fontSize="5xl" mb="2">{is404 ? '🔍' : '😵‍💫'}</Box>
        <Box as="h1" className={css({ fontSize: '2xl', fontWeight: 'bold', color: 'mypick.text', mb: '1' })}>
          {is404 ? 'Page not found' : 'Something went wrong'}
        </Box>
        <Box className={css({ fontSize: 'sm', color: 'mypick.muted', mb: '5' })}>
          {reason ?? (is404
            ? 'That page doesn’t exist — it may have moved or the link is wrong.'
            : 'The server hit an unexpected error. This is usually temporary — try again in a moment.')}
        </Box>
        <HStack gap="2" justify="center" flexWrap="wrap">
          <a href="/" className={is404 ? primaryBtn : ghostBtn}>← Back to events</a>
          {!is404 && <button type="button" onClick={() => window.location.reload()} className={primaryBtn}>Reload</button>}
        </HStack>
      </Box>
    </Center>
  );
}
