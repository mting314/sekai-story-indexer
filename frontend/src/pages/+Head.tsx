import faviconUrl from '~/favicon.png';

export default function HeadDefault() {
  return (
    <>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <meta name="description" content="Build and share Project Sekai setlists, organized by unit." />
      <link rel="icon" type="image/png" href={faviconUrl} />
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
      <link
        href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&family=Zen+Maru+Gothic:wght@500;700;900&display=swap"
        rel="stylesheet"
      />
      {/* Apply color mode before paint (no flash). Defaults to system preference. */}
      <script
        dangerouslySetInnerHTML={{
          __html: `try{var m=localStorage.getItem('color-mode');if(!m){m=(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';}document.documentElement.classList.add(m);}catch(e){}`
        }}
      />
    </>
  );
}
