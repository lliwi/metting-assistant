"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { api, type Schemas } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";

type Props = { dict: Dictionary; lang: Locale };

export function OrganizationsPage({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {() => <OrganizationList dict={dict} lang={lang} />}
    </RequireAuth>
  );
}

function OrganizationList({ dict, lang }: Props) {
  const { refresh } = useAuth();
  const { pending, error, run } = useAction(dict);
  const [name, setName] = useState("");
  const fetchOrgs = useCallback(
    async () => (await api.GET("/v1/organizations")).data ?? [],
    [],
  );
  const { data: orgs = null, reload: load } = useQuery<Schemas["OrganizationOut"][] | null>(
    fetchOrgs,
  );

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() => api.POST("/v1/organizations", { body: { name } }));
    if (data) {
      setName("");
      await Promise.all([load(), refresh()]);
    }
  }

  const shared = orgs?.filter((o) => !o.is_personal) ?? [];

  return (
    <div className="stack-lg">
      <h1>{dict.orgs.title}</h1>

      <section className="card">
        {orgs === null ? (
          <p role="status">{dict.common.loading}</p>
        ) : (
          <ul className="list">
            {orgs.map((org) => (
              <li key={org.id} className="list-row">
                <div>
                  <Link href={`/${lang}/organizations/${org.id}`}>
                    {org.is_personal ? dict.orgs.personal : org.name}
                  </Link>
                </div>
                <span className="badge">{dict.roles[org.role]}</span>
              </li>
            ))}
          </ul>
        )}
        {orgs !== null && shared.length === 0 && <p className="hint">{dict.orgs.empty}</p>}
      </section>

      <form onSubmit={create} className="card stack">
        <h2>{dict.orgs.create}</h2>
        <Field
          label={dict.orgs.createName}
          required
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <FormMessage error={error} />
        <button type="submit" className="button primary" disabled={pending}>
          {dict.orgs.create}
        </button>
      </form>
    </div>
  );
}
