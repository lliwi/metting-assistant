"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { locales, type Locale } from "@/i18n/config";
import type { Dictionary } from "@/i18n/client";

const localeNames: Record<Locale, string> = { es: "Español", ca: "Català" };

function withLocale(pathname: string, locale: Locale): string {
  const [, , ...rest] = pathname.split("/");
  return `/${locale}${rest.length ? `/${rest.join("/")}` : ""}`;
}

export function SiteHeader({ dict, lang }: { dict: Dictionary; lang: Locale }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();

  async function handleLogout() {
    await logout();
    router.push(`/${lang}/login`);
  }

  return (
    <header className="site-header">
      <Link href={`/${lang}`} className="brand">
        {dict.meta.title}
      </Link>

      <nav aria-label={dict.nav.main} className="main-nav">
        {!loading &&
          (user ? (
            <ul>
              <li>
                <Link href={`/${lang}/organizations`}>{dict.nav.organizations}</Link>
              </li>
              <li>
                <Link href={`/${lang}/account`}>{dict.nav.account}</Link>
              </li>
              <li>
                <button type="button" className="link-button" onClick={handleLogout}>
                  {dict.nav.logout}
                </button>
              </li>
            </ul>
          ) : (
            <ul>
              <li>
                <Link href={`/${lang}/login`}>{dict.nav.login}</Link>
              </li>
              <li>
                <Link href={`/${lang}/register`}>{dict.nav.register}</Link>
              </li>
            </ul>
          ))}
      </nav>

      <nav aria-label={dict.nav.language}>
        {/* La ruta actual solo se conoce en runtime: el prerender usa enlaces a la portada. */}
        <Suspense fallback={<LocaleLinks lang={lang} hrefFor={(l) => `/${l}`} />}>
          <CurrentPathLocaleLinks lang={lang} />
        </Suspense>
      </nav>
    </header>
  );
}

function CurrentPathLocaleLinks({ lang }: { lang: Locale }) {
  const pathname = usePathname();
  return <LocaleLinks lang={lang} hrefFor={(l) => withLocale(pathname, l)} />;
}

function LocaleLinks({ lang, hrefFor }: { lang: Locale; hrefFor: (l: Locale) => string }) {
  return (
    <ul className="locale-switcher">
      {locales.map((locale) => (
        <li key={locale}>
          <Link
            href={hrefFor(locale)}
            hrefLang={locale}
            lang={locale}
            aria-current={locale === lang ? "page" : undefined}
          >
            {localeNames[locale]}
          </Link>
        </li>
      ))}
    </ul>
  );
}
