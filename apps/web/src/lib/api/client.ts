import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type User = Schemas["UserOut"];

const CSRF_COOKIE = "ma_csrf";
const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

/** Siempre se lee de la cookie: el servidor rota el token al iniciar sesión. */
async function csrfToken(): Promise<string> {
  let token = readCookie(CSRF_COOKIE);
  if (!token) {
    await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
    token = readCookie(CSRF_COOKIE);
  }
  return token ?? "";
}

const csrf: Middleware = {
  async onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method)) {
      request.headers.set(CSRF_HEADER, await csrfToken());
    }
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: "/api", credentials: "same-origin" });
api.use(csrf);

/** Código estable del formato de error común: {"error": {"code": …}}. */
export function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "error" in error) {
    const inner = (error as { error?: { code?: unknown } }).error;
    if (inner && typeof inner.code === "string") return inner.code;
  }
  return "unknown";
}
