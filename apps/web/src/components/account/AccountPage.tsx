"use client";

import QRCode from "qrcode";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";

type Props = { dict: Dictionary; lang: Locale };

export function AccountPage({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {(user) => (
        <div className="stack-lg">
          <h1>{dict.account.title}</h1>
          <ProfileSection dict={dict} user={user} />
          <section className="card" aria-labelledby="security-heading">
            <h2 id="security-heading">{dict.account.security}</h2>
            <PasswordSection dict={dict} user={user} />
            <MfaSection dict={dict} user={user} />
          </section>
          <SessionsSection dict={dict} lang={lang} />
        </div>
      )}
    </RequireAuth>
  );
}

function ProfileSection({ dict, user }: { dict: Dictionary; user: User }) {
  const { setUser } = useAuth();
  const { pending, error, run } = useAction(dict);
  const [name, setName] = useState(user.name);
  const [locale, setLocale] = useState(user.locale);
  const [saved, setSaved] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaved(false);
    const data = await run(() =>
      api.PATCH("/v1/auth/me", { body: { name, locale: locale as Locale } }),
    );
    if (data) {
      setUser(data);
      setSaved(true);
    }
  }

  return (
    <section className="card" aria-labelledby="profile-heading">
      <h2 id="profile-heading">{dict.account.profile}</h2>
      <form onSubmit={submit} className="stack">
        <p>
          {dict.common.email}: <strong>{user.email}</strong>
        </p>
        <Field
          label={dict.common.name}
          required
          maxLength={200}
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <div className="field">
          <label htmlFor="locale">{dict.account.language}</label>
          <select id="locale" value={locale} onChange={(e) => setLocale(e.target.value)}>
            <option value="es">Español</option>
            <option value="ca">Català</option>
          </select>
        </div>
        <FormMessage error={error} success={saved ? dict.common.saved : null} />
        <button type="submit" className="button primary" disabled={pending}>
          {dict.common.save}
        </button>
      </form>
    </section>
  );
}

function PasswordSection({ dict, user }: { dict: Dictionary; user: User }) {
  const { pending, error, run } = useAction(dict);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);

  if (!user.has_password) {
    return (
      <div className="stack">
        <h3>{dict.account.changePassword}</h3>
        <p className="hint">{dict.account.noPassword}</p>
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setDone(false);
    const data = await run(() =>
      api.POST("/v1/auth/password/change", {
        body: { current_password: current, new_password: next },
      }),
    );
    if (data) {
      setDone(true);
      setCurrent("");
      setNext("");
    }
  }

  return (
    <form onSubmit={submit} className="stack">
      <h3>{dict.account.changePassword}</h3>
      <Field
        label={dict.account.currentPassword}
        type="password"
        autoComplete="current-password"
        required
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
      />
      <Field
        label={dict.account.newPassword}
        hint={dict.common.passwordHint}
        type="password"
        autoComplete="new-password"
        required
        minLength={12}
        maxLength={128}
        value={next}
        onChange={(e) => setNext(e.target.value)}
      />
      <FormMessage error={error} success={done ? dict.account.passwordChanged : null} />
      <button type="submit" className="button" disabled={pending}>
        {dict.account.changePassword}
      </button>
    </form>
  );
}

