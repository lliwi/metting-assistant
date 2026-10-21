import Link from "next/link";
import { notFound } from "next/navigation";

import { isLocale, locales, type Locale } from "@/i18n/config";
import { getDictionary } from "@/i18n/dictionaries";

const localeNames: Record<Locale, string> = { es: "Español", ca: "Català" };

export default async function HomePage({ params }: PageProps<"/[lang]">) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const dict = await getDictionary(lang);

  return (
    <>
      <header className="site-header">
        <span className="brand">{dict.meta.title}</span>
        <nav aria-label={dict.nav.language}>
          <ul className="locale-switcher">
            {locales.map((locale) => (
              <li key={locale}>
                <Link
                  href={`/${locale}`}
                  hrefLang={locale}
                  lang={locale}
                  aria-current={locale === lang ? "page" : undefined}
                >
                  {localeNames[locale]}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main id="main" className="home">
        <h1>{dict.home.heading}</h1>
        <p className="lead">{dict.home.lead}</p>

        <div className="actions">
          <button type="button" className="button primary" disabled aria-describedby="soon">
            {dict.home.newRecording}
          </button>
          <button type="button" className="button" disabled aria-describedby="soon">
            {dict.home.importAudio}
          </button>
        </div>
        <p id="soon" className="hint">
          {dict.home.comingSoon}
        </p>

        <p className="notice" role="note">
          {dict.home.aiNotice}
        </p>
      </main>
    </>
  );
}
