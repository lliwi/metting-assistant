"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { errorMessage, Field, FormMessage, safeNextPath, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api } from "@/lib/api/client";

const providerNames: Record<string, string> = { google: "Google", microsoft: "Microsoft" };

export function LoginForm({ dict, lang }: { dict: Dictionary; lang: Locale }) {
  const router = useRouter();
  const search = useSearchParams();
  const { setUser } = useAuth();
  const { pending, error, run } = useAction(dict);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Tras OIDC con MFA, el token llega en el fragmento (no viaja a servidores ni a logs).
  // Este componente solo se renderiza en cliente (useSearchParams dentro de Suspense).
  const [fragment] = useState(() =>
    typeof window === "undefined"
      ? new URLSearchParams()
      : new URLSearchParams(window.location.hash.slice(1)),
  );
  const [mfaToken, setMfaToken] = useState<string | null>(() => fragment.get("mfa_token"));
  const [code, setCode] = useState("");
  const [notVerified, setNotVerified] = useState(false);
  const [resent, setResent] = useState(false);
  const [providers, setProviders] = useState<string[]>([]);
  const [next] = useState(() =>
    safeNextPath(fragment.get("next") ?? search.get("next"), `/${lang}`),
  );
  const oidcError = search.get("error");
  const codeInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void api.GET("/v1/auth/oidc/providers").then(({ data }) => {
      setProviders((data ?? []).map((p) => p.name));
    });
  }, []);

  useEffect(() => {
    // Quita el token del historial del navegador una vez leído.
    if (fragment.has("mfa_token")) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, [fragment]);

  useEffect(() => {
    if (mfaToken) codeInput.current?.focus();
  }, [mfaToken]);

  async function submitCredentials(e: React.FormEvent) {
    e.preventDefault();
    setNotVerified(false);
    const data = await run(async () => {
      const res = await api.POST("/v1/auth/login", { body: { email, password } });
      if (res.response.status === 403) setNotVerified(true);
      return res;
    });
    if (!data) return;
    setPassword("");
    if (data.status === "mfa_required" && data.mfa_token) {
      setMfaToken(data.mfa_token);
    } else if (data.user) {
      setUser(data.user);
      router.replace(next);
    }
  }

  async function submitMfa(e: React.FormEvent) {
    e.preventDefault();
    if (!mfaToken) return;
    const data = await run(() =>
      api.POST("/v1/auth/login/mfa", { body: { mfa_token: mfaToken, code: code.trim() } }),
    );
    if (data?.user) {
      setCode("");
      setMfaToken(null);
      setUser(data.user);
      router.replace(next);
    }
  }

  async function resend() {
    const data = await run(() =>
      api.POST("/v1/auth/resend-verification", { body: { email } }),
    );
    if (data) setResent(true);
  }

  if (mfaToken) {
    return (
      <form onSubmit={submitMfa} className="card form">
        <h1>{dict.auth.mfaTitle}</h1>
        <p className="lead">{dict.auth.mfaLead}</p>
        <Field
          ref={codeInput}
          label={dict.auth.mfaCode}
          name="code"
          autoComplete="one-time-code"
          inputMode="text"
          required
          minLength={6}
          maxLength={16}
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <FormMessage error={error} />
        <button type="submit" className="button primary" disabled={pending}>
          {dict.auth.mfaSubmit}
        </button>
      </form>
    );
  }

  return (
    <div className="card form">
      <h1>{dict.auth.loginTitle}</h1>
      <form onSubmit={submitCredentials} className="stack">
        <Field
          label={dict.common.email}
          type="email"
          name="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Field
          label={dict.common.password}
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <FormMessage
          error={error ?? (oidcError ? errorMessage(dict, oidcError) : null)}
          success={resent ? dict.auth.resendDone : null}
        />
        {notVerified && !resent && (
          <button type="button" className="link-button" onClick={resend} disabled={pending}>
            {dict.auth.resendVerification}
          </button>
        )}
        <button type="submit" className="button primary" disabled={pending}>
          {dict.auth.loginSubmit}
        </button>
      </form>

      {providers.length > 0 && (
        <div className="stack providers">
          <p className="divider">{dict.common.or}</p>
          {providers.map((name) => (
            <a
              key={name}
              className="button"
              href={`/api/v1/auth/oidc/${name}/start?next=${encodeURIComponent(next)}`}
            >
              {format(dict.auth.continueWith, { provider: providerNames[name] ?? name })}
            </a>
          ))}
        </div>
      )}

      <p className="hint">
        <Link href={`/${lang}/forgot-password`}>{dict.auth.forgotLink}</Link>
      </p>
      <p className="hint">
        {dict.auth.noAccount} <Link href={`/${lang}/register`}>{dict.nav.register}</Link>
      </p>
    </div>
  );
}
