"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";

/** Borrado definitivo: hay que escribir el nombre de la organización para confirmar. */
export function DeleteOrganization({
  dict,
  lang,
  org,
}: {
  dict: Dictionary;
  lang: Locale;
  org: Schemas["OrganizationOut"];
}) {
  const router = useRouter();
  const { pending, error, run } = useAction(dict);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const matches = name.trim() === org.name.trim();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const result = await run(() =>
      api.DELETE("/v1/organizations/{org_id}", {
        params: { path: { org_id: org.id } },
        body: { confirm_name: name },
      }),
    );
    // 204 sin cuerpo: run devuelve null si ha ido bien y undefined si ha fallado.
    if (result !== undefined) router.push(`/${lang}/organizations`);
  }

  return (
    <section
      className="card stack danger-zone"
      aria-labelledby="delete-org-heading"
    >
      <h2 id="delete-org-heading">{dict.orgs.dangerZone}</h2>
      <p>{dict.orgs.deleteWarning}</p>
      {!open ? (
        <button
          type="button"
          className="button danger"
          onClick={() => setOpen(true)}
        >
          {dict.orgs.delete}
        </button>
      ) : (
        <form className="stack" onSubmit={submit}>
          <Field
            label={format(dict.orgs.deleteConfirmLabel, { name: org.name })}
            autoComplete="off"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <FormMessage error={error} />
          <div className="row">
            <button
              type="submit"
              className="button danger"
              disabled={pending || !matches}
            >
              {dict.orgs.deleteSubmit}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setOpen(false);
                setName("");
              }}
            >
              {dict.edit.cancel}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
