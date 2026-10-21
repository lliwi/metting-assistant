"use client";

import Link from "next/link";
import { useState } from "react";

import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { api } from "@/lib/api/client";

export function RegisterForm({ dict, lang }: { dict: Dictionary; lang: Locale }) {
  const { pending, error, run } = useAction(dict);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.POST("/v1/auth/register", { body: { name, email, password, locale: lang } }),
    );
    if (data) setDone(true);
  }

  if (done) {
    return (
      <div className="card form">
        <h1>{dict.auth.registerTitle}</h1>
        <p className="alert success" role="status">
          {dict.auth.registerDone}
        </p>
        <Link href={`/${lang}/login`}>{dict.nav.login}</Link>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="card form">
      <h1>{dict.auth.registerTitle}</h1>
      <Field
        label={dict.common.name}
        name="name"
        autoComplete="name"
        required
        maxLength={200}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
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
      <FormMessage error={error} />
      <button type="submit" className="button primary" disabled={pending}>
        {dict.auth.registerSubmit}
      </button>
      <p className="hint">
        {dict.auth.haveAccount} <Link href={`/${lang}/login`}>{dict.nav.login}</Link>
      </p>
    </form>
  );
}
