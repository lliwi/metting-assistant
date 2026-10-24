"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { api } from "@/lib/api/client";

type Props = { dict: Dictionary; lang: Locale };

export function ForgotPasswordForm({ dict }: Props) {
  const { pending, error, run } = useAction(dict);
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.POST("/v1/auth/password/forgot", { body: { email } }),
    );
    if (data) setDone(true);
  }

  return (
    <form onSubmit={submit} className="card form">
      <h1>{dict.auth.forgotTitle}</h1>
      <p className="lead">{dict.auth.forgotLead}</p>
      <Field
        label={dict.common.email}
        type="email"
        name="email"
        autoComplete="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        disabled={done}
      />
      <FormMessage error={error} success={done ? dict.auth.forgotDone : null} />
      {!done && (
        <button type="submit" className="button primary" disabled={pending}>
          {dict.auth.forgotSubmit}
        </button>
      )}
    </form>
  );
}

export function ResetPasswordForm({ dict, lang }: Props) {
  const token = useSearchParams().get("token");
  const { pending, error, run } = useAction(dict);
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    const data = await run(() =>
      api.POST("/v1/auth/password/reset", { body: { token, password } }),
    );
    if (data) setDone(true);
  }

  return (
    <form onSubmit={submit} className="card form">
      <h1>{dict.auth.resetTitle}</h1>
      {!token && <p className="alert error">{dict.auth.missingToken}</p>}
      {!done && (
        <Field
          label={dict.account.newPassword}
          hint={dict.common.passwordHint}
          type="password"
          name="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      )}
      <FormMessage error={error} success={done ? dict.auth.resetDone : null} />
      {done ? (
        <Link className="button primary" href={`/${lang}/login`}>
          {dict.nav.login}
        </Link>
      ) : (
        <button
          type="submit"
          className="button primary"
          disabled={pending || !token}
        >
          {dict.auth.resetSubmit}
        </button>
      )}
    </form>
  );
}
