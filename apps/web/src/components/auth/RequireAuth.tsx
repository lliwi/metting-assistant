"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import type { Locale } from "@/i18n/client";
import type { User } from "@/lib/api/client";

import { useAuth } from "./AuthProvider";

/** Redirige al login (volviendo luego aquí) si no hay sesión. */
export function RequireAuth({
  lang,
  loadingLabel,
  children,
}: {
  lang: Locale;
  loadingLabel: string;
  children: (user: User) => React.ReactNode;
}) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user) {
      const next = pathname + window.location.search;
      router.replace(`/${lang}/login?next=${encodeURIComponent(next)}`);
    }
  }, [loading, user, lang, pathname, router]);

  if (!user) {
    return (
      <p role="status" className="hint">
        {loadingLabel}
      </p>
    );
  }
  return <>{children(user)}</>;
}
