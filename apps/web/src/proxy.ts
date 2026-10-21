import { NextResponse, type NextRequest } from "next/server";

import { isLocale, negotiateLocale } from "@/i18n/config";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const firstSegment = pathname.split("/")[1] ?? "";
  if (isLocale(firstSegment)) return;

  const locale = negotiateLocale(request.headers.get("accept-language"));
  request.nextUrl.pathname = `/${locale}${pathname === "/" ? "" : pathname}`;
  return NextResponse.redirect(request.nextUrl);
}

export const config = {
  // Excluye internos de Next, health check, manifest, service worker y ficheros estáticos.
  matcher: ["/((?!_next|api|healthz|manifest.webmanifest|sw.js|.*\\..*).*)"],
};
