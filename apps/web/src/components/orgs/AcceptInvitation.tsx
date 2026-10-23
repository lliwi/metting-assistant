"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";

type Props = { dict: Dictionary; lang: Locale };

export function AcceptInvitation({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {() => <Accept dict={dict} lang={lang} />}
    </RequireAuth>
  );
}

function Accept({ dict, lang }: Props) {
  const token = useSearchParams().get("token");
  const { refresh } = useAuth();
  const { error, setError, run } = useAction(dict);
  const [org, setOrg] = useState<Schemas["OrganizationOut"] | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!token) {
      setError(dict.auth.missingToken);
      return;
    }
    void run(() =>
      api.POST("/v1/invitations/accept", { body: { token } }),
    ).then((data) => {
      if (data) {
        setOrg(data);
        void refresh();
      }
    });
  }, [token, run, setError, refresh, dict]);

  return (
    <div className="card form">
      <h1>{dict.orgs.acceptTitle}</h1>
      {!org && !error && <p role="status">{dict.orgs.accepting}</p>}
      <FormMessage
        error={error}
        success={org ? format(dict.orgs.accepted, { name: org.name }) : null}
      />
      {org && (
        <Link
          className="button primary"
          href={`/${lang}/organizations/${org.id}`}
        >
          {dict.orgs.open}
        </Link>
      )}
    </div>
  );
}
