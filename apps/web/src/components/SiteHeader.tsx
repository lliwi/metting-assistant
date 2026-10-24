"use client";

import {
  AudioLines,
  Languages,
  LogIn,
  LogOut,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { getRecorderQueue } from "@/lib/recorder";
import type { Dictionary } from "@/i18n/client";
import { locales, type Locale } from "@/i18n/config";

// Cada idioma se anuncia en su propia lengua (lo que buscaría quien la habla).
const localeNames: Record<Locale, string> = { es: "Español", ca: "Català" };

function withLocale(pathname: string, locale: Locale): string {
  const [, , ...rest] = pathname.split("/");
  return `/${locale}${rest.length ? `/${rest.join("/")}` : ""}`;
}

/** Icono con nombre accesible: lo leen los lectores de pantalla y aparece como tooltip. */
function IconLink({
  href,
  label,
  icon: Icon,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
}) {
  return (
    <Link href={href} className="icon-link" aria-label={label} title={label}>
      <Icon aria-hidden="true" size={22} strokeWidth={2} />
    </Link>
  );
}

export function SiteHeader({ dict, lang }: { dict: Dictionary; lang: Locale }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();

  async function handleLogout() {
    // Lo no subido se queda en este dispositivo y solo se reanuda con la misma cuenta.
    const pending = await getRecorderQueue().uploader.ownSegments();
    if (pending.length > 0 && !window.confirm(dict.nav.logoutPending)) return;
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
                <IconLink
                  href={`/${lang}/recordings`}
                  label={dict.nav.recordings}
                  icon={AudioLines}
                />
              </li>
              <li>
                <IconLink
                  href={`/${lang}/organizations`}
                  label={dict.nav.organizations}
                  icon={Users}
                />
              </li>
              <li>
                <IconLink
                  href={`/${lang}/account`}
                  label={dict.nav.account}
                  icon={UserRound}
                />
              </li>
              <li>
                <button
                  type="button"
                  className="icon-link"
                  onClick={handleLogout}
                  aria-label={dict.nav.logout}
                  title={dict.nav.logout}
                >
                  <LogOut aria-hidden="true" size={22} strokeWidth={2} />
                </button>
              </li>
            </ul>
          ) : (
            <ul>
              <li>
                <IconLink
                  href={`/${lang}/login`}
                  label={dict.nav.login}
                  icon={LogIn}
                />
              </li>
            </ul>
          ))}
      </nav>

      <nav aria-label={dict.nav.language}>
        {/* La ruta actual solo se conoce en runtime: el prerender enlaza a la portada. */}
        <Suspense
          fallback={<LocaleToggle lang={lang} hrefFor={(l) => `/${l}`} />}
        >
          <CurrentPathLocaleToggle lang={lang} />
        </Suspense>
      </nav>
    </header>
  );
}

function CurrentPathLocaleToggle({ lang }: { lang: Locale }) {
  const pathname = usePathname();
  return <LocaleToggle lang={lang} hrefFor={(l) => withLocale(pathname, l)} />;
}

/** Con dos idiomas, un único icono que cambia al otro. */
function LocaleToggle({
  lang,
  hrefFor,
}: {
  lang: Locale;
  hrefFor: (l: Locale) => string;
}) {
  const other = locales.find((l) => l !== lang) ?? lang;
  return (
    <Link
      href={hrefFor(other)}
      hrefLang={other}
      lang={other}
      className="icon-link"
      aria-label={localeNames[other]}
      title={localeNames[other]}
    >
      <Languages aria-hidden="true" size={22} strokeWidth={2} />
    </Link>
  );
}