function MfaSection({ dict, user }: { dict: Dictionary; user: User }) {
  const { refresh } = useAuth();
  const { pending, error, run } = useAction(dict);
  const [setup, setSetup] = useState<Schemas["TotpSetupResponse"] | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);

  useEffect(() => {
    if (!setup) return;
    void QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 192 }).then(setQr);
  }, [setup]);

  async function start() {
    const data = await run(() => api.POST("/v1/auth/mfa/totp/setup"));
    if (data) setSetup(data);
  }

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() => api.POST("/v1/auth/mfa/totp/confirm", { body: { code } }));
    if (data) {
      setRecovery(data.recovery_codes);
      setSetup(null);
      setCode("");
    }
  }

  async function disable(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.POST("/v1/auth/mfa/totp/disable", { body: { password, code: code.trim() } }),
    );
    if (data) {
      setCode("");
      setPassword("");
      await refresh();
    }
  }

  return (
    <div className="stack">
      <h3>
        {dict.account.mfa}:{" "}
        <span className={user.mfa_enabled ? "badge ok" : "badge"}>
          {user.mfa_enabled ? dict.account.mfaEnabled : dict.account.mfaDisabled}
        </span>
      </h3>

      {recovery && (
        <div className="stack" role="region" aria-labelledby="recovery-heading">
          <h4 id="recovery-heading">{dict.account.mfaRecoveryTitle}</h4>
          <p>{dict.account.mfaRecoveryLead}</p>
          <ul className="codes">
            {recovery.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="button primary"
            onClick={() => {
              setRecovery(null);
              void refresh();
            }}
          >
            {dict.account.mfaRecoveryDone}
          </button>
        </div>
      )}

      {!user.mfa_enabled && !setup && !recovery && (
        <button type="button" className="button" onClick={start} disabled={pending}>
          {dict.account.mfaEnable}
        </button>
      )}

      {setup && (
        <form onSubmit={confirm} className="stack">
          <p>{dict.account.mfaScan}</p>
          {qr && (
            // eslint-disable-next-line @next/next/no-img-element -- data URL generada en cliente
            <img src={qr} width={192} height={192} alt={dict.account.mfaQrAlt} className="qr" />
          )}
          <p>
            {dict.account.mfaSecret}: <code className="secret">{setup.secret}</code>
          </p>
          <Field
            label={dict.auth.mfaCode}
            autoComplete="one-time-code"
            inputMode="numeric"
            pattern="\d{6}"
            required
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <button type="submit" className="button primary" disabled={pending}>
            {dict.account.mfaConfirm}
          </button>
        </form>
      )}

      {user.mfa_enabled && !recovery && (
        <form onSubmit={disable} className="stack">
          <p className="hint">{dict.account.mfaDisableLead}</p>
          {user.has_password && (
            <Field
              label={dict.common.password}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
          <Field
            label={dict.auth.mfaCode}
            autoComplete="one-time-code"
            required
            minLength={6}
            maxLength={16}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <button type="submit" className="button danger" disabled={pending}>
            {dict.account.mfaDisable}
          </button>
        </form>
      )}

      <FormMessage error={error} />
    </div>
  );
}

function SessionsSection({ dict, lang }: Props) {
  const { run, error } = useAction(dict);
  const dateFormat = new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" });
  const fetchSessions = useCallback(
    async () => (await api.GET("/v1/auth/sessions")).data ?? [],
    [],
  );
  const { data: sessions = [], reload: load } = useQuery(fetchSessions);

  async function revoke(id: string) {
    const res = await run(() =>
      api.DELETE("/v1/auth/sessions/{session_id}", { params: { path: { session_id: id } } }),
    );
    if (res !== undefined) await load();
  }

  return (
    <section className="card" aria-labelledby="sessions-heading">
      <h2 id="sessions-heading">{dict.account.sessions}</h2>
      <ul className="list">
        {sessions.map((s) => (
          <li key={s.id} className="list-row">
            <div>
              <div>{s.user_agent ?? dict.account.unknownDevice}</div>
              <div className="hint">
                {format(dict.account.sessionLastSeen, {
                  date: dateFormat.format(new Date(s.last_seen_at)),
                })}
              </div>
            </div>
            {s.current ? (
              <span className="badge ok">{dict.account.sessionCurrent}</span>
            ) : (
              <button type="button" className="button small" onClick={() => revoke(s.id)}>
                {dict.account.sessionRevoke}
              </button>
            )}
          </li>
        ))}
      </ul>
      <FormMessage error={error} />
    </section>
  );
}
